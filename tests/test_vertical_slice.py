from __future__ import annotations

import unittest

from tenure.demo import run_demo
from tenure.domain import (
    ActionProposal,
    AuthorityLevel,
    CapabilityGrant,
    SupervisorDecision,
    VerificationResult,
)
from tenure.gateway import AgentGateway
from tenure.ledger import AppendOnlyLedger
from tenure.policy import TrustPolicyEngine


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = AppendOnlyLedger()
        self.policy = TrustPolicyEngine(self.ledger)
        self.grant = CapabilityGrant(
            "agent-1",
            "invoice.approve",
            level=AuthorityLevel.SHADOW,
            allowed_vendors=frozenset({"known"}),
        )

    def test_promotes_only_when_outcome_and_reasoning_pass(self) -> None:
        valid = VerificationResult(True, True, "policy#7.1", "policy#7.1")
        for _ in range(3):
            self.policy.record_verification(self.grant, valid)
        self.assertEqual(self.grant.level, AuthorityLevel.EXECUTE_BOUNDED)
        self.assertEqual(self.grant.amount_ceiling, 50_000)
        self.assertEqual(len(self.ledger.find("CAPABILITY_PROMOTED")), 1)

    def test_rawr_is_logged_and_does_not_trigger_promotion(self) -> None:
        rawr = VerificationResult(True, False, "policy#7.1", "policy#4.2")
        for _ in range(3):
            self.policy.record_verification(self.grant, rawr)
        self.assertEqual(self.grant.level, AuthorityLevel.SHADOW)
        self.assertEqual(len(self.ledger.find("RAWR_BLOCKED")), 3)

    def test_supervisor_cannot_expand_authority(self) -> None:
        decision = SupervisorDecision(
            incident_id="incident-1",
            target_level=AuthorityLevel.EXECUTE_FULL,
            affected_action_ids=(),
            rollback_action_ids=(),
            escalation_action_ids=(),
            narrative="invalid",
        )
        with self.assertRaises(PermissionError):
            self.policy.apply_supervisor_decision(
                self.grant, decision, previous_level=AuthorityLevel.SHADOW
            )


class GatewayTests(unittest.TestCase):
    def test_enforces_scope_outside_the_agent(self) -> None:
        ledger = AppendOnlyLedger()
        gateway = AgentGateway(ledger)
        grant = CapabilityGrant(
            "agent-1",
            "invoice.approve",
            AuthorityLevel.EXECUTE_BOUNDED,
            amount_ceiling=50_000,
            allowed_vendors=frozenset({"known"}),
        )
        allowed = ActionProposal(
            "agent-1", "invoice.approve", 49_000, "known", "policy#7.1", True
        )
        denied = ActionProposal(
            "agent-1", "invoice.approve", 1_000_000, "known", "attack", False
        )
        self.assertTrue(
            gateway.authorize(
                grant, allowed, controlling_policy="policy#7.1"
            ).allowed
        )
        self.assertFalse(
            gateway.authorize(
                grant, denied, controlling_policy="policy#7.1"
            ).allowed
        )

    def test_frozen_grant_denies_every_action(self) -> None:
        ledger = AppendOnlyLedger()
        gateway = AgentGateway(ledger)
        grant = CapabilityGrant(
            "agent-1",
            "invoice.approve",
            AuthorityLevel.EXECUTE_FULL,
            frozen=True,
        )
        action = ActionProposal(
            "agent-1", "invoice.approve", 1, "known", "policy", True
        )
        result = gateway.authorize(grant, action, controlling_policy="policy")
        self.assertEqual(result.decision.value, "DENY_FROZEN")


class LedgerTests(unittest.TestCase):
    def test_hash_chain_verifies(self) -> None:
        ledger = AppendOnlyLedger()
        ledger.append("ONE", {"value": 1})
        ledger.append("TWO", {"value": 2})
        self.assertTrue(ledger.verify_chain())


class EndToEndTests(unittest.TestCase):
    def test_demo_proves_the_vertical_slice(self) -> None:
        result = run_demo()
        supervisor = result["supervisor"]
        self.assertEqual(result["promoted_level"], "EXECUTE_BOUNDED")
        self.assertEqual(result["rawr_blocks"], 1)
        self.assertEqual(result["safe_action"], "ALLOW")
        self.assertEqual(result["attack_action"], "DENY_SCOPE")
        self.assertEqual(result["during_containment"], "DENY_FROZEN")
        self.assertEqual(supervisor["affected"], 6)
        self.assertEqual(supervisor["rolled_back"], 4)
        self.assertEqual(supervisor["escalated"], 2)
        self.assertTrue(result["ledger_chain_valid"])


if __name__ == "__main__":
    unittest.main()

