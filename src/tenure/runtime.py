"""Runtime composition that selects local or Google adapters by configuration."""

from __future__ import annotations

import os
from collections.abc import Callable
from enum import StrEnum
from importlib.util import find_spec
from typing import Any
from uuid import uuid4

from tenure.adk_supervisor import AdkSupervisorReasoner
from tenure.cloud_adapters import (
    AgentMemoryBankReader,
    CloudSettings,
    FirestoreLedger,
    FirestoreProcureToPaySandbox,
    SecretManagerProvider,
    SignedIncidentPublisher,
)
from tenure.fleet import ProcureToPayFleet
from tenure.ledger import TrustLedger
from tenure.model_armor import ModelArmorGateway
from tenure.observability import TenureTracing, build_cloud_tracer
from tenure.recovery import (
    AdkFleetRecoveryReasoner,
    FleetRecoveryOrchestrator,
    VerifiedMemorySnapshot,
)
from tenure.scenario import TenureScenario, persistent_local_ledger


class RuntimeMode(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"


class SupervisorProvider(StrEnum):
    FIXTURE = "fixture"
    GEMINI = "gemini"


REQUIRED_CLOUD_ENV = (
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "TENURE_MODEL_ARMOR_TEMPLATE",
)


def _module_available(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def cloud_readiness() -> dict[str, Any]:
    environment = {
        name: bool(os.getenv(name, "").strip()) for name in REQUIRED_CLOUD_ENV
    }
    dependencies = {
        "google_adk": _module_available("google.adk"),
        "firestore": _module_available("google.cloud.firestore"),
        "pubsub": _module_available("google.cloud.pubsub_v1"),
        "secret_manager": _module_available("google.cloud.secretmanager"),
        "otlp_exporter": _module_available(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
        ),
    }
    billing_verified = (
        os.getenv("TENURE_CLOUD_PROOF_VERIFIED", "").strip().lower() == "true"
    )
    return {
        "selected_runtime": selected_runtime().value,
        "environment": environment,
        "dependencies": dependencies,
        "code_ready": True,
        "live_ready": all(environment.values()) and all(dependencies.values()),
        "billing_verified": billing_verified,
        "note": (
            "Live Google integration proof is recorded in /api/platform."
            if billing_verified
            else "Billing and live API verification are external deployment gates."
        ),
    }


def selected_runtime() -> RuntimeMode:
    configured = os.getenv("TENURE_RUNTIME", RuntimeMode.LOCAL.value).strip().lower()
    try:
        return RuntimeMode(configured)
    except ValueError as exc:
        raise RuntimeError(f"unsupported TENURE_RUNTIME: {configured}") from exc


def selected_supervisor_provider() -> SupervisorProvider:
    configured = os.getenv(
        "TENURE_SUPERVISOR_PROVIDER", SupervisorProvider.FIXTURE.value
    ).strip().lower()
    try:
        provider = SupervisorProvider(configured)
    except ValueError as exc:
        raise RuntimeError(
            f"unsupported TENURE_SUPERVISOR_PROVIDER: {configured}"
        ) from exc
    if provider is SupervisorProvider.GEMINI and not (
        os.getenv("GOOGLE_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    ):
        raise RuntimeError(
            "GOOGLE_API_KEY or GEMINI_API_KEY is required for the local Gemini Supervisor"
        )
    return provider


def configured_ledger_factory() -> Callable[[], TrustLedger]:
    if selected_runtime() is RuntimeMode.LOCAL:
        return persistent_local_ledger

    settings = CloudSettings.from_env()

    def make_cloud_ledger() -> TrustLedger:
        run_suffix = uuid4().hex[:12]
        return FirestoreLedger(
            f"{settings.ledger_collection}_{run_suffix}",
            project_id=settings.project_id,
            database=settings.firestore_database,
        )

    return make_cloud_ledger


def build_runtime_scenario() -> TenureScenario:
    if selected_runtime() is RuntimeMode.LOCAL:
        return TenureScenario(configured_ledger_factory())

    settings = CloudSettings.from_env()
    secret_provider = SecretManagerProvider(settings.project_id)
    signing_key = secret_provider.read(settings.supervisor_secret)
    publisher = SignedIncidentPublisher(
        settings.project_id,
        settings.incident_topic,
        signing_key,
    )
    armor = ModelArmorGateway(
        settings.project_id,
        settings.location,
        settings.model_armor_template,
    )
    return TenureScenario(
        configured_ledger_factory(),
        tracing=TenureTracing(build_cloud_tracer(settings.project_id)),
        reasoner_factory=lambda ledger: AdkSupervisorReasoner(ledger),
        prompt_guard=armor,
        incident_publisher=publisher,
        mode="GOOGLE_CLOUD_LIVE",
        cloud_truth=(
            "Live Google Cloud proof: Firestore ledger, Vertex AI reasoning, "
            "Model Armor, Pub/Sub escalation, Secret Manager signing, and Cloud Trace."
        ),
        cloud_claim=True,
        integration_status={
            "cloud_run": "CLOUD_MODE",
            "firestore": "CONFIGURED",
            "pubsub": "CONFIGURED",
            "secret_manager": "CONFIGURED",
            "google_adk": "VERTEX_MODE",
            "vertex_ai": "CONFIGURED",
            "model_armor": "CONFIGURED",
            "cloud_trace": "OTEL_READY",
        },
    )


def build_runtime_fleet() -> ProcureToPayFleet:
    if selected_runtime() is RuntimeMode.LOCAL:
        return ProcureToPayFleet()

    settings = CloudSettings.from_env()
    return ProcureToPayFleet(
        ledger=FirestoreLedger(
            f"{settings.ledger_collection}_fleet",
            project_id=settings.project_id,
            database=settings.firestore_database,
        ),
        sandbox=FirestoreProcureToPaySandbox(
            project_id=settings.project_id,
            database=settings.firestore_database,
        ),
    )


def build_runtime_recovery(fleet: ProcureToPayFleet) -> FleetRecoveryOrchestrator:
    if selected_runtime() is RuntimeMode.LOCAL:
        provider = selected_supervisor_provider()
        return FleetRecoveryOrchestrator(
            fleet,
            reasoner=(
                AdkFleetRecoveryReasoner()
                if provider is SupervisorProvider.GEMINI
                else None
            ),
            memory_reader=VerifiedMemorySnapshot(),
        )

    settings = CloudSettings.from_env()
    memory_resource = os.getenv("TENURE_SUPERVISOR_MEMORY_RESOURCE", "").strip()
    if not memory_resource:
        raise RuntimeError("TENURE_SUPERVISOR_MEMORY_RESOURCE is required in cloud mode")
    signing_key = SecretManagerProvider(settings.project_id).read(
        settings.supervisor_secret
    )
    return FleetRecoveryOrchestrator(
        fleet,
        reasoner=AdkFleetRecoveryReasoner(),
        memory_reader=AgentMemoryBankReader(
            settings.project_id,
            settings.location,
            memory_resource,
        ),
        signing_key=signing_key,
        trace_id=os.getenv("TENURE_LAST_TRACE_ID") or None,
    )
