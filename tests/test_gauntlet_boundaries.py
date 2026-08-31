"""Gate 13 acceptance preflight against real fleet execution boundaries.

These are adversarial acceptance tests, not mocked benchmark scores. They must
pass before a large-corpus comparison can substantiate the product's claims.
"""

from concurrent.futures import ThreadPoolExecutor, wait
from threading import Barrier, Event

import pytest

from tenure.domain import ActionProposal, AuthorityLevel, CapabilityGrant
from tenure.fleet import ProcureToPayFleet
from tenure.gateway import AgentGateway
from tenure.ledger import AppendOnlyLedger
from tenure.recovery import (
    FleetRecoveryOrchestrator,
    LocalFleetRecoveryReasoner,
    RecoveryScenario,
)


@pytest.mark.parametrize(
    ("amount", "reversible"),
    [(0, True), (-1, True), (18_400, False)],
    ids=["zero-payment", "negative-payment", "irreversible-payment"],
)
def test_bounded_payment_rejects_invalid_value_or_irreversible_effect(
    amount: int, reversible: bool
) -> None:
    ledger = AppendOnlyLedger()
    grant = CapabilityGrant(
        "treasury-agent",
        "payment.release",
        AuthorityLevel.EXECUTE_BOUNDED,
        amount_ceiling=50_000,
        allowed_vendors=frozenset({"vendor-a"}),
    )
    result = AgentGateway(ledger).authorize(
        grant,
        ActionProposal(
            "treasury-agent", "payment.release", amount, "vendor-a",
            "treasury-policy#5.4", reversible,
        ),
        controlling_policy="treasury-policy#5.4",
    )

    assert not result.allowed, (
        f"Bounded sandbox payment was authorized: amount={amount}, "
        f"reversible={reversible}, decision={result.decision.value}"
    )
    assert result.scope_token is None


def test_active_fleet_freeze_blocks_execution_before_supervisor_finishes() -> None:
    fleet = ProcureToPayFleet()
    attempted: dict[str, object] = {}

    class TrafficDuringInvestigation(LocalFleetRecoveryReasoner):
        def decide(self, incident, toolbox, guardrail):
            attempted["freeze_events_seen"] = len(
                fleet.ledger.find("FLEET_CAPABILITY_FROZEN")
            )
            # This is a fresh business case in the same tenant, not a replay of
            # the incident case. The compromised shared capability is frozen.
            try:
                result = fleet.run_case(
                    tenant_id=incident.tenant_id,
                    case_id="case-during-active-freeze",
                )
                attempted["executed"] = result["complete"]
            except PermissionError:
                attempted["executed"] = False
            return super().decide(incident, toolbox, guardrail)

    recovery = FleetRecoveryOrchestrator(fleet, reasoner=TrafficDuringInvestigation())
    recovery.run(
        tenant_id="tenant-gauntlet",
        case_id="case-upstream-incident",
        scenario=RecoveryScenario.UPSTREAM_COMPROMISE,
    )

    assert attempted["freeze_events_seen"] == 3
    assert attempted["executed"] is False, (
        "A new same-tenant procure-to-pay case completed while all three "
        "capabilities had active freeze events and the Supervisor was still running"
    )
    assert not fleet.ledger.find(
        "SANDBOX_MUTATION_COMMITTED", case_id="case-during-active-freeze"
    )


def test_case_replay_after_recovery_returns_current_compensated_state() -> None:
    fleet = ProcureToPayFleet()
    recovery = FleetRecoveryOrchestrator(fleet)
    recovered = recovery.run(
        tenant_id="tenant-gauntlet",
        case_id="case-recovered-replay",
        scenario=RecoveryScenario.UPSTREAM_COMPROMISE,
    )
    replay = fleet.run_case(
        tenant_id="tenant-gauntlet", case_id="case-recovered-replay"
    )

    assert replay["state"] == recovered["state_after"], (
        "Replay returned the cached pre-recovery state rather than the "
        "current suspended vendor, held invoice, and reversed sandbox payment"
    )


def test_100_concurrent_replays_do_not_restore_an_unfinished_case() -> None:
    opened = Event()
    release_owner = Event()
    start_replays = Barrier(101)

    class PausedOpeningLedger(AppendOnlyLedger):
        def append(self, event_type, payload):
            event = super().append(event_type, payload)
            if event_type == "FLEET_CASE_OPENED":
                opened.set()
                if not release_owner.wait(timeout=20):
                    raise TimeoutError("Gauntlet did not release the case owner")
            return event

    ledger = PausedOpeningLedger()
    fleet = ProcureToPayFleet(ledger=ledger)

    def replay():
        start_replays.wait(timeout=15)
        try:
            result = fleet.run_case(tenant_id="tenant-gauntlet", case_id="case-race")
            return "COMPLETE" if result["complete"] else "INCOMPLETE"
        except Exception as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=101) as pool:
        owner = pool.submit(
            fleet.run_case, tenant_id="tenant-gauntlet", case_id="case-race"
        )
        try:
            assert opened.wait(timeout=5), "Owner never reached the opening boundary"
            pending = [pool.submit(replay) for _ in range(100)]
            start_replays.wait(timeout=15)
            # Existing code returns immediately with unfinished-state errors.
            # Correct single-flight code may wait; give it a bounded window,
            # then release the owner so such an implementation can finish.
            wait(pending, timeout=2)
        finally:
            release_owner.set()
        outcomes = [future.result(timeout=20) for future in pending]
        original = owner.result(timeout=20)

    assert original["complete"] is True
    assert len(ledger.find("SANDBOX_MUTATION_COMMITTED")) == 3
    assert len(ledger.find("CAPABILITY_RECEIPT_ISSUED")) == 3
    assert ledger.verify_chain() is True
    failures = {outcome: outcomes.count(outcome) for outcome in set(outcomes)
                if outcome != "COMPLETE"}
    assert not failures, f"100 replay outcomes included unfinished-state failures: {failures}"
