from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai.errors import ClientError

from tenure.api import create_app
from tenure.native_proof import (
    LOCATION,
    MEMORY,
    ORGANIZATION,
    PROJECT,
    PROJECT_NUMBER,
    RUNTIMES,
    NativeIdentityProof,
    ProofBusy,
    ProofUnavailable,
)
from tenure.scenario import TenureScenario


@pytest.fixture
def runtime_module():
    spec = importlib.util.spec_from_file_location(
        "runtime_agents_proof_test",
        Path(__file__).parents[1] / "deploy/runtime_agents.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def native_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    monkeypatch.setenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", RUNTIMES["owner"])
    monkeypatch.delenv("TENURE_NATIVE_PROJECT", raising=False)
    monkeypatch.delenv("TENURE_NATIVE_RUNTIME_ID", raising=False)
    monkeypatch.delenv("GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES", raising=False)


def native_client(module, monkeypatch, get):
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(agent_engines=SimpleNamespace(memories=SimpleNamespace(get=get)))

    monkeypatch.setattr(module.agentplatform, "Client", factory)
    return calls


def test_native_probe_reads_only_pinned_memory_and_never_returns_content(
    runtime_module,
    native_env,
    monkeypatch,
):
    targets = []

    def get(**kwargs):
        targets.append(kwargs)
        return SimpleNamespace(name=MEMORY, fact="sensitive-should-not-return")

    calls = native_client(runtime_module, monkeypatch, get)
    result = runtime_module.native_identity_probe("a" * 32)
    assert targets == [{"name": MEMORY}]
    assert calls[0]["http_options"]["retry_options"]["attempts"] == 1
    assert result["outcome"] == "ALLOW"
    assert result["http_status"] == 200
    assert result["model_calls"] == 0
    assert "sensitive-should-not-return" not in str(result)


@pytest.mark.parametrize(
    "code,reason,outcome",
    [
        (403, "IAM_PERMISSION_DENIED", "DENY"),
        (403, "SERVICE_DISABLED", "ERROR"),
        (401, "IAM_PERMISSION_DENIED", "ERROR"),
        (404, "NOT_FOUND", "ERROR"),
        (429, "RATE_LIMIT", "ERROR"),
        (500, "SERVER_ERROR", "ERROR"),
    ],
)
def test_only_actual_permission_denial_counts_as_denial(
    runtime_module,
    native_env,
    monkeypatch,
    code,
    reason,
    outcome,
):
    def get(**kwargs):
        raise ClientError(
            code,
            {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "message": "secret error context",
                    "details": [
                        {"reason": reason, "metadata": {"permission": "aiplatform.memories.get"}}
                    ],
                }
            },
        )

    native_client(runtime_module, monkeypatch, get)
    result = runtime_module.native_identity_probe("a" * 32)
    assert result["outcome"] == outcome
    assert "secret error context" not in str(result)


@pytest.mark.parametrize(
    "variable,value",
    [
        ("GOOGLE_CLOUD_PROJECT", "other-project"),
        ("GOOGLE_CLOUD_AGENT_ENGINE_ID", "unknown-runtime"),
        ("GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES", "false"),
    ],
)
def test_native_probe_fails_closed_before_network(
    runtime_module,
    native_env,
    monkeypatch,
    variable,
    value,
):
    calls = native_client(runtime_module, monkeypatch, lambda **_: pytest.fail("network"))
    monkeypatch.setenv(variable, value)
    with pytest.raises(RuntimeError):
        runtime_module.native_identity_probe("b" * 32)
    assert calls == []


def test_probe_is_non_model_rpc_not_an_agent_tool(runtime_module):
    for app in (runtime_module.build_subject_app(), runtime_module.build_supervisor_app()):
        assert "identity_probe" in app.register_operations()[""]
        assert "async_stream_query" in app.register_operations()["async_stream"]
    assert "identity_probe" not in runtime_module.build_vendor_app().register_operations()[""]


def test_update_config_pins_runtime_identity_without_token_sharing(runtime_module):
    config = runtime_module.deployment_config(
        display_name="TENURE Supervisor Agent",
        description="proof",
        staging_bucket="gs://tenure-agent-runtime-585333584620",
        project=PROJECT,
        native_runtime_id=RUNTIMES["owner"],
    )
    assert config["env_vars"]["TENURE_NATIVE_PROJECT"] == PROJECT
    assert config["env_vars"]["TENURE_NATIVE_RUNTIME_ID"] == RUNTIMES["owner"]
    assert (
        config["env_vars"]["GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES"]
        == "TRUE"
    )


class FakeRemote:
    def __init__(self, runtime_id, *, mutate=None):
        self.runtime_id = runtime_id
        self.mutate = mutate
        self.calls = 0
        name = f"projects/{PROJECT_NUMBER}/locations/{LOCATION}/reasoningEngines/{runtime_id}"
        self.api_resource = SimpleNamespace(
            spec=SimpleNamespace(
                identity_type="AGENT_IDENTITY",
                effective_identity=f"agents.global.org-{ORGANIZATION}.system.id.goog/resources/aiplatform/{name}",
            )
        )

    def identity_probe(self, nonce):
        self.calls += 1
        owner = self.runtime_id == RUNTIMES["owner"]
        result = {
            "schema": "tenure.native-identity/v1",
            "nonce": nonce,
            "runtime_id": self.runtime_id,
            "resource": MEMORY,
            "permission": "aiplatform.memories.get",
            "model_calls": 0,
            "content_returned": False,
            "observed_at": datetime.now(UTC).isoformat(),
            "outcome": "ALLOW" if owner else "DENY",
            "http_status": 200 if owner else 403,
            "permission_denied_verified": not owner,
            "fact": "must-not-escape",
        }
        if self.mutate:
            self.mutate(result)
        return {"output": result}


def make_proof(mutate=None):
    remotes = {
        runtime_id: FakeRemote(runtime_id, mutate=mutate) for runtime_id in RUNTIMES.values()
    }
    client = SimpleNamespace(
        agent_engines=SimpleNamespace(
            get=lambda name: remotes[name.split("/")[-1]],
        )
    )
    return NativeIdentityProof(enabled=True, client_factory=lambda: client), remotes


def test_comparison_binds_both_identities_fresh_nonce_and_same_target():
    proof, remotes = make_proof()
    result = proof.run()
    assert result["status"] == "PASS"
    assert [check["outcome"] for check in result["checks"]] == ["ALLOW", "DENY"]
    assert all(remote.calls == 1 for remote in remotes.values())
    assert "must-not-escape" not in str(result)
    result["status"] = "changed"
    assert proof.status()["last_result"]["status"] == "PASS"
    with pytest.raises(ProofBusy):
        proof.run()


@pytest.mark.parametrize(
    "field,value",
    [
        ("nonce", "replayed-nonce"),
        ("runtime_id", "another-runtime"),
        ("resource", "another-memory"),
        ("model_calls", 1),
        ("content_returned", True),
        ("observed_at", (datetime.now(UTC) - timedelta(days=1)).isoformat()),
    ],
)
def test_replay_or_wrong_target_cannot_pass(field, value):
    proof, _ = make_proof(lambda result: result.update({field: value}))
    assert proof.run()["status"] == "INCONCLUSIVE"


def test_missing_method_or_wrong_identity_never_falls_back_to_admin_read():
    proof, remotes = make_proof()
    remotes[RUNTIMES["other"]].identity_probe = None
    report = proof.run()
    assert report["error_code"] == "PROBE_METHOD_NOT_DEPLOYED"
    assert not any(remote.calls for remote in remotes.values())
    proof, remotes = make_proof()
    remotes[RUNTIMES["other"]].api_resource.spec.effective_identity = "service-account"
    assert proof.run()["error_code"] == "NATIVE_IDENTITY_MISMATCH"
    assert not any(remote.calls for remote in remotes.values())


def test_network_error_is_not_a_successful_denial():
    def fail():
        raise RuntimeError("private request context")

    proof = NativeIdentityProof(enabled=True, client_factory=fail)
    report = proof.run()
    assert report["status"] == "INCONCLUSIVE"
    assert report["error_code"] == "NATIVE_REQUEST_FAILED"
    assert "private request" not in str(report)


def test_local_api_reports_unavailable_without_network():
    with TestClient(create_app(TenureScenario.in_memory())) as client:
        status = client.get("/api/proofs/identity").json()
        assert status["status"] == "UNAVAILABLE"
        assert status["last_result"] is None
        assert client.post("/api/proofs/identity").status_code == 409
    with pytest.raises(ProofUnavailable):
        NativeIdentityProof(enabled=False, client_factory=lambda: pytest.fail("network")).run()


def test_api_proof_limit_returns_retry_after():
    app = create_app(TenureScenario.in_memory())
    prepared, _ = make_proof()
    app.state.identity_proof.enabled = True
    app.state.identity_proof.client_factory = prepared.client_factory
    with TestClient(app) as client:
        assert client.post("/api/proofs/identity").json()["status"] == "PASS"
        limited = client.post("/api/proofs/identity")
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "60"
