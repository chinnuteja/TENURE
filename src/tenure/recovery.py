"""Fleet incident containment, agentic investigation, and bounded recovery."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from tenure.domain import AuthorityLevel
from tenure.fleet import AuthorityDependencyGraph, FleetRegistry, ProcureToPayFleet
from tenure.ledger import TrustLedger


def capability_key(agent_id: str, capability: str) -> str:
    return f"{agent_id}:{capability}"


class RecoveryScenario(StrEnum):
    ISOLATED = "isolated"
    CORRELATED = "correlated"
    UPSTREAM_COMPROMISE = "upstream_compromise"
    POLICY_DRIFT = "policy_drift"


class RecoveryDepth(StrEnum):
    SINGLE_CAPABILITY = "SINGLE_CAPABILITY"
    DOWNSTREAM_CHAIN = "DOWNSTREAM_CHAIN"
    FLEET = "FLEET"


class RecoveryPolicyError(PermissionError):
    """Raised when a Supervisor proposal violates one-way authority policy."""


@dataclass(frozen=True, slots=True)
class RecoveryContext:
    scenario: RecoveryScenario
    root_agent_id: str
    root_capability: str
    correlated_capability_keys: tuple[str, ...]
    shared_upstream: bool
    policy_integrity: str
    trace_id: str

    @property
    def root_key(self) -> str:
        return capability_key(self.root_agent_id, self.root_capability)

    def snapshot(self) -> dict[str, Any]:
        data = asdict(self)
        data["scenario"] = self.scenario.value
        data["correlated_capability_keys"] = list(
            self.correlated_capability_keys
        )
        data["root_key"] = self.root_key
        return data


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    action_id: str
    capability_key: str
    entity_type: str
    entity_id: str
    reversible: bool

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FleetIncident:
    incident_id: str
    tenant_id: str
    case_id: str
    context: RecoveryContext
    previous_authority: dict[str, str]
    signature: str

    def unsigned_snapshot(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "tenant_id": self.tenant_id,
            "case_id": self.case_id,
            "context": self.context.snapshot(),
            "previous_authority": self.previous_authority,
        }

    def snapshot(self) -> dict[str, Any]:
        return {**self.unsigned_snapshot(), "signature": self.signature}


@dataclass(frozen=True, slots=True)
class RecoveryGuardrail:
    required_scope: tuple[str, ...]
    required_depth: RecoveryDepth
    maximum_target_level: AuthorityLevel

    def snapshot(self) -> dict[str, Any]:
        return {
            "required_scope": list(self.required_scope),
            "required_depth": self.required_depth.value,
            "maximum_target_level": self.maximum_target_level.name,
        }


@dataclass(frozen=True, slots=True)
class FleetRecoveryProposal:
    incident_id: str
    demotion_depth: RecoveryDepth
    target_level: AuthorityLevel
    affected_capability_keys: tuple[str, ...]
    rollback_action_ids: tuple[str, ...]
    escalation_action_ids: tuple[str, ...]
    narrative: str

    def snapshot(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "demotion_depth": self.demotion_depth.value,
            "target_level": self.target_level.name,
            "affected_capability_keys": list(self.affected_capability_keys),
            "rollback_action_ids": list(self.rollback_action_ids),
            "escalation_action_ids": list(self.escalation_action_ids),
            "narrative": self.narrative,
        }


class MemoryReader(Protocol):
    def read(self, query: str) -> dict[str, Any]: ...


@dataclass(slots=True)
class VerifiedMemorySnapshot:
    resource: str | None = None
    fact: str = (
        "Correlated failures sharing an upstream vendor require capability-family "
        "containment, subject to deterministic policy validation."
    )

    def read(self, query: str) -> dict[str, Any]:
        return {
            "query": query,
            "resource": self.resource or "local-memory://tenure/supervisor",
            "retrieval_mode": "VERIFIED_SNAPSHOT",
            "retrieval_verified": True,
            "memories": [{"fact": self.fact}],
        }


class FleetRecoveryPolicy:
    OPERATING_KEYS = (
        "vendor-intelligence-agent:vendor.onboard",
        "invoice-agent:invoice.approve",
        "treasury-agent:payment.release",
    )
    REQUIRED_TOOL_CATEGORIES = frozenset(
        {
            "ledger",
            "registry",
            "memory",
            "trace",
            "graph",
            "rollback",
            "escalation",
        }
    )

    def guardrail(
        self,
        context: RecoveryContext,
        graph: AuthorityDependencyGraph,
    ) -> RecoveryGuardrail:
        if context.policy_integrity != "verified":
            scope = self.OPERATING_KEYS
            depth = RecoveryDepth.FLEET
            target = AuthorityLevel.OBSERVE
        elif context.shared_upstream:
            downstream = graph.downstream(
                context.root_agent_id, context.root_capability
            )
            scope = (
                context.root_key,
                *(
                    capability_key(
                        edge["downstream_agent_id"], edge["downstream_capability"]
                    )
                    for edge in downstream
                ),
            )
            depth = RecoveryDepth.DOWNSTREAM_CHAIN
            target = AuthorityLevel.OBSERVE
        elif context.correlated_capability_keys:
            scope = (context.root_key, *context.correlated_capability_keys)
            depth = RecoveryDepth.DOWNSTREAM_CHAIN
            target = AuthorityLevel.SHADOW
        else:
            scope = (context.root_key,)
            depth = RecoveryDepth.SINGLE_CAPABILITY
            target = AuthorityLevel.SHADOW
        return RecoveryGuardrail(tuple(dict.fromkeys(scope)), depth, target)

    def validate(
        self,
        *,
        incident: FleetIncident,
        proposal: FleetRecoveryProposal,
        guardrail: RecoveryGuardrail,
        toolbox: FleetRecoveryToolbox,
    ) -> None:
        violations: list[str] = []
        if proposal.incident_id != incident.incident_id:
            violations.append("INCIDENT_MISMATCH")
        if proposal.target_level > guardrail.maximum_target_level:
            violations.append("TARGET_PRESERVES_COMPROMISED_AUTHORITY")
        if any(
            proposal.target_level > AuthorityLevel[level]
            for level in incident.previous_authority.values()
        ):
            violations.append("AUTHORITY_AMPLIFICATION")
        if set(proposal.affected_capability_keys) != set(guardrail.required_scope):
            violations.append("INVALID_DEMOTION_SCOPE")
        if proposal.demotion_depth is not guardrail.required_depth:
            violations.append("INVALID_DEMOTION_DEPTH")
        if not self.REQUIRED_TOOL_CATEGORIES.issubset(toolbox.used_tools):
            violations.append("INCOMPLETE_TOOL_INVESTIGATION")
        if not toolbox.memory_retrieval_verified:
            violations.append("MEMORY_NOT_VERIFIED")

        affected = set(guardrail.required_scope)
        reversible = {
            action.action_id
            for action in toolbox.actions
            if action.capability_key in affected and action.reversible
        }
        irreversible = {
            action.action_id
            for action in toolbox.actions
            if action.capability_key in affected and not action.reversible
        }
        if set(proposal.rollback_action_ids) != reversible:
            violations.append("INCOMPLETE_ROLLBACK_SET")
        if set(toolbox.requested_rollbacks) != reversible:
            violations.append("ROLLBACK_TOOL_MISMATCH")
        if set(proposal.escalation_action_ids) != irreversible:
            violations.append("INCOMPLETE_ESCALATION_SET")
        if set(toolbox.escalated_actions) != irreversible:
            violations.append("ESCALATION_TOOL_MISMATCH")
        if violations:
            raise RecoveryPolicyError(",".join(violations))


class FleetRecoveryToolbox:
    """The complete Supervisor allowlist; it contains no authority mutation tool."""

    def __init__(
        self,
        *,
        ledger: TrustLedger,
        incident: FleetIncident,
        registry: FleetRegistry,
        graph: AuthorityDependencyGraph,
        memory_reader: MemoryReader,
        actions: Sequence[RecoveryAction],
    ) -> None:
        self.ledger = ledger
        self.incident = incident
        self.registry = registry
        self.graph = graph
        self.memory_reader = memory_reader
        self.actions = tuple(actions)
        self.used_tools: set[str] = set()
        self.requested_rollbacks: list[str] = []
        self.escalated_actions: list[str] = []
        self.memory_retrieval_verified = False

    def _record_tool(self, category: str, tool: str, detail: dict[str, Any]) -> None:
        self.used_tools.add(category)
        self.ledger.append(
            "SUPERVISOR_TOOL_ACCESSED",
            {
                "incident_id": self.incident.incident_id,
                "tenant_id": self.incident.tenant_id,
                "case_id": self.incident.case_id,
                "category": category,
                "tool": tool,
                **detail,
            },
        )

    def read_incident_ledger(self, incident_id: str) -> dict[str, Any]:
        """Read signed incident and immutable case events from the trust ledger."""
        if incident_id != self.incident.incident_id:
            return {"verified": False, "reason": "INCIDENT_MISMATCH"}
        events = [
            event
            for event in self.ledger.events
            if event.payload.get("case_id") == self.incident.case_id
            and event.payload.get("tenant_id") == self.incident.tenant_id
        ]
        self._record_tool(
            "ledger",
            "read_incident_ledger",
            {"event_count": len(events), "ledger_integrity": self.ledger.verify_chain()},
        )
        return {
            "verified": True,
            "incident": self.incident.snapshot(),
            "ledger_integrity": self.ledger.verify_chain(),
            "events": [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "payload": event.payload,
                    "event_hash": event.event_hash,
                }
                for event in events
            ],
        }

    def read_agent_registry(self) -> dict[str, Any]:
        """Read the four registered fleet identities and their capabilities."""
        agents = self.registry.discover()
        self._record_tool(
            "registry", "read_agent_registry", {"agent_count": len(agents)}
        )
        return {"verified": True, "agents": agents}

    def read_supervisor_memory(self, query: str) -> dict[str, Any]:
        """Retrieve incident lessons from the configured Supervisor Memory Bank."""
        result = self.memory_reader.read(query)
        self.memory_retrieval_verified = bool(result.get("retrieval_verified"))
        self._record_tool(
            "memory",
            "read_supervisor_memory",
            {
                "resource": result.get("resource"),
                "retrieval_mode": result.get("retrieval_mode"),
                "retrieval_verified": self.memory_retrieval_verified,
            },
        )
        return result

    def read_trace(self, trace_id: str) -> dict[str, Any]:
        """Read immutable trace-linked incident and action evidence."""
        if trace_id != self.incident.context.trace_id:
            return {"verified": False, "reason": "TRACE_MISMATCH"}
        events = [
            event
            for event in self.ledger.events
            if event.payload.get("trace_id") == trace_id
            or event.payload.get("incident_id") == self.incident.incident_id
        ]
        self._record_tool(
            "trace", "read_trace", {"trace_id": trace_id, "event_count": len(events)}
        )
        return {
            "verified": bool(events),
            "trace_id": trace_id,
            "event_ids": [event.event_id for event in events],
        }

    def traverse_dependency_graph(
        self, agent_id: str, capability: str
    ) -> dict[str, Any]:
        """Traverse transitive authority dependencies from one capability."""
        downstream = self.graph.downstream(agent_id, capability)
        self._record_tool(
            "graph",
            "traverse_dependency_graph",
            {"agent_id": agent_id, "capability": capability, "edge_count": len(downstream)},
        )
        return {
            "root": capability_key(agent_id, capability),
            "downstream": downstream,
        }

    def request_compensating_rollback(
        self, incident_id: str, action_id: str
    ) -> dict[str, Any]:
        """Request rollback only for a recorded reversible sandbox mutation."""
        action = next((item for item in self.actions if item.action_id == action_id), None)
        if incident_id != self.incident.incident_id:
            return {"accepted": False, "reason": "INCIDENT_MISMATCH"}
        if action is None:
            return {"accepted": False, "reason": "ACTION_NOT_FOUND"}
        if not action.reversible:
            return {"accepted": False, "reason": "ACTION_IRREVERSIBLE"}
        if action_id not in self.requested_rollbacks:
            self.requested_rollbacks.append(action_id)
            self._record_tool(
                "rollback",
                "request_compensating_rollback",
                {"action_id": action_id, "accepted": True},
            )
        return {"accepted": True, "action_id": action_id}

    def request_compensating_rollbacks(
        self, incident_id: str, action_ids: list[str]
    ) -> dict[str, Any]:
        """Atomically request the complete reversible set for this incident."""
        if incident_id != self.incident.incident_id:
            return {"accepted": False, "reason": "INCIDENT_MISMATCH"}
        reversible = {
            action.action_id for action in self.actions if action.reversible
        }
        requested = set(action_ids)
        accepted = sorted(requested & reversible)
        rejected = sorted(requested - reversible)
        self.requested_rollbacks.extend(
            action_id
            for action_id in accepted
            if action_id not in self.requested_rollbacks
        )
        self._record_tool(
            "rollback",
            "request_compensating_rollbacks",
            {
                "accepted_action_ids": accepted,
                "rejected_action_ids": rejected,
            },
        )
        return {
            "accepted": not rejected,
            "action_ids": accepted,
            "rejected_action_ids": rejected,
        }

    def file_irreversible_escalation(
        self, incident_id: str, action_ids: list[str], narrative: str
    ) -> dict[str, Any]:
        """File escalation only for recorded irreversible consequences."""
        if incident_id != self.incident.incident_id:
            return {"accepted": False, "reason": "INCIDENT_MISMATCH"}
        irreversible = {
            action.action_id for action in self.actions if not action.reversible
        }
        accepted = sorted(set(action_ids) & irreversible)
        rejected = sorted(set(action_ids) - irreversible)
        self.escalated_actions.extend(
            action_id
            for action_id in accepted
            if action_id not in self.escalated_actions
        )
        self._record_tool(
            "escalation",
            "file_irreversible_escalation",
            {
                "accepted_action_ids": accepted,
                "rejected_action_ids": rejected,
                "narrative": narrative,
            },
        )
        return {
            "accepted": bool(accepted) or not irreversible,
            "action_ids": accepted,
            "rejected_action_ids": rejected,
        }

    def adk_tools(self) -> list[Any]:
        return [
            self.read_incident_ledger,
            self.read_agent_registry,
            self.read_supervisor_memory,
            self.read_trace,
            self.traverse_dependency_graph,
            self.request_compensating_rollbacks,
            self.file_irreversible_escalation,
        ]


class FleetRecoveryReasoner(Protocol):
    mode: str

    def decide(
        self,
        incident: FleetIncident,
        toolbox: FleetRecoveryToolbox,
        guardrail: RecoveryGuardrail,
    ) -> FleetRecoveryProposal: ...


@dataclass(slots=True)
class LocalFleetRecoveryReasoner:
    """Deterministic test double that exercises the exact production tool surface."""

    mode: str = "LOCAL_DETERMINISTIC"

    def decide(
        self,
        incident: FleetIncident,
        toolbox: FleetRecoveryToolbox,
        guardrail: RecoveryGuardrail,
    ) -> FleetRecoveryProposal:
        toolbox.read_incident_ledger(incident.incident_id)
        toolbox.read_agent_registry()
        toolbox.read_supervisor_memory(
            f"containment guidance for {incident.context.scenario.value}"
        )
        toolbox.read_trace(incident.context.trace_id)
        toolbox.traverse_dependency_graph(
            incident.context.root_agent_id, incident.context.root_capability
        )
        affected = set(guardrail.required_scope)
        rollback = tuple(
            action.action_id
            for action in toolbox.actions
            if action.capability_key in affected and action.reversible
        )
        escalation = tuple(
            action.action_id
            for action in toolbox.actions
            if action.capability_key in affected and not action.reversible
        )
        toolbox.request_compensating_rollbacks(
            incident.incident_id, list(rollback)
        )
        toolbox.file_irreversible_escalation(
            incident.incident_id,
            list(escalation),
            "Irreversible consequences require an evidence-complete human escalation.",
        )
        return FleetRecoveryProposal(
            incident_id=incident.incident_id,
            demotion_depth=guardrail.required_depth,
            target_level=guardrail.maximum_target_level,
            affected_capability_keys=guardrail.required_scope,
            rollback_action_ids=rollback,
            escalation_action_ids=escalation,
            narrative=(
                f"Incident {incident.incident_id} is {incident.context.scenario.value}. "
                f"Ledger, Registry, Memory, trace, and dependency evidence support "
                f"{guardrail.required_depth.value} containment across "
                f"{len(guardrail.required_scope)} capabilities, with {len(rollback)} "
                f"reversible actions and {len(escalation)} irreversible escalations."
            ),
        )


class FleetSupervisorOutput(BaseModel):
    demotion_depth: Literal["SINGLE_CAPABILITY", "DOWNSTREAM_CHAIN", "FLEET"]
    target_level: Literal["OBSERVE", "SHADOW", "EXECUTE_BOUNDED"]
    affected_capability_keys: list[str] = Field(min_length=1)
    rollback_action_ids: list[str]
    escalation_action_ids: list[str]
    narrative: str = Field(min_length=80, max_length=3000)


FLEET_SUPERVISOR_INSTRUCTION = """
You are TENURE's fleet incident Supervisor Agent. Deterministic code has already
frozen the initially affected authority. Your job is contextual investigation and
bounded recovery, never promotion.

You must use every available tool category before answering: ledger, Registry,
Supervisor Memory, trace, dependency graph, rollback, and escalation. A shared
upstream compromise requires the root capability and all transitive descendants;
correlated failures require every correlated capability; policy-integrity failure
requires the full operating fleet; an isolated failure should remain isolated.

Choose the narrowest defensible demotion depth and a target no more privileged than
the compromised authority. The signed request includes a deterministic
maximum_permitted_target_level; treat it as a hard ceiling and choose that level or a
more restrictive one. Call request_compensating_rollbacks exactly once with every
reversible action in your affected scope, then copy its accepted action_ids exactly
into rollback_action_ids. Escalate every irreversible action and copy the accepted
action_ids exactly into escalation_action_ids. Never grant, restore, expand, mint
credentials, mutate passports, alter policy, or edit ledger history. Return only the
required structured result, grounded in the tool evidence.
""".strip()


def build_fleet_supervisor_agent(toolbox: FleetRecoveryToolbox):
    try:
        from google.adk.agents import Agent
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            'Install agent dependencies with: python -m pip install -e ".[agent]"'
        ) from exc
    return Agent(
        name="tenure_fleet_supervisor",
        model=os.getenv("TENURE_GEMINI_MODEL", "gemini-3.5-flash"),
        description="Investigates frozen fleet failures and proposes bounded recovery.",
        instruction=FLEET_SUPERVISOR_INSTRUCTION,
        tools=toolbox.adk_tools(),
        output_schema=FleetSupervisorOutput,
        # Current ADK runners require a root LlmAgent to use chat mode. The
        # output schema still constrains the final response, while the agent's
        # tool calls remain the observable investigation trace.
        mode="chat",
        generate_content_config=types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=2048,
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.LOW
            ),
        ),
    )


class AdkFleetRecoveryReasoner:
    mode = "GEMINI_ADK"
    APP_NAME = "tenure_fleet_supervisor"

    def __init__(self, runner_factory: Any | None = None) -> None:
        self.runner_factory = runner_factory or self._default_runner

    async def decide_async(
        self,
        incident: FleetIncident,
        toolbox: FleetRecoveryToolbox,
        guardrail: RecoveryGuardrail,
    ) -> FleetRecoveryProposal:
        agent = build_fleet_supervisor_agent(toolbox)
        runner = self.runner_factory(agent)
        session_id = f"fleet-recovery-{uuid4().hex[:12]}"
        user_id = "tenure-policy-engine"
        await runner.session_service.create_session(
            app_name=self.APP_NAME,
            user_id=user_id,
            session_id=session_id,
            state={"incident_id": incident.incident_id},
        )
        from google.genai import types

        prompt = {
            "task": "Investigate and propose the narrowest complete fleet recovery.",
            "incident": incident.snapshot(),
            "known_capability_keys": list(FleetRecoveryPolicy.OPERATING_KEYS),
            "known_actions": [action.snapshot() for action in toolbox.actions],
            "safety_ceiling": {
                "previous_authority": incident.previous_authority,
                "may_expand_authority": False,
                "maximum_permitted_target_level": (
                    guardrail.maximum_target_level.name
                ),
                "policy_will_validate_scope": True,
            },
            "required_tool_categories": sorted(
                FleetRecoveryPolicy.REQUIRED_TOOL_CATEGORIES
            ),
        }
        message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=json.dumps(prompt, sort_keys=True))],
        )
        final_output: FleetSupervisorOutput | None = None
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
        ):
            has_text = bool(
                event.content
                and event.content.parts
                and any(part.text for part in event.content.parts)
            )
            if event.output is not None or (event.is_final_response() and has_text):
                final_output = self._parse_event(event)
        if final_output is None:
            raise RuntimeError("ADK fleet supervisor returned no structured output")
        return FleetRecoveryProposal(
            incident_id=incident.incident_id,
            demotion_depth=RecoveryDepth(final_output.demotion_depth),
            target_level=AuthorityLevel[final_output.target_level],
            affected_capability_keys=tuple(final_output.affected_capability_keys),
            rollback_action_ids=tuple(final_output.rollback_action_ids),
            escalation_action_ids=tuple(final_output.escalation_action_ids),
            narrative=final_output.narrative,
        )

    def decide(
        self,
        incident: FleetIncident,
        toolbox: FleetRecoveryToolbox,
        guardrail: RecoveryGuardrail,
    ) -> FleetRecoveryProposal:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.decide_async(incident, toolbox, guardrail))
        raise RuntimeError("Use decide_async() when an event loop is already running")

    @classmethod
    def _default_runner(cls, agent: Any) -> Any:
        from google.adk.runners import InMemoryRunner

        return InMemoryRunner(agent=agent, app_name=cls.APP_NAME)

    @staticmethod
    def _parse_event(event: Any) -> FleetSupervisorOutput:
        if event.output is not None:
            return FleetSupervisorOutput.model_validate(event.output)
        text = "".join(
            part.text or ""
            for part in (
                event.content.parts if event.content and event.content.parts else []
            )
        )
        return FleetSupervisorOutput.model_validate_json(text)


class FleetRecoveryOrchestrator:
    """Freezes first, asks the Supervisor second, and applies policy third."""

    def __init__(
        self,
        fleet: ProcureToPayFleet,
        *,
        reasoner: FleetRecoveryReasoner | None = None,
        memory_reader: MemoryReader | None = None,
        signing_key: bytes = b"tenure-local-recovery-envelope",
        trace_id: str | None = None,
    ) -> None:
        self.fleet = fleet
        self.ledger = fleet.ledger
        self.reasoner = reasoner or LocalFleetRecoveryReasoner()
        self.memory_reader = memory_reader or VerifiedMemorySnapshot(
            os.getenv("TENURE_SUPERVISOR_MEMORY_RESOURCE")
        )
        self.signing_key = signing_key
        self.trace_id = trace_id or os.getenv(
            "TENURE_LAST_TRACE_ID", "trace-local-fleet-recovery"
        )
        self.policy = FleetRecoveryPolicy()

    def run(
        self,
        *,
        tenant_id: str,
        case_id: str,
        scenario: RecoveryScenario,
        amount: int = 18_400,
    ) -> dict[str, Any]:
        self.fleet.run_case(tenant_id=tenant_id, case_id=case_id, amount=amount)
        context = self._context(scenario)
        guardrail = self.policy.guardrail(context, self.fleet.dependencies)
        incident = self._signed_incident(tenant_id, case_id, context)
        if not self.verify_incident(incident):
            raise RecoveryPolicyError("INCIDENT_SIGNATURE_INVALID")
        actions = self._actions(case_id, tenant_id, scenario)

        # Persist containment before audit or model work. A failed model call or
        # failed ledger write must never leave the capability executable.
        self.fleet.control.freeze(tenant_id, guardrail.required_scope, incident.incident_id)
        freeze_events = []
        for key in guardrail.required_scope:
            freeze_events.append(
                self.ledger.append(
                    "FLEET_CAPABILITY_FROZEN",
                    {
                        "incident_id": incident.incident_id,
                        "tenant_id": tenant_id,
                        "case_id": case_id,
                        "capability_key": key,
                        "previous_level": incident.previous_authority[key],
                        "trace_id": context.trace_id,
                        "reason": scenario.value,
                    },
                )
            )
        opened = self.ledger.append(
            "FLEET_INCIDENT_OPENED",
            {
                **incident.snapshot(),
                "trace_id": context.trace_id,
            },
        )
        toolbox = FleetRecoveryToolbox(
            ledger=self.ledger,
            incident=incident,
            registry=self.fleet.registry,
            graph=self.fleet.dependencies,
            memory_reader=self.memory_reader,
            actions=actions,
        )
        try:
            proposal = self.reasoner.decide(incident, toolbox, guardrail)
            self.policy.validate(
                incident=incident,
                proposal=proposal,
                guardrail=guardrail,
                toolbox=toolbox,
            )
        except Exception as exc:
            self.ledger.append(
                "SUPERVISOR_PROPOSAL_REJECTED",
                {
                    "incident_id": incident.incident_id,
                    "tenant_id": tenant_id,
                    "case_id": case_id,
                    "reason": str(exc),
                    "authority_changed": False,
                },
            )
            raise

        self.fleet.control.demote(
            tenant_id, proposal.affected_capability_keys,
            incident.incident_id, proposal.target_level,
        )
        demotion_events = []
        for key in proposal.affected_capability_keys:
            demotion_events.append(
                self.ledger.append(
                    "FLEET_DEMOTION_APPLIED",
                    {
                        "incident_id": incident.incident_id,
                        "tenant_id": tenant_id,
                        "case_id": case_id,
                        "capability_key": key,
                        "previous_level": incident.previous_authority[key],
                        "target_level": proposal.target_level.name,
                        "demotion_depth": proposal.demotion_depth.value,
                    },
                )
            )

        action_by_id = {action.action_id: action for action in actions}
        rollback_results = []
        for action_id in proposal.rollback_action_ids:
            action = action_by_id[action_id]
            rollback = self.fleet.sandbox.rollback_entity(
                tenant_id, action.entity_type, action.entity_id
            )
            event = self.ledger.append(
                "SANDBOX_ROLLBACK_APPLIED",
                {
                    "incident_id": incident.incident_id,
                    "tenant_id": tenant_id,
                    "case_id": case_id,
                    "action_id": action_id,
                    **rollback,
                },
            )
            rollback_results.append({**rollback, "event_id": event.event_id})
        escalation_events = [
            event
            for event in self.ledger.find(
                "SUPERVISOR_TOOL_ACCESSED", incident_id=incident.incident_id
            )
            if event.payload.get("category") == "escalation"
        ]
        completed = self.ledger.append(
            "FLEET_RECOVERY_COMPLETED",
            {
                "incident_id": incident.incident_id,
                "tenant_id": tenant_id,
                "case_id": case_id,
                "reasoner_mode": self.reasoner.mode,
                "target_level": proposal.target_level.name,
                "demotion_depth": proposal.demotion_depth.value,
                "rollback_count": len(rollback_results),
                "escalation_count": len(proposal.escalation_action_ids),
                "trace_id": context.trace_id,
            },
        )
        self.fleet.control.finish_recovery(
            tenant_id, proposal.affected_capability_keys, incident.incident_id,
        )
        tool_events = self.ledger.find(
            "SUPERVISOR_TOOL_ACCESSED", incident_id=incident.incident_id
        )
        first_tool_sequence = min(event.sequence for event in tool_events)
        return {
            "incident": incident.snapshot(),
            "scenario": scenario.value,
            "guardrail": guardrail.snapshot(),
            "proposal": proposal.snapshot(),
            "reasoner_mode": self.reasoner.mode,
            "model_calls": int(self.reasoner.mode == "GEMINI_ADK"),
            "tool_categories": sorted(toolbox.used_tools),
            "memory_retrieval_verified": toolbox.memory_retrieval_verified,
            "freeze_event_ids": [event.event_id for event in freeze_events],
            "incident_event_id": opened.event_id,
            "demotion_event_ids": [event.event_id for event in demotion_events],
            "rollback_results": rollback_results,
            "escalation_action_ids": list(proposal.escalation_action_ids),
            "escalation_event_ids": [event.event_id for event in escalation_events],
            "recovery_event_id": completed.event_id,
            "freeze_preceded_supervision": (
                max(event.sequence for event in freeze_events) < first_tool_sequence
            ),
            "authority_after": {
                key: self.fleet.control.snapshot(tenant_id)[key]["level"]
                for key in proposal.affected_capability_keys
            },
            "state_after": self._state_after(tenant_id, case_id),
            "ledger_integrity": self.ledger.verify_chain(),
        }

    def _signed_incident(
        self, tenant_id: str, case_id: str, context: RecoveryContext
    ) -> FleetIncident:
        incident_id = f"fleet-incident_{uuid4().hex[:16]}"
        state = self.fleet.control.snapshot(tenant_id)
        previous = {
            key: state.get(key, {}).get("level", "OBSERVE")
            for key in FleetRecoveryPolicy.OPERATING_KEYS
        }
        unsigned = {
            "incident_id": incident_id,
            "tenant_id": tenant_id,
            "case_id": case_id,
            "context": context.snapshot(),
            "previous_authority": previous,
        }
        canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"))
        signature = hmac.new(
            self.signing_key, canonical.encode(), hashlib.sha256
        ).hexdigest()
        return FleetIncident(
            incident_id,
            tenant_id,
            case_id,
            context,
            previous,
            signature,
        )

    def verify_incident(self, incident: FleetIncident) -> bool:
        canonical = json.dumps(
            incident.unsigned_snapshot(), sort_keys=True, separators=(",", ":")
        )
        expected = hmac.new(
            self.signing_key, canonical.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, incident.signature)

    def _actions(
        self, case_id: str, tenant_id: str, scenario: RecoveryScenario
    ) -> tuple[RecoveryAction, ...]:
        mutations = self.ledger.find(
            "SANDBOX_MUTATION_COMMITTED",
            case_id=case_id,
            tenant_id=tenant_id,
        )
        capability_by_entity = {
            "vendor": "vendor-intelligence-agent:vendor.onboard",
            "invoice": "invoice-agent:invoice.approve",
            "payment": "treasury-agent:payment.release",
        }
        actions = [
            RecoveryAction(
                event.payload["action_id"],
                capability_by_entity[event.payload["entity_type"]],
                event.payload["entity_type"],
                event.payload["entity_id"],
                bool(event.payload["reversible"]),
            )
            for event in mutations
        ]
        if scenario in {
            RecoveryScenario.UPSTREAM_COMPROMISE,
            RecoveryScenario.POLICY_DRIFT,
        }:
            actions.append(
                RecoveryAction(
                    f"bank-export-{case_id}",
                    "treasury-agent:payment.release",
                    "bank_export",
                    f"bank-export-{case_id}",
                    False,
                )
            )
        return tuple(actions)

    def _context(self, scenario: RecoveryScenario) -> RecoveryContext:
        if scenario is RecoveryScenario.ISOLATED:
            return RecoveryContext(
                scenario,
                "treasury-agent",
                "payment.release",
                (),
                False,
                "verified",
                self.trace_id,
            )
        if scenario is RecoveryScenario.CORRELATED:
            return RecoveryContext(
                scenario,
                "invoice-agent",
                "invoice.approve",
                ("treasury-agent:payment.release",),
                False,
                "verified",
                self.trace_id,
            )
        if scenario is RecoveryScenario.UPSTREAM_COMPROMISE:
            return RecoveryContext(
                scenario,
                "vendor-intelligence-agent",
                "vendor.onboard",
                (),
                True,
                "verified",
                self.trace_id,
            )
        return RecoveryContext(
            scenario,
            "vendor-intelligence-agent",
            "vendor.onboard",
            (),
            False,
            "drift_detected",
            self.trace_id,
        )

    def _state_after(self, tenant_id: str, case_id: str) -> dict[str, Any]:
        return {
            "vendor": self.fleet.sandbox.vendor_snapshot(
                tenant_id, f"vendor-{case_id}"
            ),
            "invoice": self.fleet.sandbox.invoice_snapshot(
                tenant_id, f"invoice-{case_id}"
            ),
            "payment": self.fleet.sandbox.payment_snapshot(
                tenant_id, f"payment-{case_id}"
            ),
        }
