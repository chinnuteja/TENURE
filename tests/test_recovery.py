from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tenure.domain import AuthorityLevel
from tenure.fleet import ProcureToPayFleet
from tenure.recovery import (
    AdkFleetRecoveryReasoner,
    FleetRecoveryOrchestrator,
    FleetRecoveryProposal,
    FleetRecoveryToolbox,
    RecoveryDepth,
    RecoveryGuardrail,
    RecoveryPolicyError,
    RecoveryScenario,
    build_fleet_supervisor_agent,
)


@pytest.mark.parametrize(
    (
        "scenario",
        "expected_depth",
        "expected_target",
        "expected_scope",
        "expected_rollbacks",
        "expected_escalations",
    ),
    [
        (
            RecoveryScenario.ISOLATED,
            "SINGLE_CAPABILITY",
            "SHADOW",
            {"treasury-agent:payment.release"},
            1,
            0,
        ),
        (
            RecoveryScenario.CORRELATED,
            "DOWNSTREAM_CHAIN",
            "SHADOW",
            {
                "invoice-agent:invoice.approve",
                "treasury-agent:payment.release",
            },
            2,
            0,
        ),
        (
            RecoveryScenario.UPSTREAM_COMPROMISE,
            "DOWNSTREAM_CHAIN",
            "OBSERVE",
            {
                "vendor-intelligence-agent:vendor.onboard",
                "invoice-agent:invoice.approve",
                "treasury-agent:payment.release",
            },
            3,
            1,
        ),
        (
            RecoveryScenario.POLICY_DRIFT,
            "FLEET",
            "OBSERVE",
            {
                "vendor-intelligence-agent:vendor.onboard",
                "invoice-agent:invoice.approve",
                "treasury-agent:payment.release",
            },
            3,
            1,
        ),
    ],
)
def test_context_changes_demotion_depth_and_recovery_scope(
    scenario: RecoveryScenario,
    expected_depth: str,
    expected_target: str,
    expected_scope: set[str],
    expected_rollbacks: int,
    expected_escalations: int,
) -> None:
    fleet = ProcureToPayFleet()
    recovery = FleetRecoveryOrchestrator(fleet)

    result = recovery.run(
        tenant_id="tenant-a",
        case_id=f"case-{scenario.value}",
        scenario=scenario,
    )

    assert result["proposal"]["demotion_depth"] == expected_depth
    assert result["proposal"]["target_level"] == expected_target
    assert set(result["proposal"]["affected_capability_keys"]) == expected_scope
    assert len(result["rollback_results"]) == expected_rollbacks
    assert len(result["escalation_action_ids"]) == expected_escalations
    assert result["freeze_preceded_supervision"] is True
    assert result["memory_retrieval_verified"] is True
    assert set(result["tool_categories"]) == {
        "ledger",
        "registry",
        "memory",
        "trace",
        "graph",
        "rollback",
        "escalation",
    }
    assert result["ledger_integrity"] is True


def test_upstream_recovery_applies_real_rollbacks_and_escalation() -> None:
    fleet = ProcureToPayFleet()
    result = FleetRecoveryOrchestrator(fleet).run(
        tenant_id="tenant-a",
        case_id="case-real-recovery",
        scenario=RecoveryScenario.UPSTREAM_COMPROMISE,
    )

    assert result["state_after"]["vendor"]["status"] == "SUSPENDED"
    assert result["state_after"]["invoice"]["status"] == "HELD"
    assert result["state_after"]["payment"]["status"] == "REVERSED_SANDBOX"
    assert len(result["rollback_results"]) == 3
    assert result["escalation_action_ids"] == ["bank-export-case-real-recovery"]
    assert len(result["escalation_event_ids"]) == 1
    assert len(fleet.ledger.find("SANDBOX_ROLLBACK_APPLIED")) == 3


def test_signed_incident_tampering_is_detected() -> None:
    recovery = FleetRecoveryOrchestrator(ProcureToPayFleet())
    context = recovery._context(RecoveryScenario.ISOLATED)
    incident = recovery._signed_incident("tenant-a", "case-a", context)

    assert recovery.verify_incident(incident) is True
    assert recovery.verify_incident(replace(incident, tenant_id="tenant-b")) is False


class MaliciousRecoveryReasoner:
    mode = "MALICIOUS_TEST_DOUBLE"

    def decide(self, incident, toolbox, guardrail: RecoveryGuardrail):
        return FleetRecoveryProposal(
            incident_id=incident.incident_id,
            demotion_depth=RecoveryDepth.SINGLE_CAPABILITY,
            target_level=AuthorityLevel.EXECUTE_FULL,
            affected_capability_keys=(incident.context.root_key,),
            rollback_action_ids=(),
            escalation_action_ids=(),
            narrative="Attempt to restore and amplify compromised authority without evidence.",
        )


def test_malicious_supervisor_cannot_amplify_or_change_authority() -> None:
    fleet = ProcureToPayFleet()
    recovery = FleetRecoveryOrchestrator(
        fleet,
        reasoner=MaliciousRecoveryReasoner(),
    )

    with pytest.raises(RecoveryPolicyError) as rejected:
        recovery.run(
            tenant_id="tenant-a",
            case_id="case-malicious",
            scenario=RecoveryScenario.UPSTREAM_COMPROMISE,
        )

    assert "AUTHORITY_AMPLIFICATION" in str(rejected.value)
    assert len(fleet.ledger.find("SUPERVISOR_PROPOSAL_REJECTED")) == 1
    assert len(fleet.ledger.find("FLEET_DEMOTION_APPLIED")) == 0
    assert len(fleet.ledger.find("SANDBOX_ROLLBACK_APPLIED")) == 0


def test_fleet_supervisor_agent_has_only_recovery_tools() -> None:
    recovery = FleetRecoveryOrchestrator(ProcureToPayFleet())
    fleet = recovery.fleet
    case = fleet.run_case(tenant_id="tenant-a", case_id="case-tools")
    context = recovery._context(RecoveryScenario.UPSTREAM_COMPROMISE)
    incident = recovery._signed_incident("tenant-a", "case-tools", context)
    toolbox = FleetRecoveryToolbox(
        ledger=fleet.ledger,
        incident=incident,
        registry=fleet.registry,
        graph=fleet.dependencies,
        memory_reader=recovery.memory_reader,
        actions=recovery._actions(
            case["case_id"], case["tenant_id"], RecoveryScenario.UPSTREAM_COMPROMISE
        ),
    )

    agent = build_fleet_supervisor_agent(toolbox)
    tool_names = {
        getattr(tool, "name", None) or getattr(tool, "__name__", "")
        for tool in agent.tools
    }
    assert tool_names == {
        "read_incident_ledger",
        "read_agent_registry",
        "read_supervisor_memory",
        "read_trace",
        "traverse_dependency_graph",
        "request_compensating_rollbacks",
        "file_irreversible_escalation",
    }
    assert all(
        forbidden not in name
        for name in tool_names
        for forbidden in ("promote", "grant", "credential", "passport", "policy_write")
    )


class PromptCaptureSessionService:
    async def create_session(self, **kwargs):
        return SimpleNamespace(id=kwargs["session_id"])


class PromptCaptureEvent:
    def __init__(self, output: dict) -> None:
        self.output = output
        self.content = None

    def is_final_response(self) -> bool:
        return False


class PromptCaptureRunner:
    def __init__(self, output: dict) -> None:
        self.session_service = PromptCaptureSessionService()
        self.output = output
        self.prompt = None

    async def run_async(self, **kwargs):
        self.prompt = json.loads(kwargs["new_message"].parts[0].text)
        yield PromptCaptureEvent(self.output)


def test_adk_prompt_exposes_every_enforced_authority_ceiling() -> None:
    recovery = FleetRecoveryOrchestrator(ProcureToPayFleet())
    fleet = recovery.fleet
    fleet.run_case(tenant_id="tenant-a", case_id="case-prompt-contract")
    context = recovery._context(RecoveryScenario.UPSTREAM_COMPROMISE)
    incident = recovery._signed_incident(
        "tenant-a", "case-prompt-contract", context
    )
    guardrail = recovery.policy.guardrail(context, fleet.dependencies)
    toolbox = FleetRecoveryToolbox(
        ledger=fleet.ledger,
        incident=incident,
        registry=fleet.registry,
        graph=fleet.dependencies,
        memory_reader=recovery.memory_reader,
        actions=recovery._actions(
            "case-prompt-contract",
            "tenant-a",
            RecoveryScenario.UPSTREAM_COMPROMISE,
        ),
    )
    runner = PromptCaptureRunner(
        {
            "demotion_depth": "DOWNSTREAM_CHAIN",
            "target_level": "OBSERVE",
            "affected_capability_keys": list(guardrail.required_scope),
            "rollback_action_ids": [
                action.action_id for action in toolbox.actions if action.reversible
            ],
            "escalation_action_ids": [
                action.action_id for action in toolbox.actions if not action.reversible
            ],
            "narrative": (
                "The signed safety ceiling requires OBSERVE while the upstream vendor "
                "and every transitive dependent capability are investigated and recovered."
            ),
        }
    )
    reasoner = AdkFleetRecoveryReasoner(runner_factory=lambda agent: runner)

    proposal = asyncio.run(reasoner.decide_async(incident, toolbox, guardrail))

    assert proposal.target_level is AuthorityLevel.OBSERVE
    assert runner.prompt["safety_ceiling"] == {
        "previous_authority": incident.previous_authority,
        "may_expand_authority": False,
        "maximum_permitted_target_level": "OBSERVE",
        "policy_will_validate_scope": True,
    }
