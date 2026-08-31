"""Bounded, read-only native identity comparison; no local success fixtures."""

from __future__ import annotations

import os
from copy import deepcopy
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from uuid import uuid4

PROJECT = "project-ceca895d-33b0-44b9-b5a"
PROJECT_NUMBER = "585333584620"
LOCATION = "us-central1"
ORGANIZATION = "364779231455"
RUNTIMES = {"owner": "1415402967703486464", "other": "8053708818447597568"}
MEMORY = (
    f"projects/{PROJECT_NUMBER}/locations/{LOCATION}/reasoningEngines/"
    f"{RUNTIMES['owner']}/memories/8766834811634450432"
)


class ProofUnavailable(RuntimeError):
    pass


class ProofBusy(RuntimeError):
    pass


class NativeIdentityProof:
    def __init__(self, *, enabled: bool | None = None, client_factory=None, clock=monotonic):
        self.enabled = (
            enabled
            if enabled is not None
            else (
                os.getenv("TENURE_RUNTIME") == "cloud"
                and os.getenv("GOOGLE_CLOUD_PROJECT") == PROJECT
                and os.getenv("TENURE_NATIVE_PROOF_ENABLED", "false").lower() == "true"
            )
        )
        self.client_factory = client_factory or self._client
        self.clock = clock
        self.lock = Lock()
        self.last = None
        self.next_run = 0.0
        self.runs = 0

    @staticmethod
    def _client():
        import agentplatform

        return agentplatform.Client(
            project=PROJECT,
            location=LOCATION,
            http_options={"timeout": 30000, "retry_options": {"attempts": 1}},
        )

    def status(self) -> dict:
        with self.lock:
            return {
                "enabled": self.enabled,
                "status": "ENABLED_UNVERIFIED" if self.enabled else "UNAVAILABLE",
                "message": (
                    "Run two native runtime calls to inspect actual resource authorization."
                    if self.enabled
                    else (
                        "Requires the updated native runtimes and explicit cloud proof "
                        "enablement. Local fixtures are never substituted."
                    )
                ),
                "resource": MEMORY,
                "model_calls": 0,
                "cooldown_seconds": max(0, int(self.next_run - self.clock() + 0.999)),
                "last_result": deepcopy(self.last),
                "limit": "One pair per minute; ten pairs per process. Not a durable spend cap.",
            }

    def run(self) -> dict:
        if not self.enabled:
            raise ProofUnavailable("Native proof is not enabled; no cloud calls were made")
        if not self.lock.acquire(blocking=False):
            raise ProofBusy("A native proof is already running")
        try:
            if self.clock() < self.next_run or self.runs >= 10:
                raise ProofBusy("Proof cooling down or process limit reached")
            self.runs += 1
            self.next_run = self.clock() + 60
            nonce = uuid4().hex
            report = {
                "proof_id": nonce,
                "status": "INCONCLUSIVE",
                "schema": "tenure.identity-pair/v1",
                "source": "NATIVE_AGENT_RUNTIME",
                "resource": MEMORY,
                "checks": [],
                "model_calls": 0,
                "started_at": datetime.now(UTC).isoformat(),
            }
            try:
                client = self.client_factory()
                remotes = {}
                # Verify BOTH identities and method registrations before either probe.
                for role, runtime_id in RUNTIMES.items():
                    name = (
                        f"projects/{PROJECT_NUMBER}/locations/{LOCATION}/"
                        f"reasoningEngines/{runtime_id}"
                    )
                    remote = client.agent_engines.get(name=name)
                    spec = remote.api_resource.spec
                    identity = str(spec.effective_identity).removeprefix("principal://")
                    expected = (
                        f"agents.global.org-{ORGANIZATION}.system.id.goog/resources/"
                        f"aiplatform/{name}"
                    )
                    if identity != expected or not str(spec.identity_type).endswith(
                        "AGENT_IDENTITY"
                    ):
                        raise ProofUnavailable("NATIVE_IDENTITY_MISMATCH")
                    if not callable(getattr(remote, "identity_probe", None)):
                        raise ProofUnavailable("PROBE_METHOD_NOT_DEPLOYED")
                    remotes[role] = (remote, identity)
                for role in ("owner", "other"):
                    remote, identity = remotes[role]
                    response = remote.identity_probe(nonce=nonce)
                    # SDK wraps a :query result in output; accept its unwrapped
                    # form too. Strict field checks bind result to this fresh call.
                    result = response.get("output", response)
                    if (
                        result.get("schema") != "tenure.native-identity/v1"
                        or result.get("nonce") != nonce
                        or result.get("runtime_id") != RUNTIMES[role]
                        or result.get("resource") != MEMORY
                        or result.get("permission") != "aiplatform.memories.get"
                        or result.get("model_calls") != 0
                        or result.get("content_returned") is not False
                    ):
                        raise ProofUnavailable("PROBE_RESULT_BINDING_FAILED")
                    observed = datetime.fromisoformat(result["observed_at"])
                    age = (datetime.now(UTC) - observed).total_seconds()
                    if not -5 <= age <= 120:
                        raise ProofUnavailable("STALE_PROBE_RESULT")
                    # Export only selected non-sensitive fields, never raw errors/content.
                    report["checks"].append(
                        {
                            "role": role,
                            "runtime_id": RUNTIMES[role],
                            "identity": identity,
                            "outcome": result.get("outcome"),
                            "http_status": result.get("http_status"),
                            "permission_denied_verified": result.get("permission_denied_verified")
                            is True,
                            "observed_at": result["observed_at"],
                        }
                    )
                owner, other = report["checks"]
                passed = (
                    owner["outcome"] == "ALLOW"
                    and owner["http_status"] == 200
                    and other["outcome"] == "DENY"
                    and other["http_status"] == 403
                    and other["permission_denied_verified"]
                )
                report["status"] = "PASS" if passed else "FAIL"
            except ProofUnavailable as exc:
                report["error_code"] = str(exc)
            except Exception:
                # SDK exception messages may include request context. Do not echo them.
                report["error_code"] = "NATIVE_REQUEST_FAILED"
            report["completed_at"] = datetime.now(UTC).isoformat()
            self.last = report
            return deepcopy(report)
        finally:
            self.lock.release()
