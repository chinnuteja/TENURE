"""Google ADK adapter for the TENURE Supervisor Agent."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Sequence
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from tenure.domain import ActionProposal, AuthorityLevel, IncidentEnvelope
from tenure.ledger import AppendOnlyLedger, TrustLedger
from tenure.supervisor_tools import SupervisorToolbox

SUPERVISOR_INSTRUCTION = """
You are TENURE's incident Supervisor Agent. Deterministic containment has already
frozen the affected capability. Investigate only from the signed incident envelope,
append-only ledger events, action receipts, and traces supplied by tools.

You may recommend OBSERVE, SHADOW, or the previous authority level, but never a more
privileged level. Enumerate the complete blast radius, distinguish reversible from
irreversible actions, request only policy-permitted compensating actions, write a
concise incident narrative, and escalate every irreversible consequence. Never
promote, enlarge a ceiling, mint credentials, suppress a hard violation, or modify
ledger history.

Before deciding, use the evidence and blast-radius tools. Your final response must match
the required structured output: target_level and narrative. The narrative must cite the
incident, affected-action count, reversibility split, evidence, and why the demotion is
proportionate.
""".strip()


class SupervisorOutput(BaseModel):
    target_level: Literal["OBSERVE", "SHADOW", "EXECUTE_BOUNDED"]
    narrative: str = Field(min_length=40, max_length=2000)


def build_root_agent(toolbox: SupervisorToolbox | None = None):
    """Build the ADK agent after the optional dependency is installed."""

    try:
        from google.adk.agents import Agent
    except ImportError as exc:
        raise RuntimeError(
            'Install agent dependencies with: python -m pip install -e ".[agent]"'
        ) from exc

    bounded_tools = toolbox or SupervisorToolbox(AppendOnlyLedger())
    return Agent(
        name="tenure_supervisor",
        model=os.getenv("TENURE_GEMINI_MODEL", "gemini-3.5-flash"),
        description="Investigates contained agent failures and orchestrates bounded recovery.",
        instruction=SUPERVISOR_INSTRUCTION,
        tools=bounded_tools.adk_tools(),
        output_schema=SupervisorOutput,
        # Root LlmAgents run in chat mode in current ADK releases. Structured
        # output and deterministic post-validation retain the task contract.
        mode="chat",
    )


RunnerFactory = Callable[[Any], Any]


class AdkSupervisorReasoner:
    """Live-capable Google ADK runner behind the bounded reasoner port."""

    APP_NAME = "tenure_supervisor"

    def __init__(
        self,
        ledger: TrustLedger | None = None,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        self.ledger = ledger or AppendOnlyLedger()
        self.runner_factory = runner_factory or self._default_runner

    async def decide_async(
        self,
        incident: IncidentEnvelope,
        affected_actions: Sequence[ActionProposal],
    ) -> tuple[AuthorityLevel, str]:
        toolbox = SupervisorToolbox(self.ledger, affected_actions)
        toolbox.ledger.append(
            "INCIDENT_ENVELOPE_RECEIVED",
            {
                "incident_id": incident.incident_id,
                "agent_id": incident.agent_id,
                "capability": incident.capability,
                "previous_level": incident.previous_level.name,
                "reason": incident.reason,
                "trace_id": incident.trace_id,
            },
        )
        agent = build_root_agent(toolbox)
        runner = self.runner_factory(agent)
        user_id = "tenure-policy-engine"
        session_id = f"incident-{uuid4().hex[:12]}"
        await runner.session_service.create_session(
            app_name=self.APP_NAME,
            user_id=user_id,
            session_id=session_id,
            state={"incident_id": incident.incident_id},
        )

        from google.genai import types

        message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=self._prompt(incident, affected_actions))],
        )
        final_output: SupervisorOutput | None = None
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
        ):
            # Prefer ADK's schema-validated output when present; chat-mode
            # runners may otherwise provide the final JSON as response text.
            has_text = bool(
                event.content
                and event.content.parts
                and any(part.text for part in event.content.parts)
            )
            if event.output is not None or (event.is_final_response() and has_text):
                final_output = self._parse_event(event)

        if final_output is None:
            raise RuntimeError("ADK supervisor returned no structured final response")
        target = AuthorityLevel[final_output.target_level]
        if target > incident.previous_level:
            raise PermissionError("ADK supervisor attempted to expand authority")
        return target, final_output.narrative

    def decide(
        self,
        incident: IncidentEnvelope,
        affected_actions: Sequence[ActionProposal],
    ) -> tuple[AuthorityLevel, str]:
        """Synchronous boundary for worker threads and local command-line execution."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.decide_async(incident, affected_actions))
        raise RuntimeError("Use decide_async() when an event loop is already running")

    @classmethod
    def _default_runner(cls, agent: Any) -> Any:
        from google.adk.runners import InMemoryRunner

        return InMemoryRunner(agent=agent, app_name=cls.APP_NAME)

    @staticmethod
    def _prompt(
        incident: IncidentEnvelope, affected_actions: Sequence[ActionProposal]
    ) -> str:
        return json.dumps(
            {
                "task": "Investigate the contained incident and return a bounded demotion.",
                "incident": {
                    "incident_id": incident.incident_id,
                    "agent_id": incident.agent_id,
                    "capability": incident.capability,
                    "previous_level": incident.previous_level.name,
                    "failed_action_id": incident.failed_action_id,
                    "controlling_policy": incident.controlling_policy,
                    "reason": incident.reason,
                    "trace_id": incident.trace_id,
                },
                "known_action_ids": [action.action_id for action in affected_actions],
                "constraints": {
                    "may_promote": False,
                    "maximum_target_level": incident.previous_level.name,
                    "must_use_tools": True,
                },
            },
            sort_keys=True,
        )

    @staticmethod
    def _parse_event(event: Any) -> SupervisorOutput:
        if event.output is not None:
            return SupervisorOutput.model_validate(event.output)
        text = "".join(
            part.text or ""
            for part in (event.content.parts if event.content and event.content.parts else [])
        )
        return SupervisorOutput.model_validate_json(text)


try:
    root_agent = build_root_agent()
except RuntimeError:
    root_agent = None
