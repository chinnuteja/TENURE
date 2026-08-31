"""Constrained tool surface exposed to the Google ADK Supervisor Agent."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from tenure.domain import ActionProposal
from tenure.ledger import TrustLedger


@dataclass(slots=True)
class SupervisorToolbox:
    """Read evidence and request recovery actions without authority-expansion powers."""

    ledger: TrustLedger
    action_history: Sequence[ActionProposal] = field(default_factory=tuple)

    def query_incident_evidence(self, incident_id: str) -> dict[str, Any]:
        """Return immutable events whose payload references an incident identifier."""
        events = [
            event
            for event in self.ledger.events
            if event.payload.get("incident_id") == incident_id
        ]
        return {
            "incident_id": incident_id,
            "ledger_integrity": self.ledger.verify_chain(),
            "events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at,
                    "payload": event.payload,
                    "event_hash": event.event_hash,
                }
                for event in events
            ],
        }

    def enumerate_blast_radius(self, agent_id: str, capability: str) -> dict[str, Any]:
        """List actions exposed under one agent capability and their reversibility."""
        affected = [
            action
            for action in self.action_history
            if action.agent_id == agent_id and action.capability == capability
        ]
        return {
            "agent_id": agent_id,
            "capability": capability,
            "affected_actions": [action.snapshot() for action in affected],
            "reversible_action_ids": [
                action.action_id for action in affected if action.reversible
            ],
            "irreversible_action_ids": [
                action.action_id for action in affected if not action.reversible
            ],
        }

    def request_compensating_rollback(
        self, incident_id: str, action_id: str
    ) -> dict[str, Any]:
        """Request rollback only when the recorded action is explicitly reversible."""
        action = next(
            (candidate for candidate in self.action_history if candidate.action_id == action_id),
            None,
        )
        if action is None:
            return {"accepted": False, "reason": "ACTION_NOT_FOUND"}
        if not action.reversible:
            return {"accepted": False, "reason": "ACTION_IRREVERSIBLE"}
        event = self.ledger.append(
            "SUPERVISOR_TOOL_ROLLBACK_REQUESTED",
            {"incident_id": incident_id, "action_id": action_id},
        )
        return {"accepted": True, "event_id": event.event_id, "action_id": action_id}

    def file_irreversible_escalation(
        self, incident_id: str, action_ids: list[str], narrative: str
    ) -> dict[str, Any]:
        """File an evidence-complete escalation for irreversible recorded actions."""
        known_irreversible = {
            action.action_id
            for action in self.action_history
            if not action.reversible
        }
        accepted = [action_id for action_id in action_ids if action_id in known_irreversible]
        rejected = [action_id for action_id in action_ids if action_id not in known_irreversible]
        if not accepted:
            return {
                "accepted": False,
                "reason": "NO_IRREVERSIBLE_ACTIONS",
                "rejected_action_ids": rejected,
            }
        event = self.ledger.append(
            "SUPERVISOR_TOOL_ESCALATION_FILED",
            {
                "incident_id": incident_id,
                "action_ids": accepted,
                "narrative": narrative,
            },
        )
        return {
            "accepted": True,
            "event_id": event.event_id,
            "action_ids": accepted,
            "rejected_action_ids": rejected,
        }

    def adk_tools(self) -> list[Any]:
        """Return the complete allowlist; intentionally contains no promotion tool."""
        return [
            self.query_incident_evidence,
            self.enumerate_blast_radius,
            self.request_compensating_rollback,
            self.file_irreversible_escalation,
        ]
