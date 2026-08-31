"""Small live Firestore adapter probe; no deploy, model calls, or production data.

Leaves uniquely named synthetic evidence for audit. This does NOT prove that the
deployed Cloud Run revision contains the repair or that Gemini recovery ran.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tenure.cloud_adapters import FirestoreLedger, FirestoreProcureToPaySandbox
from tenure.fleet import ProcureToPayFleet
from tenure.fleet_control import CaseConflict, CaseInProgress, FirestoreAtomicStore
from tenure.recovery import FleetRecoveryOrchestrator, LocalFleetRecoveryReasoner, RecoveryScenario

PROJECT = "project-ceca895d-33b0-44b9-b5a"


def verify(project: str) -> dict:
    import google.auth
    from google.cloud import firestore

    if project != PROJECT:
        raise ValueError("this probe is restricted to the participant-approved project")
    credentials, detected_project = google.auth.default()
    if detected_project not in (None, project):
        raise RuntimeError("ADC project differs from the approved project; no writes performed")
    run_id = uuid4().hex[:12]
    prefix = f"tenure_repair_probe_{run_id}"
    print(f"Starting isolated Firestore probe: {prefix}; zero model calls", flush=True)
    client = firestore.Client(project=project, database="tenure", credentials=credentials)
    transaction_count = 0

    def bounded_transaction(client, callback):
        nonlocal transaction_count
        transaction_count += 1
        if transaction_count > 100:
            raise RuntimeError("probe transaction-call budget exhausted")
        return FirestoreAtomicStore._run_transaction(client, callback)

    def make_fleet():
        return ProcureToPayFleet(
            ledger=FirestoreLedger(f"{prefix}_ledger", client),
            sandbox=FirestoreProcureToPaySandbox(
                client, collection_prefix=prefix, transaction_runner=bounded_transaction,
            ),
        )

    fleet = make_fleet()
    first = fleet.run_case(tenant_id="probe-a", case_id="original")
    assert first["complete"] and first["ledger_integrity"]
    print("Initial transaction-backed case completed", flush=True)
    frozen_attempt_blocked = False

    class ProbeReasoner(LocalFleetRecoveryReasoner):
        def decide(self, incident, toolbox, guardrail):
            nonlocal frozen_attempt_blocked
            try:
                make_fleet().run_case(tenant_id="probe-a", case_id="during-freeze")
            except PermissionError:
                frozen_attempt_blocked = True
            if not frozen_attempt_blocked:
                raise AssertionError("fresh reconstructed fleet bypassed active freeze")
            return super().decide(incident, toolbox, guardrail)

    recovered = FleetRecoveryOrchestrator(fleet, reasoner=ProbeReasoner()).run(
        tenant_id="probe-a", case_id="original", scenario=RecoveryScenario.UPSTREAM_COMPROMISE,
    )
    rebuilt = make_fleet()
    replay = rebuilt.run_case(tenant_id="probe-a", case_id="original")
    assert replay["state"] == recovered["state_after"]
    try:
        rebuilt.run_case(tenant_id="probe-a", case_id="after-recovery")
    except PermissionError:
        pass
    else:
        raise AssertionError("reconstruction lost the demotion")
    print("Freeze, demotion, compensation, and reconstructed replay verified", flush=True)
    try:
        rebuilt.run_case(tenant_id="probe-a", case_id="original", amount=18_401)
    except CaseConflict:
        pass
    else:
        raise AssertionError("idempotency input mismatch was accepted")
    assert rebuilt.run_case(tenant_id="probe-b", case_id="unrelated")["complete"]

    rebuilt.control.claim("probe-b", "owned", 100, "owner")
    other = make_fleet()
    other.control.replay_wait_seconds = 0
    try:
        other.control.claim("probe-b", "owned", 100, "not-owner")
    except CaseInProgress:
        pass
    else:
        raise AssertionError("a reconstructed control bypassed the owner")
    rebuilt.control.finish_case("probe-b", "owned", "owner", failed=True)
    audit = rebuilt.audit_case("probe-a", "original")
    assert audit["receipt_count"] == 3 and audit["ledger_integrity"]
    assert not rebuilt.ledger.find("SANDBOX_MUTATION_COMMITTED", case_id="during-freeze")
    assert not rebuilt.ledger.find("SANDBOX_MUTATION_COMMITTED", case_id="after-recovery")
    return {
        "verified_at": datetime.now(UTC).isoformat(),
        "project_id": project, "database": "tenure", "collection_prefix": prefix,
        "proof_mode": "LOCAL_CODE_LIVE_FIRESTORE", "deployed_revision_verified": False,
        "model_calls": 0, "supervisor_mode": recovered["reasoner_mode"],
        "transaction_calls": transaction_count, "transaction_call_limit": 100,
        "actual_cost": "not measured; Firestore usage only, no model calls or deployment",
        "checks": {
            "freeze_blocks_reconstructed_worker": frozen_attempt_blocked,
            "demotion_survives_reconstruction": True,
            "replay_returns_compensated_state": True,
            "input_conflict_rejected": True, "other_tenant_unaffected": True,
            "active_owner_not_stolen": True, "exactly_three_original_receipts": True,
            "ledger_integrity": audit["ledger_integrity"],
        },
        "incident_id": recovered["incident"]["incident_id"],
        "state_after": recovered["state_after"],
        "authority_after": recovered["authority_after"],
        "limitations": [
            "Not a deployed Cloud Run canary or Gemini execution",
            "No live 100-way concurrency test; that remains an offline contract",
            "Business mutation and audit receipt are separate commits",
            "Abandoned owners require explicit reconciliation",
            "Synthetic probe collections retained for audit",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", choices=[PROJECT], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = verify(args.project)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2), flush=True)


if __name__ == "__main__":
    main()
