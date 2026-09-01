import subprocess
import sys
import tempfile
from pathlib import Path

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


def test_vercel_entrypoint_overrides_stale_cloud_environment() -> None:
    root = Path(__file__).resolve().parents[1]
    script = """
import os
os.environ["VERCEL"] = "1"
os.environ["TENURE_RUNTIME"] = "cloud"
os.environ["TENURE_SUPERVISOR_PROVIDER"] = "gemini"
os.environ["TENURE_DATA_DIR"] = "/read-only/path"
import app
print(os.environ["TENURE_RUNTIME"])
print(os.environ["TENURE_SUPERVISOR_PROVIDER"])
print(os.environ["TENURE_DATA_DIR"])
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == [
        "local",
        "fixture",
        str(Path(tempfile.gettempdir()) / "tenure" / "runs"),
    ]
