from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tenure.adk_supervisor import AdkSupervisorReasoner
from tenure.domain import ActionProposal, AuthorityLevel, IncidentEnvelope
from tenure.ledger import AppendOnlyLedger
from tenure.model_armor import ModelArmorGateway
from tenure.observability import TenureTracing, build_tracer
from tenure.runtime import (
    RuntimeMode,
    cloud_readiness,
    configured_ledger_factory,
    selected_runtime,
)
from tenure.scenario import TenureScenario


class FakeSessionService:
    def __init__(self) -> None:
        self.created = None

    async def create_session(self, **kwargs):
        self.created = kwargs
        return SimpleNamespace(id=kwargs["session_id"])


class FakeAdkEvent:
    def __init__(self, output, *, final: bool = True) -> None:
        self.output = output
        self.content = None
        self.final = final

    def is_final_response(self) -> bool:
        return self.final


class FakeAdkRunner:
    def __init__(self, output, *, final: bool = True) -> None:
        self.session_service = FakeSessionService()
        self.output = output
        self.final = final

    async def run_async(self, **kwargs):
        assert kwargs["new_message"].role == "user"
        yield FakeAdkEvent(self.output, final=self.final)


def incident(previous_level: AuthorityLevel = AuthorityLevel.EXECUTE_BOUNDED):
    return IncidentEnvelope(
        incident_id="incident-live",
        agent_id="agent-1",
        capability="invoice.approve",
        failed_action_id="action-1",
        previous_level=previous_level,
        controlling_policy="policy#7.1",
        reason="outcome incorrect",
        trace_id="trace-1",
    )


def test_adk_runner_parses_schema_validated_bounded_decision() -> None:
    fake = FakeAdkRunner(
        {
            "target_level": "SHADOW",
            "narrative": (
                "Incident incident-live affected one reversible action; evidence supports "
                "a proportional demotion to SHADOW pending re-verification."
            ),
        }
    )
    reasoner = AdkSupervisorReasoner(runner_factory=lambda agent: fake)
    actions = [ActionProposal("agent-1", "invoice.approve", 10, "vendor", "policy", True)]

    target, narrative = asyncio.run(reasoner.decide_async(incident(), actions))
    assert target is AuthorityLevel.SHADOW
    assert "proportional" in narrative
    assert fake.session_service.created["app_name"] == "tenure_supervisor"


def test_adk_runner_accepts_task_output_before_conversational_final_event() -> None:
    fake = FakeAdkRunner(
        {
            "target_level": "OBSERVE",
            "narrative": (
                "Incident incident-live affected one reversible action; the task-mode "
                "result recommends OBSERVE until deterministic re-verification completes."
            ),
        },
        final=False,
    )
    reasoner = AdkSupervisorReasoner(runner_factory=lambda agent: fake)

    target, narrative = asyncio.run(reasoner.decide_async(incident(), []))

    assert target is AuthorityLevel.OBSERVE
    assert "task-mode" in narrative


def test_adk_runner_rejects_authority_expansion() -> None:
    fake = FakeAdkRunner(
        {
            "target_level": "EXECUTE_BOUNDED",
            "narrative": (
                "This deliberately invalid recommendation would expand authority "
                "after incident evidence."
            ),
        }
    )
    reasoner = AdkSupervisorReasoner(runner_factory=lambda agent: fake)
    with pytest.raises(PermissionError):
        asyncio.run(reasoner.decide_async(incident(AuthorityLevel.SHADOW), []))


@dataclass
class FakeHttpResponse:
    payload: dict

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeHttpSession:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.call = None

    def post(self, url: str, *, json: dict, timeout: int):
        self.call = (url, json, timeout)
        return FakeHttpResponse({"sanitizationResult": self.result})


@pytest.mark.parametrize(
    ("match_state", "invocation", "allowed"),
    [
        ("NO_MATCH_FOUND", "SUCCESS", True),
        ("MATCH_FOUND", "SUCCESS", False),
        ("NO_MATCH_FOUND", "PARTIAL", False),
    ],
)
def test_model_armor_fails_closed(match_state: str, invocation: str, allowed: bool) -> None:
    session = FakeHttpSession(
        {
            "filterMatchState": match_state,
            "invocationResult": invocation,
            "filterResults": {"pi_and_jailbreak": {}},
        }
    )
    armor = ModelArmorGateway("project", "us-central1", "tenure-template", session)
    verdict = armor.sanitize_user_prompt("latest prompt only")

    assert verdict.allowed is allowed
    assert session.call[0].startswith("https://modelarmor.us-central1.rep.googleapis.com/v1/")
    assert session.call[1] == {"userPromptData": {"text": "latest prompt only"}}
    assert session.call[2] == 30


def test_scenario_emits_one_span_per_real_transition() -> None:
    exporter = InMemorySpanExporter()
    tracing = TenureTracing(build_tracer(exporter, service_name="tenure-test"))
    scenario = TenureScenario(AppendOnlyLedger, tracing)
    scenario.run_all()

    spans = exporter.get_finished_spans()
    assert len(spans) == 8
    assert all(span.name == "tenure.scenario.transition" for span in spans)
    assert spans[-1].attributes["tenure.authority.after"] == "OBSERVE"
    assert spans[-1].attributes["tenure.capability"] == "invoice.approve"


class FakePromptGuard:
    def sanitize_user_prompt(self, latest_user_input: str):
        assert "10,00,000" in latest_user_input
        return SimpleNamespace(
            allowed=False,
            filter_match_state="MATCH_FOUND",
            invocation_result="SUCCESS",
        )


class FakeIncidentPublisher:
    def publish(self, opened_incident: IncidentEnvelope) -> str:
        assert opened_incident.capability == "invoice.approve"
        return "pubsub-message-1"


def test_cloud_boundaries_are_injected_into_same_scenario() -> None:
    scenario = TenureScenario(
        AppendOnlyLedger,
        prompt_guard=FakePromptGuard(),
        incident_publisher=FakeIncidentPublisher(),
    )
    result = scenario.run_all()

    assert result["metrics"]["model_armor_blocks"] == 1
    assert len(scenario.ledger.find("MODEL_ARMOR_SCREENED")) == 1
    published = scenario.ledger.find("INCIDENT_ENVELOPE_PUBLISHED")
    assert len(published) == 1
    assert published[0].payload["message_id"] == "pubsub-message-1"


def test_runtime_selection_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv("TENURE_RUNTIME", "local")
    assert selected_runtime() is RuntimeMode.LOCAL
    assert configured_ledger_factory().__name__ == "persistent_local_ledger"

    monkeypatch.setenv("TENURE_RUNTIME", "surprise")
    with pytest.raises(RuntimeError):
        selected_runtime()


def test_local_gemini_supervisor_requires_a_key(monkeypatch) -> None:
    from tenure.runtime import selected_supervisor_provider

    monkeypatch.setenv("TENURE_SUPERVISOR_PROVIDER", "gemini")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        selected_supervisor_provider()


def test_local_gemini_supervisor_can_be_selected(monkeypatch) -> None:
    from tenure.runtime import SupervisorProvider, selected_supervisor_provider

    monkeypatch.setenv("TENURE_SUPERVISOR_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-only-key")

    assert selected_supervisor_provider() is SupervisorProvider.GEMINI


def test_cloud_readiness_never_claims_billing(monkeypatch) -> None:
    monkeypatch.setenv("TENURE_RUNTIME", "local")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("TENURE_MODEL_ARMOR_TEMPLATE", "template")

    readiness = cloud_readiness()
    assert readiness["code_ready"] is True
    assert readiness["billing_verified"] is False
    assert "external deployment gates" in readiness["note"]
