from fastapi.testclient import TestClient

from app import app


def test_vercel_entrypoint_serves_the_complete_demo() -> None:
    client = TestClient(app)

    page = client.get("/")
    health = client.get("/api/health")

    assert page.status_code == 200
    assert "Agents should earn the" in page.text
    assert health.status_code == 200
    assert health.json()["supervisor_mode"] == "LOCAL_DETERMINISTIC"
