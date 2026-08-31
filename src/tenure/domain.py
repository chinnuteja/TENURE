"""Domain types shared by TENURE services."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def utc_now() -> datetime:
    return datetime.now(UTC)


class AuthorityLevel(IntEnum):
    OBSERVE = 0
    SHADOW = 1
    EXECUTE_BOUNDED = 2
    EXECUTE_FULL = 3


class GatewayDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY_FROZEN = "DENY_FROZEN"
    DENY_LEVEL = "DENY_LEVEL"
    DENY_SCOPE = "DENY_SCOPE"


@dataclass(slots=True)
class CapabilityGrant:
    agent_id: str
    capability: str
    level: AuthorityLevel = AuthorityLevel.OBSERVE
    amount_ceiling: int | None = None
    allowed_vendors: frozenset[str] = field(default_factory=frozenset)
    verified_tasks: int = 0
    outcome_correct: int = 0
    reasoning_valid: int = 0
    frozen: bool = False
    earned_at: datetime | None = None
    version: int = 1

    def snapshot(self) -> dict[str, Any]:
        data = asdict(self)
        data["level"] = self.level.name
        data["allowed_vendors"] = sorted(self.allowed_vendors)
        data["earned_at"] = self.earned_at.isoformat() if self.earned_at else None
        return data


@dataclass(frozen=True, slots=True)
class VerificationResult:
    outcome_correct: bool
    reasoning_valid: bool
    controlling_policy: str
    cited_policy: str
    evidence_id: str = field(default_factory=lambda: new_id("evidence"))

    @property
    def rawr(self) -> bool:
        return self.outcome_correct and not self.reasoning_valid


@dataclass(frozen=True, slots=True)
class ActionProposal:
    agent_id: str
    capability: str
    amount: int
    vendor_id: str
    cited_policy: str
    reversible: bool
    action_id: str = field(default_factory=lambda: new_id("action"))
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActionTrustReceipt:
    action_id: str
    agent_id: str
    capability: str
    grant_level: str
    scope_ceiling: int | None
    controlling_policy: str
    credential_fingerprint: str | None
    gateway_decision: GatewayDecision
    trace_id: str
    supervision_status: str = "NONE"
    rollback_status: str = "NOT_REQUIRED"
    receipt_id: str = field(default_factory=lambda: new_id("receipt"))
    created_at: datetime = field(default_factory=utc_now)

    def snapshot(self) -> dict[str, Any]:
        data = asdict(self)
        data["gateway_decision"] = self.gateway_decision.value
        data["created_at"] = self.created_at.isoformat()
        return data


@dataclass(frozen=True, slots=True)
class IncidentEnvelope:
    incident_id: str
    agent_id: str
    capability: str
    failed_action_id: str
    previous_level: AuthorityLevel
    controlling_policy: str
    reason: str
    trace_id: str
    opened_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class SupervisorDecision:
    incident_id: str
    target_level: AuthorityLevel
    affected_action_ids: tuple[str, ...]
    rollback_action_ids: tuple[str, ...]
    escalation_action_ids: tuple[str, ...]
    narrative: str
    decision_id: str = field(default_factory=lambda: new_id("decision"))

