"""Gateway enforcement and capability-scoped token minting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from uuid import uuid4

from tenure.domain import (
    ActionProposal,
    ActionTrustReceipt,
    AuthorityLevel,
    CapabilityGrant,
    GatewayDecision,
)
from tenure.ledger import TrustLedger


@dataclass(frozen=True, slots=True)
class GatewayResult:
    allowed: bool
    decision: GatewayDecision
    receipt: ActionTrustReceipt
    scope_token: str | None


class ScopeTokenIssuer:
    def __init__(self, secret: bytes | None = None, ttl_seconds: int = 60) -> None:
        configured = os.getenv("TENURE_TOKEN_SECRET", "tenure-local-demo-only").encode()
        self.secret = secret or configured
        self.ttl_seconds = ttl_seconds

    def issue(self, grant: CapabilityGrant, proposal: ActionProposal) -> str:
        claims = {
            "sub": grant.agent_id,
            "action_id": proposal.action_id,
            "capability": grant.capability,
            "amount_ceiling": grant.amount_ceiling,
            "exp": int(time.time()) + self.ttl_seconds,
            "nonce": uuid4().hex,
        }
        payload = base64.urlsafe_b64encode(
            json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        signature = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def verify(self, token: str) -> dict[str, object]:
        payload, signature = token.split(".", 1)
        expected = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise PermissionError("invalid scope token signature")
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded).decode())
        if int(claims["exp"]) < int(time.time()):
            raise PermissionError("scope token expired")
        return claims


class AgentGateway:
    def __init__(
        self, ledger: TrustLedger, token_issuer: ScopeTokenIssuer | None = None
    ) -> None:
        self.ledger = ledger
        self.token_issuer = token_issuer or ScopeTokenIssuer()

    def authorize(
        self,
        grant: CapabilityGrant,
        proposal: ActionProposal,
        *,
        controlling_policy: str,
    ) -> GatewayResult:
        decision = self._decision(grant, proposal)
        token = (
            self.token_issuer.issue(grant, proposal)
            if decision is GatewayDecision.ALLOW
            else None
        )
        fingerprint = hashlib.sha256(token.encode()).hexdigest()[:16] if token else None
        receipt = ActionTrustReceipt(
            action_id=proposal.action_id,
            agent_id=proposal.agent_id,
            capability=proposal.capability,
            grant_level=grant.level.name,
            scope_ceiling=grant.amount_ceiling,
            controlling_policy=controlling_policy,
            credential_fingerprint=fingerprint,
            gateway_decision=decision,
            trace_id=uuid4().hex,
        )
        self.ledger.append(
            "ACTION_TRUST_RECEIPT",
            {
                **receipt.snapshot(),
                "amount": proposal.amount,
                "vendor_id": proposal.vendor_id,
                "reversible": proposal.reversible,
            },
        )
        return GatewayResult(
            allowed=decision is GatewayDecision.ALLOW,
            decision=decision,
            receipt=receipt,
            scope_token=token,
        )

    @staticmethod
    def _decision(
        grant: CapabilityGrant, proposal: ActionProposal
    ) -> GatewayDecision:
        if proposal.agent_id != grant.agent_id or proposal.capability != grant.capability:
            return GatewayDecision.DENY_SCOPE
        if grant.frozen:
            return GatewayDecision.DENY_FROZEN
        if grant.level < AuthorityLevel.EXECUTE_BOUNDED:
            return GatewayDecision.DENY_LEVEL
        if proposal.capability == "payment.release" and (
            type(proposal.amount) is not int
            or proposal.amount <= 0
            or proposal.reversible is not True
        ):
            return GatewayDecision.DENY_SCOPE
        if grant.level == AuthorityLevel.EXECUTE_BOUNDED:
            if grant.amount_ceiling is None or proposal.amount > grant.amount_ceiling:
                return GatewayDecision.DENY_SCOPE
            if grant.allowed_vendors and proposal.vendor_id not in grant.allowed_vendors:
                return GatewayDecision.DENY_SCOPE
        return GatewayDecision.ALLOW
