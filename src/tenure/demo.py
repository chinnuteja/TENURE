"""Executable TENURE vertical slice."""

from __future__ import annotations

import json

from tenure.domain import ActionProposal, AuthorityLevel, CapabilityGrant, VerificationResult
from tenure.gateway import AgentGateway
from tenure.ledger import AppendOnlyLedger
from tenure.policy import TrustPolicyEngine
from tenure.supervisor import SupervisorAgent


def run_demo() -> dict[str, object]:
    ledger = AppendOnlyLedger()
    policy = TrustPolicyEngine(ledger)
    gateway = AgentGateway(ledger)
    supervisor = SupervisorAgent(ledger)
    grant = CapabilityGrant(
        agent_id="accounts-payable-agent",
        capability="invoice.approve",
        level=AuthorityLevel.SHADOW,
        allowed_vendors=frozenset({"vendor-alpha", "vendor-beta"}),
    )

    correct = VerificationResult(True, True, "vendor-policy#7.1", "vendor-policy#7.1")
    for _ in range(3):
        policy.record_verification(grant, correct)
    promoted_level = grant.level.name

    rawr = VerificationResult(True, False, "vendor-policy#7.1", "vendor-policy#4.2")
    policy.record_verification(grant, rawr)

    safe_action = ActionProposal(
        grant.agent_id,
        grant.capability,
        40_000,
        "vendor-alpha",
        "vendor-policy#7.1",
        True,
    )
    safe_result = gateway.authorize(
        grant, safe_action, controlling_policy="vendor-policy#7.1"
    )
    attack = ActionProposal(
        grant.agent_id,
        grant.capability,
        1_000_000,
        "vendor-alpha",
        "injected-instruction",
        False,
    )
    attack_result = gateway.authorize(
        grant, attack, controlling_policy="vendor-policy#7.1"
    )

    history = [
        safe_action,
        ActionProposal(
            grant.agent_id,
            grant.capability,
            11_000,
            "vendor-alpha",
            "vendor-policy#7.1",
            True,
        ),
        ActionProposal(
            grant.agent_id,
            grant.capability,
            12_000,
            "vendor-alpha",
            "vendor-policy#7.1",
            True,
        ),
        ActionProposal(
            grant.agent_id,
            grant.capability,
            13_000,
            "vendor-beta",
            "vendor-policy#7.1",
            True,
        ),
        ActionProposal(
            grant.agent_id,
            grant.capability,
            29_000,
            "vendor-beta",
            "vendor-policy#7.1",
            False,
        ),
        ActionProposal(
            grant.agent_id,
            grant.capability,
            31_000,
            "vendor-alpha",
            "vendor-policy#7.1",
            False,
        ),
    ]
    failed_action = history[-1]
    incident = policy.contain(
        grant,
        failed_action_id=failed_action.action_id,
        controlling_policy="vendor-policy#7.1",
        reason="outcome incorrect for a high-value invoice",
        trace_id="trace-demo-001",
    )
    frozen_result = gateway.authorize(
        grant, safe_action, controlling_policy="vendor-policy#7.1"
    )
    decision = supervisor.investigate(incident, history)
    policy.apply_supervisor_decision(
        grant, decision, previous_level=incident.previous_level
    )

    return {
        "promoted_level": promoted_level,
        "rawr_blocks": len(ledger.find("RAWR_BLOCKED")),
        "safe_action": safe_result.decision.value,
        "attack_action": attack_result.decision.value,
        "during_containment": frozen_result.decision.value,
        "supervisor": {
            "target_level": decision.target_level.name,
            "affected": len(decision.affected_action_ids),
            "rolled_back": len(decision.rollback_action_ids),
            "escalated": len(decision.escalation_action_ids),
            "narrative": decision.narrative,
        },
        "final_grant": grant.snapshot(),
        "ledger_events": len(ledger.events),
        "ledger_chain_valid": ledger.verify_chain(),
        "receipt": safe_result.receipt.snapshot(),
    }


def main() -> None:
    print(json.dumps(run_demo(), indent=2, default=str))


if __name__ == "__main__":
    main()
