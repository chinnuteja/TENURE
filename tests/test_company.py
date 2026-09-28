"""Company OS pack: one agent, separate authority per connected tool."""

import pytest
from fastapi.testclient import TestClient

from tenure.api import create_app
from tenure.company import (
    AGENT_ID,
    INJECTED_MESSAGE,
    OPERATING_KEYS,
    STEPS,
    CompanyFleet,
    CompanyRecoveryOrchestrator,
    CompanyScenario,
    LocalHeuristicPromptScreen,
)
from tenure.domain import AuthorityLevel
from tenure.recovery import FleetRecoveryPolicy
from tenure.scenario import TenureScenario


def test_case_earns_four_capabilities_and_refuses_the_rest():
    case = CompanyFleet().run_case(tenant_id="acme", case_id="sso-1", amount=18_400)
    assert case["complete"] is True
    assert len(case["passports"]) == 4
    assert [receipt["capability"] for receipt in case["receipts"]] == [
        step.capability for step in STEPS
    ]
    assert case["state"]["issue"]["status"] == "OPEN"
    assert case["state"]["pull_request"]["status"] == "MERGED"
    assert case["state"]["deal"]["status"] == "CLOSED_WON"
    assert case["state"]["invoice"]["status"] == "SENT_SANDBOX"
    assert case["state"]["invoice"]["detail"]["amount_usd"] == 18_400
    decisions = {
        probe["capability"]: probe["gateway_decision"] for probe in case["unearned_attempts"]
    }
    assert decisions["stripe.issue_refund"] == "DENY_LEVEL"
    assert decisions["github.change_branch_protection"] == "DENY_LEVEL"
    assert decisions["stripe.create_invoice"] == "DENY_SCOPE"
    assert case["ledger_integrity"] is True


def test_replay_does_not_mint_new_passports():
    fleet = CompanyFleet()
    first = fleet.run_case(tenant_id="acme", case_id="sso-1", amount=18_400)
    again = fleet.run_case(tenant_id="acme", case_id="sso-1", amount=18_400)
    assert [item["passport_id"] for item in again["passports"]] == [
        item["passport_id"] for item in first["passports"]
    ]
    assert len(fleet.ledger.find("CAPABILITY_PASSPORT_ISSUED", case_id="sso-1")) == 4


def test_tenant_boundary_hides_another_customers_case():
    fleet = CompanyFleet()
    fleet.run_case(tenant_id="acme", case_id="sso-1", amount=18_400)
    with pytest.raises(PermissionError):
        fleet.audit_case("other-customer", "sso-1")


@pytest.mark.parametrize(
    ("scenario", "depth", "level", "still_executable"),
    [
        (CompanyScenario.INJECTED_SLACK_INSTRUCTION, "DOWNSTREAM_CHAIN", "OBSERVE", set()),
        (CompanyScenario.BAD_MERGE, "SINGLE_CAPABILITY", "SHADOW",
         {"linear.create_issue", "attio.update_deal", "stripe.create_invoice"}),
        (CompanyScenario.BILLING_MISMATCH, "DOWNSTREAM_CHAIN", "SHADOW",
         {"linear.create_issue", "github.merge_pr"}),
        (CompanyScenario.PRICING_POLICY_CHANGED, "FLEET", "OBSERVE", set()),
    ],
)
def test_break_freezes_before_the_supervisor_and_demotes_only_dependents(
    scenario, depth, level, still_executable
):
    result = CompanyRecoveryOrchestrator(CompanyFleet()).run(
        tenant_id="acme", case_id="sso-1", scenario=scenario, amount=22_000,
    )
    assert result["freeze_preceded_supervision"] is True
    assert result["proposal"]["demotion_depth"] == depth
    assert result["proposal"]["target_level"] == level
    assert result["ledger_integrity"] is True
    assert result["escalations"]
    executable = {
        row["capability"]
        for row in result["authority"]
        if row["earned"] and row["level"] == AuthorityLevel.EXECUTE_BOUNDED.name
    }
    assert executable == still_executable
    assert set(result["proposal"]["affected_capability_keys"]) <= set(OPERATING_KEYS)
    assert AGENT_ID in result["proposal"]["affected_capability_keys"][0]


def test_injected_slack_undoes_sandbox_and_escalates_what_the_customer_saw():
    result = CompanyRecoveryOrchestrator(CompanyFleet()).run(
        tenant_id="acme", case_id="sso-1", scenario=CompanyScenario.INJECTED_SLACK_INSTRUCTION,
    )
    assert result["state_after"]["issue"]["status"] == "CANCELED"
    assert result["state_after"]["pull_request"]["status"] == "REVERTED"
    assert result["state_after"]["deal"]["status"] == "REOPENED"
    assert result["state_after"]["invoice"]["status"] == "VOIDED"
    descriptions = {item["description"] for item in result["escalations"]}
    assert "SSO build deployed to production" in descriptions
    assert "Stripe emailed the invoice to Acme" in descriptions


def test_local_heuristic_screen_actually_evaluates_the_text():
    screen = LocalHeuristicPromptScreen()
    blocked = screen.sanitize_user_prompt(INJECTED_MESSAGE)
    assert blocked.allowed is False
    assert blocked.filter_match_state == "MATCH_FOUND"
    assert len(blocked.matched_phrases) >= 2

    clean = screen.sanitize_user_prompt(
        "Acme confirmed the SSO scope over email and approved the standard rollout."
    )
    assert clean.allowed is True
    assert clean.matched_phrases == ()


def test_injected_scenario_screens_the_real_message_before_freezing():
    orchestrator = CompanyRecoveryOrchestrator(CompanyFleet())
    result = orchestrator.run(
        tenant_id="acme", case_id="sso-1",
        scenario=CompanyScenario.INJECTED_SLACK_INSTRUCTION, amount=18_400,
    )
    screen = result["prompt_screen"]
    assert screen["message"] == INJECTED_MESSAGE
    assert screen["allowed"] is False
    assert len(screen["matched_phrases"]) >= 2
    assert screen["provider"] == "local_heuristic_v1"
    events = orchestrator.ledger.find("PROMPT_INJECTION_SCREENED", case_id="sso-1")
    assert len(events) == 1


def test_other_scenarios_do_not_run_the_prompt_screen():
    result = CompanyRecoveryOrchestrator(CompanyFleet()).run(
        tenant_id="acme", case_id="sso-1", scenario=CompanyScenario.BAD_MERGE, amount=18_400,
    )
    assert "prompt_screen" not in result


def test_tool_trace_shows_the_full_investigation_in_order():
    result = CompanyRecoveryOrchestrator(CompanyFleet()).run(
        tenant_id="acme", case_id="sso-1",
        scenario=CompanyScenario.INJECTED_SLACK_INSTRUCTION, amount=18_400,
    )
    trace = result["tool_trace"]
    assert [item["sequence"] for item in trace] == sorted(item["sequence"] for item in trace)
    assert {item["category"] for item in trace} == FleetRecoveryPolicy.REQUIRED_TOOL_CATEGORIES
    tools = [item["tool"] for item in trace]
    assert "request_compensating_rollbacks" in tools
    assert "file_irreversible_escalation" in tools


def test_company_api_runs_and_contains():
    with TestClient(create_app(TenureScenario.in_memory())) as client:
        page = client.get("/company")
        assert page.status_code == 200
        assert "company.js" in page.text
        assert "Attio" in page.text
        assert 'id="promptScreen"' in page.text
        assert 'id="toolTrace"' in page.text
        assert 'id="promptDisclosure"' in page.text
        params = {"tenant_id": "ui-acme", "amount": 9_500}
        case = client.post("/api/company/cases/ui-sso", params=params)
        assert case.status_code == 200
        body = case.json()
        assert body["complete"] is True
        assert len(body["unearned_attempts"]) == 3
        broken = client.post(
            "/api/company/recovery/cases/ui-sso",
            params={**params, "scenario": "injected_slack_instruction"},
        )
        assert broken.status_code == 200
        payload = broken.json()
        assert payload["freeze_preceded_supervision"] is True
        assert payload["prompt_screen"]["allowed"] is False
        assert len(payload["tool_trace"]) >= len(FleetRecoveryPolicy.REQUIRED_TOOL_CATEGORIES)
        assert client.post(
            "/api/company/recovery/cases/other",
            params={**params, "scenario": "not-a-scenario"},
        ).status_code == 422
        assert client.get(
            "/api/company/cases/ui-sso/audit", params={"tenant_id": "someone-else"}
        ).status_code == 403
