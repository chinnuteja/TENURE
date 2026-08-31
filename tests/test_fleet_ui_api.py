import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from tenure.api import STATIC_DIR, create_app
from tenure.scenario import TenureScenario


@pytest.mark.parametrize("path", ["/", "/proof", "/conformance", "/platform", "/limitations"])
def test_fleet_surfaces_exist(path):
    with TestClient(create_app(TenureScenario.in_memory())) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert "fleet.js" in response.text
    assert "Scope & limits" in response.text


def test_custom_amount_survives_recovery_and_remains_reversed():
    with TestClient(create_app(TenureScenario.in_memory())) as client:
        params = {"tenant_id": "ui-test", "amount": 24_999}
        case = client.post("/api/fleet/cases/ui-case", params=params)
        assert case.status_code == 200
        result = client.post("/api/recovery/cases/ui-case", params=params)
        assert result.status_code == 200
        assert result.json()["state_after"]["payment"]["status"] == "REVERSED_SANDBOX"
        assert result.json()["state_after"]["payment"]["amount"] == 24_999
        replay = client.post("/api/fleet/cases/ui-case", params=params)
        assert replay.json()["state"]["payment"]["status"] == "REVERSED_SANDBOX"
        denied = client.post("/api/fleet/cases/new-ui-case", params=params)
        assert denied.status_code == 403


@pytest.mark.parametrize("amount", [0, -1, 50_001, "1.5", "no"])
def test_amount_validation_precedes_any_mutation(amount):
    app = create_app(TenureScenario.in_memory())
    with TestClient(app) as client:
        for path in ("/api/fleet/cases/invalid", "/api/recovery/cases/invalid"):
            assert (
                client.post(path, params={"tenant_id": "invalid", "amount": amount}).status_code
                == 422
            )
    assert len(app.state.fleet.ledger.events) == 0


def test_proof_summary_matches_download_and_discloses_fixture_mode():
    with TestClient(create_app(TenureScenario.in_memory())) as client:
        report = client.get("/api/gauntlet").json()
        full = client.get("/static/gauntlet-report.json").json()
        health = client.get("/api/health").json()
    assert report["summary"] == full["summary"]
    assert "corpus" not in report and "results" not in report
    assert (
        hashlib.sha256(json.dumps(full["corpus"], sort_keys=True).encode()).hexdigest()
        == report["corpus_sha256"]
    )
    assert health["operating_mode"] == "DETERMINISTIC_FIXTURES"
    assert health["supervisor_mode"] == "LOCAL_DETERMINISTIC"
    assert health["fleet_persistence"] == "memory"
    assert (STATIC_DIR / "fleet.js").exists()


def test_failed_supervision_exposes_current_freezes_in_the_inspector_audit():
    class FailedReasoner:
        mode = "LOCAL_DETERMINISTIC"

        def decide(self, *args):
            raise RuntimeError("simulated provider failure")

    app = create_app(TenureScenario.in_memory())
    app.state.recovery.reasoner = FailedReasoner()
    with TestClient(app, raise_server_exceptions=False) as client:
        params = {"tenant_id": "failed-ui"}
        assert client.post("/api/fleet/cases/frozen-ui", params=params).status_code == 200
        assert client.post("/api/recovery/cases/frozen-ui", params=params).status_code == 500
        audit = client.get("/api/fleet/cases/frozen-ui/audit", params=params).json()
    assert len(audit["current_authority"]) == 3
    assert all(entry["freezes"] for entry in audit["current_authority"].values())
    assert audit["ledger_integrity"]
