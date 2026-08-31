"""Authoritative fleet lifecycle and single-owner case execution.

The store's transaction also encloses the sandbox mutation. Model code receives
neither this port nor its credentials. Abandoned owners fail closed: there is no
lease expiry that could let a slow or crashed writer execute twice.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from copy import deepcopy
from threading import RLock
from typing import Any

from tenure.domain import AuthorityLevel, CapabilityGrant

OPERATING_KEYS = (
    "vendor-intelligence-agent:vendor.onboard",
    "invoice-agent:invoice.approve",
    "treasury-agent:payment.release",
)
MUTATION_KEYS = dict(zip(
    ("onboard_vendor", "approve_invoice", "release_payment"), OPERATING_KEYS, strict=True,
))


class CaseConflict(RuntimeError):
    """Conflicting input, incomplete failure, or an operation needing review."""


class CaseInProgress(CaseConflict):
    """Another durable owner still holds this case; retry the same request later."""


class MemoryAtomicStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._documents: dict[tuple[str, str], dict] = {}

    def transact(self, namespace: str, key: str, callback: Callable) -> Any:
        with self._lock:
            document = deepcopy(self._documents.get((namespace, key), {}))
            result = callback(document, None)
            self._documents[(namespace, key)] = deepcopy(document)
            return result


class FirestoreAtomicStore:
    def __init__(self, client: Any, prefix: str, transaction_runner=None) -> None:
        self.client = client
        self.prefix = prefix
        self.transaction_runner = transaction_runner or self._run_transaction

    @staticmethod
    def _run_transaction(client, callback):
        from google.cloud import firestore

        return firestore.transactional(callback)(client.transaction())

    def transact(self, namespace: str, key: str, callback: Callable) -> Any:
        document_id = hashlib.sha256(key.encode()).hexdigest()
        ref = self.client.collection(f"{self.prefix}_{namespace}").document(document_id)

        def run(transaction):
            snapshot = ref.get(transaction=transaction)
            document = snapshot.to_dict() if snapshot.exists else {}
            previous = deepcopy(document)
            result = callback(document, transaction)
            # All reads in callback precede writes; retries have no external
            # effects (no ledger append, Gemini call, or network request here).
            if document != previous:
                transaction.set(ref, document)
            return result

        return self.transaction_runner(self.client, run)


class FleetControl:
    def __init__(self, store=None, *, replay_wait_seconds: float = 5.0) -> None:
        self.store = store or MemoryAtomicStore()
        self.replay_wait_seconds = replay_wait_seconds

    def snapshot(self, tenant_id: str) -> dict:
        return self.store.transact("authority", tenant_id, lambda state, _: deepcopy(state))

    def import_legacy_restrictions(self, tenant_id: str, events) -> None:
        """Old log-only freezes must not disappear on deployment of this store.

        Keep legacy containment closed even if old code logged completion. It
        never enforced a durable lifecycle, so restoration needs reconciliation.
        """
        def restore(state, _):
            if state:
                return
            for event in events:
                key = event.payload.get("capability_key")
                if key not in OPERATING_KEYS:
                    continue
                entry = state.setdefault(key, {
                    "level": "OBSERVE", "amount_ceiling": 0, "freezes": [], "version": 1,
                })
                incident_id = event.payload["incident_id"]
                if incident_id not in entry["freezes"]:
                    entry["freezes"].append(incident_id)
        self.store.transact("authority", tenant_id, restore)

    @staticmethod
    def _require(state: dict, key: str, *, missing_allowed: bool = False) -> None:
        entry = state.get(key)
        if entry is None and missing_allowed:
            return
        if entry is None or entry.get("freezes"):
            raise PermissionError(f"authority absent or frozen: {key}")
        if AuthorityLevel[entry["level"]] < AuthorityLevel.EXECUTE_BOUNDED:
            raise PermissionError(f"authority demoted: {key}")

    def preflight(self, tenant_id: str) -> None:
        def check(state, _):
            for key in OPERATING_KEYS:
                self._require(state, key, missing_allowed=True)
        self.store.transact("authority", tenant_id, check)

    def initialize(self, tenant_id: str, grants: dict[str, CapabilityGrant]) -> None:
        """Accept initial deterministic proof, never overwrite an existing grant."""
        def initialize(state, _):
            for grant in grants.values():
                key = f"{grant.agent_id}:{grant.capability}"
                state.setdefault(key, {
                    "level": grant.level.name,
                    "amount_ceiling": grant.amount_ceiling,
                    "freezes": [],
                    "version": 1,
                })
                self._require(state, key)
        self.store.transact("authority", tenant_id, initialize)

    def constrain(self, tenant_id: str, grant: CapabilityGrant) -> None:
        entry = self.snapshot(tenant_id).get(f"{grant.agent_id}:{grant.capability}")
        if entry is None:
            grant.level = AuthorityLevel.OBSERVE
            return
        grant.level = min(grant.level, AuthorityLevel[entry["level"]])
        grant.frozen = bool(entry["freezes"])
        grant.version = entry["version"]
        if entry["amount_ceiling"] is not None:
            grant.amount_ceiling = min(grant.amount_ceiling or 0, entry["amount_ceiling"])

    def guard(self, tenant_id: str, key: str, mutation: Callable) -> Any:
        def guarded(state, transaction):
            self._require(state, key)
            return mutation(transaction)
        return self.store.transact("authority", tenant_id, guarded)

    def freeze(self, tenant_id: str, keys, incident_id: str) -> None:
        def freeze(state, _):
            for key in keys:
                entry = state.setdefault(key, {
                    "level": "OBSERVE", "amount_ceiling": 0, "freezes": [], "version": 0,
                })
                if incident_id not in entry["freezes"]:
                    entry["freezes"].append(incident_id)
                    entry["version"] += 1
        self.store.transact("authority", tenant_id, freeze)

    def demote(self, tenant_id: str, keys, incident_id: str, target: AuthorityLevel) -> None:
        def demote(state, _):
            for key in keys:
                entry = state[key]
                if incident_id not in entry["freezes"]:
                    raise PermissionError("demotion requires this incident's active freeze")
                entry["level"] = min(AuthorityLevel[entry["level"]], target).name
                entry["version"] += 1
        self.store.transact("authority", tenant_id, demote)

    def finish_recovery(self, tenant_id: str, keys, incident_id: str) -> None:
        def finish(state, _):
            for key in keys:
                entry = state[key]
                if AuthorityLevel[entry["level"]] >= AuthorityLevel.EXECUTE_BOUNDED:
                    raise PermissionError("cannot release containment before demotion")
                entry["freezes"] = [item for item in entry["freezes"] if item != incident_id]
                entry["version"] += 1
        self.store.transact("authority", tenant_id, finish)

    def claim(self, tenant_id: str, case_id: str, amount: int, owner: str) -> str:
        deadline = time.monotonic() + self.replay_wait_seconds
        key = f"{tenant_id}\0{case_id}"

        def claim(state, _):
            if not state:
                state.update(amount=amount, owner=owner, status="IN_PROGRESS")
                return "OWNED"
            if state["amount"] != amount:
                raise CaseConflict("case id already belongs to different input")
            if state.get("status") not in {"IN_PROGRESS", "COMPLETE", "FAILED"}:
                raise CaseConflict("unrecognized case state; reconciliation required")
            if state["status"] == "FAILED":
                raise CaseConflict("case failed; reconcile partial effects before retry")
            return state["status"]

        while True:
            status = self.store.transact("cases", key, claim)
            if status != "IN_PROGRESS":
                return status
            if time.monotonic() >= deadline:
                raise CaseInProgress("case is in progress; retry the same case later")
            time.sleep(0.05)

    def finish_case(self, tenant_id: str, case_id: str, owner: str, *, failed=False) -> None:
        def finish(state, _):
            if state.get("owner") != owner or state.get("status") != "IN_PROGRESS":
                raise CaseConflict("case completion requires the active owner")
            state["status"] = "FAILED" if failed else "COMPLETE"
        self.store.transact("cases", f"{tenant_id}\0{case_id}", finish)
