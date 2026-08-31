from __future__ import annotations

from fastapi.testclient import TestClient

from tenure.api import create_app
from tenure.scenario import TenureScenario


def make_client() -> TestClient:
    return TestClient(create_app(TenureScenario.in_memory()))


def test_health_and_dashboard() -> None:
    with make_client() as client:
        health = client.get("/api/health")
        readiness = client.get("/api/cloud-readiness")
        platform = client.get("/api/platform")
        dashboard = client.get("/")
    assert health.status_code == 200
    assert health.json()["ledger_integrity"] is True
    assert readiness.json()["code_ready"] is True
    assert readiness.json()["billing_verified"] is False
    assert platform.status_code == 200
    assert platform.json()["cloud_proof_verified"] is False
    assert platform.json()["cloud_run"]["service"] is None
    assert platform.json()["agent_gateway"]["resource"] is None
    assert set(platform.json()["fleet"]) == {
        "vendor",
        "invoice",
        "treasury",
        "supervisor",
    }
    assert dashboard.status_code == 200
    assert "TENURE" in dashboard.text


def test_full_scenario_api_proves_expected_outcome() -> None:
    with make_client() as client:
        response = client.post("/api/scenario/run")
        ledger = client.get("/api/ledger")
        receipts = client.get("/api/receipts")
        evidence = client.get("/api/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["complete"] is True
    assert body["grant"]["level"] == "OBSERVE"
    assert body["metrics"]["rawr_blocks"] == 1
    assert body["metrics"]["unsafe_actions_executed"] == 0
    assert body["metrics"]["affected_actions"] == 6
    assert body["metrics"]["rollbacks_requested"] == 4
    assert body["metrics"]["escalations_filed"] == 2
    assert ledger.json()["integrity"] is True
    assert len(receipts.json()["receipts"]) == 3
    assert evidence.json()["cloud_claim"] is False
    assert all(evidence.json()["safety_invariants"].values())


def test_reset_and_advance_are_repeatable() -> None:
    with make_client() as client:
        client.post("/api/scenario/run")
        reset = client.post("/api/scenario/reset").json()
        first = client.post("/api/scenario/advance").json()

    assert reset["step"] == 0
    assert reset["grant"]["level"] == "SHADOW"
    assert first["step"] == 1
    assert first["metrics"]["verified_tasks"] == 1


def test_deployment_truth_is_explicitly_injected() -> None:
    scenario = TenureScenario(
        mode="GOOGLE_CLOUD_LIVE",
        cloud_truth="Verified cloud composition.",
        cloud_claim=True,
    )

    snapshot = scenario.snapshot()
    evidence = scenario.evidence_report()

    assert snapshot["mode"] == "GOOGLE_CLOUD_LIVE"
    assert snapshot["cloud_truth"] == "Verified cloud composition."
    assert evidence["cloud_claim"] is True
    assert scenario.ledger.find("SCENARIO_STARTED")[0].payload["mode"] == (
        "GOOGLE_CLOUD_LIVE"
    )


def test_fleet_case_is_discoverable_and_auditable_through_api() -> None:
    with make_client() as client:
        registry = client.get("/api/fleet/registry")
        run = client.post(
            "/api/fleet/cases/case-api", params={"tenant_id": "tenant-api"}
        )
        audit = client.get(
            "/api/fleet/cases/case-api/audit", params={"tenant_id": "tenant-api"}
        )
        denied = client.get(
            "/api/fleet/cases/case-api/audit", params={"tenant_id": "tenant-other"}
        )
        missing = client.get(
            "/api/fleet/cases/missing/audit", params={"tenant_id": "tenant-api"}
        )
        local_reconstruct = client.post("/api/fleet/proof/reconstruct")

    assert registry.status_code == 200
    assert len(registry.json()["agents"]) == 4
    assert len(registry.json()["dependency_edges"]) == 2
    assert run.status_code == 200
    assert run.json()["state"]["payment"]["status"] == "RELEASED_SANDBOX"
    assert audit.status_code == 200
    assert audit.json()["receipt_count"] == 3
    assert denied.status_code == 403
    assert missing.status_code == 404
    assert local_reconstruct.status_code == 409


def test_authority_proof_exposes_equal_accuracy_and_counterfactual_denial() -> None:
    with make_client() as client:
        response = client.post(
            "/api/authority/proof",
            params={"tenant_id": "tenant-api", "stress_ceiling": 250_000},
        )
        invalid = client.post(
            "/api/authority/proof",
            params={"tenant_id": "tenant-api", "stress_ceiling": 10_000_001},
        )

    assert response.status_code == 200
    proof = response.json()
    assert proof["proof"] == "SAME_OUTCOME_DIFFERENT_AUTHORITY"
    assert proof["equal_outcome_accuracy"] is True
    assert proof["different_authority"] is True
    assert proof["grounded_agent"]["applied_level"] == "EXECUTE_BOUNDED"
    assert proof["rawr_agent"]["applied_level"] == "SHADOW"
    assert proof["stress_promotion"]["decision"] == "DENY_PROMOTION"
    assert proof["stress_promotion"]["applied_ceiling"] == 50_000
    assert proof["stress_replay"]["within_budget"] is False
    assert proof["model_calls"] == 0
    assert invalid.status_code == 422


def test_recovery_api_freezes_before_supervision_and_rolls_back() -> None:
    with make_client() as client:
        response = client.post(
            "/api/recovery/cases/case-api-recovery",
            params={
                "tenant_id": "tenant-api",
                "scenario": "upstream_compromise",
            },
        )
        invalid = client.post(
            "/api/recovery/cases/case-api-invalid",
            params={"tenant_id": "tenant-api", "scenario": "unknown"},
        )

    assert response.status_code == 200
    result = response.json()
    assert result["reasoner_mode"] == "LOCAL_DETERMINISTIC"
    assert result["freeze_preceded_supervision"] is True
    assert result["proposal"]["demotion_depth"] == "DOWNSTREAM_CHAIN"
    assert result["state_after"]["vendor"]["status"] == "SUSPENDED"
    assert result["state_after"]["invoice"]["status"] == "HELD"
    assert result["state_after"]["payment"]["status"] == "REVERSED_SANDBOX"
    assert invalid.status_code == 422
