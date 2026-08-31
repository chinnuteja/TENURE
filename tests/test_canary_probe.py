"""Validate the deployment verifier without Cloud or Gemini calls."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location(
        "tenure_canary_probe", Path(__file__).parents[1] / "deploy" / "verify_repair_canary.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_refuses_other_hosts_before_reading_credentials(probe, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda _: pytest.fail("credential lookup"))
    with pytest.raises(ValueError, match="restricted"):
        probe.verify("https://not-our-service.example")


def test_probe_checks_revision_and_never_records_token(probe, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda _: "gcloud.cmd")
    monkeypatch.setattr(
        probe.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="test-secret-token"),
    )
    monkeypatch.setattr(probe.requests, "get", lambda *a, **kw: SimpleNamespace(status_code=403))
    calls = []

    def request(method, url, **kwargs):
        calls.append(url)
        assert kwargs["headers"]["Authorization"] == "Bearer test-secret-token"
        body = (
            {"mode": "GOOGLE_CLOUD_LIVE"} if url.endswith("/health")
            else {"cloud_run": {"revision": "wrong-revision"}, "project_id": probe.PROJECT}
        )
        return SimpleNamespace(status_code=200, json=lambda: body)

    monkeypatch.setattr(probe.requests, "request", request)
    result = probe.verify(probe.CANARY)
    assert result["status"] == "FAIL"
    assert len(calls) == 2  # Fail before any business mutation or model call.
    assert "test-secret-token" not in str(result)
