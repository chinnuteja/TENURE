"""Policy-bounded supervisor investigation and recovery orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from tenure.domain import ActionProposal, AuthorityLevel, IncidentEnvelope, SupervisorDecision
from tenure.ledger import TrustLedger


class SupervisorReasoner(Protocol):
    """Model boundary implemented by Google ADK in the cloud adapter."""

    def decide(
        self,
        incident: IncidentEnvelope,
        affected_actions: Sequence[ActionProposal],
    ) -> tuple[AuthorityLevel, str]:
        """Return a stricter target level and evidence-grounded narrative."""


@dataclass(slots=True)
class LocalEvidenceReasoner:
    """Offline reasoner used only for repeatable tests and the first vertical slice."""

    def decide(
        self,
        incident: IncidentEnvelope,
        affected_actions: Sequence[ActionProposal],
    ) -> tuple[AuthorityLevel, str]:
        high_value = any(action.amount > 25_000 for action in affected_actions)
        target = AuthorityLevel.OBSERVE if high_value else AuthorityLevel.SHADOW
        narrative = (
            f"Incident {incident.incident_id} affected {len(affected_actions)} actions under "
            f"{incident.capability}. Evidence indicates {incident.reason}. "
            f"Demotion to {target.name} is required while the capability re-earns trust."
        )
        return target, narrative


class SupervisorAgent:
    """Investigates after deterministic containment; never grants authority."""

    def __init__(
        self,
        ledger: TrustLedger,
        reasoner: SupervisorReasoner | None = None,
    ) -> None:
        self.ledger = ledger
        self.reasoner = reasoner or LocalEvidenceReasoner()

    def investigate(
        self,
        incident: IncidentEnvelope,
        action_history: Sequence[ActionProposal],
    ) -> SupervisorDecision:
        affected = tuple(
            action
            for action in action_history
            if action.agent_id == incident.agent_id
            and action.capability == incident.capability
        )
        target, narrative = self.reasoner.decide(incident, affected)
        if target > incident.previous_level:
            raise PermissionError("supervisor reasoner attempted to expand authority")

        rollback = tuple(action.action_id for action in affected if action.reversible)
        escalation = tuple(action.action_id for action in affected if not action.reversible)
        decision = SupervisorDecision(
            incident_id=incident.incident_id,
            target_level=target,
            affected_action_ids=tuple(action.action_id for action in affected),
            rollback_action_ids=rollback,
            escalation_action_ids=escalation,
            narrative=narrative,
        )
        self.ledger.append(
            "SUPERVISOR_INVESTIGATION_COMPLETED",
            {
                "incident_id": incident.incident_id,
                "decision_id": decision.decision_id,
                "target_level": target.name,
                "affected_action_ids": list(decision.affected_action_ids),
                "rollback_action_ids": list(rollback),
                "escalation_action_ids": list(escalation),
                "narrative": narrative,
            },
        )
        for action_id in rollback:
            self.ledger.append(
                "COMPENSATING_ROLLBACK_REQUESTED",
                {"incident_id": incident.incident_id, "action_id": action_id},
            )
        if escalation:
            self.ledger.append(
                "HUMAN_ESCALATION_FILED",
                {
                    "incident_id": incident.incident_id,
                    "action_ids": list(escalation),
                    "narrative": narrative,
                },
            )
        return decision
