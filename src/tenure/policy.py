"""Deterministic promotion, containment, and demotion policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from tenure.domain import (
    AuthorityLevel,
    CapabilityGrant,
    IncidentEnvelope,
    SupervisorDecision,
    VerificationResult,
    new_id,
)
from tenure.ledger import TrustLedger


@dataclass(frozen=True, slots=True)
class PromotionRule:
    minimum_tasks: int
    minimum_outcome_rate: float
    minimum_reasoning_rate: float
    next_level: AuthorityLevel
    amount_ceiling: int | None = None


DEFAULT_RULES = {
    AuthorityLevel.OBSERVE: PromotionRule(2, 1.0, 1.0, AuthorityLevel.SHADOW),
    AuthorityLevel.SHADOW: PromotionRule(
        3, 0.95, 0.95, AuthorityLevel.EXECUTE_BOUNDED, 50_000
    ),
    AuthorityLevel.EXECUTE_BOUNDED: PromotionRule(
        10, 0.98, 0.98, AuthorityLevel.EXECUTE_FULL
    ),
}


class TrustPolicyEngine:
    def __init__(
        self,
        ledger: TrustLedger,
        rules: dict[AuthorityLevel, PromotionRule] | None = None,
    ) -> None:
        self.ledger = ledger
        self.rules = rules or DEFAULT_RULES

    def record_verification(
        self, grant: CapabilityGrant, result: VerificationResult
    ) -> AuthorityLevel:
        if grant.frozen:
            raise ValueError("frozen grants cannot accumulate promotion evidence")

        grant.verified_tasks += 1
        grant.outcome_correct += int(result.outcome_correct)
        grant.reasoning_valid += int(result.reasoning_valid)
        grant.version += 1
        event_type = "RAWR_BLOCKED" if result.rawr else "VERIFICATION_RECORDED"
        self.ledger.append(
            event_type,
            {
                "agent_id": grant.agent_id,
                "capability": grant.capability,
                "evidence_id": result.evidence_id,
                "outcome_correct": result.outcome_correct,
                "reasoning_valid": result.reasoning_valid,
                "controlling_policy": result.controlling_policy,
                "cited_policy": result.cited_policy,
            },
        )
        self._maybe_promote(grant)
        return grant.level

    def contain(
        self,
        grant: CapabilityGrant,
        *,
        failed_action_id: str,
        controlling_policy: str,
        reason: str,
        trace_id: str,
    ) -> IncidentEnvelope:
        previous_level = grant.level
        grant.frozen = True
        grant.version += 1
        incident = IncidentEnvelope(
            incident_id=new_id("incident"),
            agent_id=grant.agent_id,
            capability=grant.capability,
            failed_action_id=failed_action_id,
            previous_level=previous_level,
            controlling_policy=controlling_policy,
            reason=reason,
            trace_id=trace_id,
        )
        self.ledger.append(
            "CAPABILITY_FROZEN",
            {
                "incident_id": incident.incident_id,
                "agent_id": grant.agent_id,
                "capability": grant.capability,
                "previous_level": previous_level.name,
                "failed_action_id": failed_action_id,
                "reason": reason,
                "trace_id": trace_id,
            },
        )
        return incident

    def apply_supervisor_decision(
        self,
        grant: CapabilityGrant,
        decision: SupervisorDecision,
        *,
        previous_level: AuthorityLevel,
    ) -> None:
        if decision.target_level > previous_level:
            raise PermissionError("a supervisor cannot expand authority")
        grant.level = decision.target_level
        grant.frozen = False
        grant.amount_ceiling = (
            50_000 if decision.target_level == AuthorityLevel.EXECUTE_BOUNDED else None
        )
        grant.verified_tasks = 0
        grant.outcome_correct = 0
        grant.reasoning_valid = 0
        grant.version += 1
        self.ledger.append(
            "SUPERVISOR_DEMOTION_APPLIED",
            {
                "incident_id": decision.incident_id,
                "decision_id": decision.decision_id,
                "agent_id": grant.agent_id,
                "capability": grant.capability,
                "previous_level": previous_level.name,
                "target_level": decision.target_level.name,
            },
        )

    def _maybe_promote(self, grant: CapabilityGrant) -> None:
        rule = self.rules.get(grant.level)
        if rule is None or grant.verified_tasks < rule.minimum_tasks:
            return
        outcome_rate = grant.outcome_correct / grant.verified_tasks
        reasoning_rate = grant.reasoning_valid / grant.verified_tasks
        if outcome_rate < rule.minimum_outcome_rate or reasoning_rate < rule.minimum_reasoning_rate:
            return

        previous_level = grant.level
        grant.level = rule.next_level
        grant.amount_ceiling = rule.amount_ceiling
        grant.earned_at = datetime.now(UTC)
        grant.verified_tasks = 0
        grant.outcome_correct = 0
        grant.reasoning_valid = 0
        grant.version += 1
        self.ledger.append(
            "CAPABILITY_PROMOTED",
            {
                "agent_id": grant.agent_id,
                "capability": grant.capability,
                "previous_level": previous_level.name,
                "new_level": grant.level.name,
                "amount_ceiling": grant.amount_ceiling,
                "policy": {
                    "minimum_tasks": rule.minimum_tasks,
                    "minimum_outcome_rate": rule.minimum_outcome_rate,
                    "minimum_reasoning_rate": rule.minimum_reasoning_rate,
                },
            },
        )
