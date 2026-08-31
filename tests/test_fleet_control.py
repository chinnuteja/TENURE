"""Execution-boundary contracts. Firestore tests use an explicit transaction fake,
not a live-cloud claim: atomic buffered writes, read ordering, and conflict retry.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import RLock

import pytest
from fastapi.testclient import TestClient

from tenure.api import create_app
from tenure.cloud_adapters import FirestoreProcureToPaySandbox
from tenure.domain import AuthorityLevel
from tenure.fleet import ProcureToPayFleet, ProcureToPaySandbox
from tenure.fleet_control import OPERATING_KEYS, CaseConflict, CaseInProgress, FleetControl
from tenure.ledger import AppendOnlyLedger
from tenure.recovery import FleetRecoveryOrchestrator, LocalFleetRecoveryReasoner, RecoveryScenario


class Snapshot:
    def __init__(self, value):
        self.value = deepcopy(value)
        self.exists = value is not None

    def to_dict(self):
        return deepcopy(self.value)


class Document:
    def __init__(self, client, key):
        self.client, self.key = client, key

    def get(self, transaction=None):
        with self.client.lock:
            if transaction:
                assert not transaction.writes, "Firestore forbids reads after writes"
                transaction.reads[self.key] = self.client.versions.get(self.key, 0)
            return Snapshot(self.client.documents.get(self.key))

    def set(self, value):
        with self.client.lock:
            self.client.documents[self.key] = deepcopy(value)
            self.client.versions[self.key] = self.client.versions.get(self.key, 0) + 1

    def update(self, value):
        with self.client.lock:
            current = self.get().to_dict()
            current.update(value)
            self.set(current)


class Collection:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def document(self, key):
        return Document(self.client, f"{self.name}/{key}")


class Transaction:
    def __init__(self):
        self.reads, self.writes = {}, []

    def set(self, ref, value):
        self.writes.append((ref, deepcopy(value)))


class TransactionalClient:
    def __init__(self):
        self.lock = RLock()
        self.documents, self.versions = {}, {}
        self.before_commit = None
        self.retries = 0

    def collection(self, name):
        return Collection(self, name)

    @staticmethod
    def run(client, callback):
        for _ in range(100):
            transaction = Transaction()
            result = callback(transaction)
            if client.before_commit:
                client.before_commit(transaction)
            with client.lock:
                if any(client.versions.get(key, 0) != version
                       for key, version in transaction.reads.items()):
                    client.retries += 1
                    continue
                for ref, value in transaction.writes:
                    ref.set(value)
                return result
        raise RuntimeError("fake transaction exceeded retry bound")


@pytest.fixture(params=["memory", "firestore-contract"])
def sandbox_factory(request):
    if request.param == "memory":
        sandbox = ProcureToPaySandbox()
        return lambda: sandbox
    client = TransactionalClient()
    return lambda: FirestoreProcureToPaySandbox(client, transaction_runner=client.run)


def recover(fleet, case_id="incident"):
    return FleetRecoveryOrchestrator(fleet).run(
        tenant_id="a", case_id=case_id, scenario=RecoveryScenario.UPSTREAM_COMPROMISE,
    )


def test_demotion_survives_reconstruction_and_other_tenant_can_work(sandbox_factory):
    first = ProcureToPayFleet(sandbox=sandbox_factory())
    recovered = recover(first)
    rebuilt = ProcureToPayFleet(ledger=first.ledger, sandbox=sandbox_factory())
    assert rebuilt.run_case(tenant_id="a", case_id="incident")["state"] == recovered["state_after"]
    with pytest.raises(PermissionError, match="demoted"):
        rebuilt.run_case(tenant_id="a", case_id="fresh")
    assert not first.ledger.find("SANDBOX_MUTATION_COMMITTED", case_id="fresh")
    assert all(entry["level"] == "OBSERVE" for entry in rebuilt.control.snapshot("a").values())
    assert rebuilt.run_case(tenant_id="b", case_id="fresh")["complete"]


def test_failed_supervisor_keeps_freeze_across_reconstruction(sandbox_factory):
    fleet = ProcureToPayFleet(sandbox=sandbox_factory())

    class BrokenSupervisor(LocalFleetRecoveryReasoner):
        def decide(self, *args):
            raise RuntimeError("model unavailable")

    with pytest.raises(RuntimeError, match="model unavailable"):
        FleetRecoveryOrchestrator(fleet, reasoner=BrokenSupervisor()).run(
            tenant_id="a", case_id="incident", scenario=RecoveryScenario.UPSTREAM_COMPROMISE,
        )
    rebuilt = ProcureToPayFleet(ledger=fleet.ledger, sandbox=sandbox_factory())
    assert all(entry["freezes"] for entry in rebuilt.control.snapshot("a").values())
    with pytest.raises(PermissionError, match="frozen"):
        rebuilt.run_case(tenant_id="a", case_id="fresh")


def test_failed_freeze_audit_cannot_leave_authority_live(sandbox_factory):
    class FreezeAuditFailure(AppendOnlyLedger):
        def append(self, event_type, payload):
            if event_type == "FLEET_CAPABILITY_FROZEN":
                raise RuntimeError("audit unavailable after durable freeze")
            return super().append(event_type, payload)

    fleet = ProcureToPayFleet(ledger=FreezeAuditFailure(), sandbox=sandbox_factory())
    with pytest.raises(RuntimeError, match="audit unavailable"):
        recover(fleet)
    rebuilt = ProcureToPayFleet(ledger=fleet.ledger, sandbox=sandbox_factory())
    assert all(entry["freezes"] for entry in rebuilt.control.snapshot("a").values())
    with pytest.raises(PermissionError, match="frozen"):
        rebuilt.run_case(tenant_id="a", case_id="after-audit-failure")


def test_failed_rollback_preserves_demotion_and_containment(sandbox_factory, monkeypatch):
    fleet = ProcureToPayFleet(sandbox=sandbox_factory())

    def unavailable(*args):
        raise RuntimeError("compensation unavailable")

    monkeypatch.setattr(fleet.sandbox, "rollback_entity", unavailable)
    with pytest.raises(RuntimeError, match="compensation unavailable"):
        recover(fleet)
    state = fleet.control.snapshot("a")
    assert all(entry["freezes"] and entry["level"] == "OBSERVE" for entry in state.values())
    assert not fleet.ledger.find("FLEET_RECOVERY_COMPLETED")


def test_raw_sandbox_payment_validation_is_independent_of_gateway(sandbox_factory):
    sandbox = sandbox_factory()
    sandbox.seed_case(
        tenant_id="a", vendor_id="v", po_id="po", invoice_id="i", amount=0,
    )
    sandbox.onboard_vendor("a", "v")
    sandbox.approve_invoice("a", "i")
    with pytest.raises(ValueError, match="positive integer"):
        sandbox.release_payment("a", "p", "i")
    with pytest.raises(KeyError):
        sandbox.payment_snapshot("a", "p")


def test_overlapping_incidents_cannot_clear_each_other_or_raise_authority(sandbox_factory):
    fleet = ProcureToPayFleet(sandbox=sandbox_factory())
    fleet.run_case(tenant_id="a", case_id="start")
    control = fleet.control
    control.freeze("a", OPERATING_KEYS, "one")
    control.freeze("a", OPERATING_KEYS, "two")
    control.demote("a", OPERATING_KEYS, "one", AuthorityLevel.OBSERVE)
    control.finish_recovery("a", OPERATING_KEYS, "one")
    assert all(entry["freezes"] == ["two"] for entry in control.snapshot("a").values())
    control.demote("a", OPERATING_KEYS, "two", AuthorityLevel.SHADOW)
    control.finish_recovery("a", OPERATING_KEYS, "two")
    assert all(entry["level"] == "OBSERVE" for entry in control.snapshot("a").values())
    with pytest.raises(PermissionError):
        control.initialize("a", fleet._earn_operating_grants("new-vendor"))


def test_freeze_between_gateway_and_mutation_prevents_side_effect(sandbox_factory, monkeypatch):
    fleet = ProcureToPayFleet(sandbox=sandbox_factory())
    original = fleet.sandbox.execute

    def freeze_then_execute(tenant_id, key, operation, *args):
        if operation == "release_payment":
            fleet.control.freeze(tenant_id, (key,), "racing-freeze")
        return original(tenant_id, key, operation, *args)

    monkeypatch.setattr(fleet.sandbox, "execute", freeze_then_execute)
    with pytest.raises(PermissionError):
        fleet.run_case(tenant_id="a", case_id="race")
    assert len(fleet.ledger.find("SANDBOX_MUTATION_COMMITTED")) == 2
    with pytest.raises(KeyError):
        fleet.sandbox.payment_snapshot("a", "payment-race")


def test_firestore_conflict_retry_rechecks_freeze_before_payment_commit():
    client = TransactionalClient()
    sandbox = FirestoreProcureToPaySandbox(client, transaction_runner=client.run)
    fleet = ProcureToPayFleet(sandbox=sandbox)

    def racing_freeze(transaction):
        if any("_payments/" in ref.key for ref, _ in transaction.writes):
            client.before_commit = None
            sandbox.control.freeze("a", OPERATING_KEYS, "concurrent-freeze")

    client.before_commit = racing_freeze
    with pytest.raises(PermissionError):
        fleet.run_case(tenant_id="a", case_id="race")
    assert client.retries == 1
    with pytest.raises(KeyError):
        sandbox.payment_snapshot("a", "payment-race")
    assert len(fleet.ledger.find("CAPABILITY_RECEIPT_ISSUED")) == 2


def test_partial_failure_cannot_reexecute_after_reconstruction(sandbox_factory):
    class ReceiptFailureLedger(AppendOnlyLedger):
        def append(self, event_type, payload):
            if event_type == "CAPABILITY_RECEIPT_ISSUED":
                raise RuntimeError("receipt storage unavailable")
            return super().append(event_type, payload)

    ledger = ReceiptFailureLedger()
    fleet = ProcureToPayFleet(ledger=ledger, sandbox=sandbox_factory())
    with pytest.raises(RuntimeError, match="receipt storage unavailable"):
        fleet.run_case(tenant_id="a", case_id="partial")
    before = tuple(ledger.events)
    rebuilt = ProcureToPayFleet(ledger=ledger, sandbox=sandbox_factory())
    with pytest.raises(CaseConflict, match="reconcile"):
        rebuilt.run_case(tenant_id="a", case_id="partial")
    assert ledger.events == before
    assert rebuilt.sandbox.vendor_snapshot("a", "vendor-partial")["status"] == "ONBOARDED"


def test_crashed_owner_stays_owned_and_wrong_owner_cannot_finish():
    control = FleetControl(replay_wait_seconds=0)
    assert control.claim("a", "case", 10, "first") == "OWNED"
    with pytest.raises(CaseInProgress):
        control.claim("a", "case", 10, "second")
    with pytest.raises(CaseConflict, match="active owner"):
        control.finish_case("a", "case", "second")


def test_unknown_persisted_case_status_does_not_create_a_new_owner():
    control = FleetControl()
    control.store.transact(
        "cases", "a\0case",
        lambda state, _: state.update(amount=10, owner="old", status="UNKNOWN"),
    )
    with pytest.raises(CaseConflict, match="unrecognized"):
        control.claim("a", "case", 10, "new")


def test_case_input_conflict_is_rejected_without_mutation(sandbox_factory):
    fleet = ProcureToPayFleet(sandbox=sandbox_factory())
    fleet.run_case(tenant_id="a", case_id="once", amount=100)
    before = fleet.ledger.events
    with pytest.raises(CaseConflict, match="different input"):
        fleet.run_case(tenant_id="a", case_id="once", amount=101)
    assert fleet.ledger.events == before


def test_concurrent_rebuilt_instances_share_one_owner(sandbox_factory):
    ledger = AppendOnlyLedger()

    def run(_):
        fleet = ProcureToPayFleet(ledger=ledger, sandbox=sandbox_factory())
        return fleet.run_case(tenant_id="a", case_id="shared")

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(run, range(100)))
    assert all(result["complete"] for result in results)
    assert len(ledger.find("SANDBOX_MUTATION_COMMITTED")) == 3
    assert len(ledger.find("CAPABILITY_RECEIPT_ISSUED")) == 3
    assert ledger.verify_chain()


def test_legacy_log_only_freeze_is_not_forgotten():
    ledger = AppendOnlyLedger()
    ledger.append("FLEET_CAPABILITY_FROZEN", {
        "tenant_id": "a", "capability_key": OPERATING_KEYS[0], "incident_id": "legacy",
    })
    fleet = ProcureToPayFleet(ledger=ledger)
    with pytest.raises(PermissionError, match="frozen"):
        fleet.run_case(tenant_id="a", case_id="new")
    assert not ledger.find("SANDBOX_MUTATION_COMMITTED")


def test_worker_cannot_substitute_unfrozen_capability(sandbox_factory):
    fleet = ProcureToPayFleet(sandbox=sandbox_factory())
    fleet.run_case(tenant_id="a", case_id="initial")
    fleet.control.freeze("a", (OPERATING_KEYS[2],), "payment-freeze")
    with pytest.raises(PermissionError, match="does not match"):
        fleet.sandbox.execute(
            "a", OPERATING_KEYS[0], "release_payment", "payment-2", "invoice-initial",
        )


@pytest.mark.parametrize("amount", [0, -1, True, 1.5])
def test_invalid_case_value_fails_before_any_work(amount):
    fleet = ProcureToPayFleet()
    with pytest.raises(ValueError):
        fleet.run_case(tenant_id="a", case_id="invalid", amount=amount)
    assert not fleet.ledger.events


def test_payment_cannot_release_again_after_compensation(sandbox_factory):
    fleet = ProcureToPayFleet(sandbox=sandbox_factory())
    fleet.run_case(tenant_id="a", case_id="paid")
    fleet.sandbox.rollback_entity("a", "payment", "payment-paid")
    with pytest.raises(PermissionError, match="compensated"):
        fleet.sandbox.release_payment("a", "payment-paid", "invoice-paid")


def test_api_reports_frozen_and_in_progress_cases_deliberately():
    app = create_app()
    client = TestClient(app)
    fleet = app.state.fleet
    fleet.control.freeze("blocked", OPERATING_KEYS, "api-freeze")
    assert client.post("/api/fleet/cases/new?tenant_id=blocked").status_code == 403
    fleet.control.replay_wait_seconds = 0
    fleet.control.claim("busy", "owned", 18_400, "other-request")
    response = client.post("/api/fleet/cases/owned?tenant_id=busy")
    assert response.status_code == 409
    assert response.headers["Retry-After"] == "2"


def test_parallel_ledger_appends_preserve_sequence_and_payload_immutability():
    ledger = AppendOnlyLedger()
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda n: ledger.append("PARALLEL", {"n": n}), range(100)))
    assert [event.sequence for event in ledger.events] == list(range(1, 101))
    assert ledger.verify_chain()
    ledger.events[0].payload["n"] = "tampered snapshot"
    assert ledger.verify_chain()
