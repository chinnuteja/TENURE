from __future__ import annotations

from dataclasses import dataclass

import pytest

from tenure.cloud_adapters import (
    AgentMemoryBankReader,
    CloudSettings,
    FirestoreLedger,
    FirestoreProcureToPaySandbox,
    SecretManagerProvider,
    SignedIncidentPublisher,
    verify_incident_signature,
)
from tenure.domain import AuthorityLevel, IncidentEnvelope


@dataclass
class FakePayload:
    data: bytes


@dataclass
class FakeSecretResponse:
    payload: FakePayload


class FakeSecretClient:
    def __init__(self) -> None:
        self.request = None

    def access_secret_version(self, *, request):
        self.request = request
        return FakeSecretResponse(FakePayload(b"supervisor-key"))


class FakeFuture:
    def result(self, timeout: int) -> str:
        assert timeout == 30
        return "message-123"


class FakeAgentEnginesClient:
    def __init__(self) -> None:
        self.request = None

    def retrieve_memories(self, **kwargs):
        self.request = kwargs
        return iter([{"fact": "shared upstream requires family containment"}])


class FakeAgentPlatformClient:
    def __init__(self) -> None:
        self.agent_engines = FakeAgentEnginesClient()


class FakePublisherClient:
    def __init__(self) -> None:
        self.published = None

    def topic_path(self, project_id: str, topic_id: str) -> str:
        return f"projects/{project_id}/topics/{topic_id}"

    def publish(self, topic_path: str, body: bytes, **attributes):
        self.published = (topic_path, body, attributes)
        return FakeFuture()


@dataclass
class FakeDocument:
    id: str
    document: dict

    def to_dict(self) -> dict:
        return self.document


class FakeQuery:
    def stream(self):
        return iter(
            (
                FakeDocument(
                    "_chain_head", {"sequence": 1, "event_hash": "head-hash"}
                ),
                FakeDocument(
                    "evt_1",
                    {
                        "sequence": 1,
                        "event_id": "evt_1",
                        "event_type": "PROBE",
                        "occurred_at": "2026-08-25T00:00:00+00:00",
                        "payload": {"ok": True},
                        "previous_hash": "0" * 64,
                        "event_hash": "event-hash",
                    },
                ),
            )
        )


class FakeCollection:
    def order_by(self, field: str) -> FakeQuery:
        assert field == "sequence"
        return FakeQuery()


class FakeFirestoreClient:
    def collection(self, name: str) -> FakeCollection:
        assert name == "probe"
        return FakeCollection()


def test_cloud_settings_require_explicit_project(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(RuntimeError):
        CloudSettings.from_env()

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "tenure-test")
    assert CloudSettings.from_env().project_id == "tenure-test"


def test_secret_manager_provider_uses_project_scoped_version() -> None:
    client = FakeSecretClient()
    provider = SecretManagerProvider("tenure-test", client)
    assert provider.read("incident-key") == b"supervisor-key"
    assert client.request == {
        "name": "projects/tenure-test/secrets/incident-key/versions/latest"
    }


def test_agent_memory_reader_uses_exact_runtime_scope() -> None:
    client = FakeAgentPlatformClient()
    reader = AgentMemoryBankReader(
        "tenure-test",
        "us-central1",
        "projects/123/locations/us-central1/reasoningEngines/456/memories/789",
        client,
    )

    result = reader.read("upstream containment")

    assert result["retrieval_mode"] == "LIVE_AGENT_MEMORY_BANK"
    assert result["retrieval_verified"] is True
    assert result["retrieved_count"] == 1
    assert result["resource"].endswith("/reasoningEngines/456/memories/789")
    assert result["runtime_resource"].endswith("/reasoningEngines/456")
    assert client.agent_engines.request == {
        "name": "projects/123/locations/us-central1/reasoningEngines/456",
        "scope": {
            "user_id": "tenure-platform-proof",
            "app_name": "tenure_supervisor_agent",
        },
        "simple_retrieval_params": {"page_size": 20},
    }


def test_pubsub_incident_is_canonical_and_signed() -> None:
    client = FakePublisherClient()
    publisher = SignedIncidentPublisher(
        "tenure-test", "incidents", b"signing-key", client
    )
    incident = IncidentEnvelope(
        incident_id="incident-1",
        agent_id="agent-1",
        capability="invoice.approve",
        failed_action_id="action-1",
        previous_level=AuthorityLevel.EXECUTE_BOUNDED,
        controlling_policy="policy#7.1",
        reason="test failure",
        trace_id="trace-1",
    )

    assert publisher.publish(incident) == "message-123"
    topic, body, attributes = client.published
    assert topic == "projects/tenure-test/topics/incidents"
    assert attributes["incident_id"] == "incident-1"
    assert verify_incident_signature(
        body, attributes["tenure_signature"], b"signing-key"
    )


def test_firestore_events_exclude_internal_chain_head() -> None:
    ledger = FirestoreLedger("probe", FakeFirestoreClient())

    assert len(ledger.events) == 1
    assert ledger.events[0].event_id == "evt_1"


class DurableFakeSnapshot:
    def __init__(self, document: dict | None) -> None:
        self.document = document
        self.exists = document is not None

    def to_dict(self) -> dict:
        return dict(self.document or {})


class DurableFakeDocumentRef:
    def __init__(self, store: dict[str, dict], key: str) -> None:
        self.store = store
        self.key = key

    def set(self, document: dict) -> None:
        self.store[self.key] = dict(document)

    def update(self, changes: dict) -> None:
        self.store[self.key].update(changes)

    def get(self) -> DurableFakeSnapshot:
        return DurableFakeSnapshot(self.store.get(self.key))


class DurableFakeCollection:
    def __init__(self, store: dict[str, dict], collection: str) -> None:
        self.store = store
        self.collection = collection

    def document(self, document_id: str) -> DurableFakeDocumentRef:
        return DurableFakeDocumentRef(
            self.store, f"{self.collection}/{document_id}"
        )


class DurableFakeFirestoreClient:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def collection(self, name: str) -> DurableFakeCollection:
        return DurableFakeCollection(self.store, name)


def test_firestore_sandbox_survives_adapter_reconstruction() -> None:
    client = DurableFakeFirestoreClient()
    first = FirestoreProcureToPaySandbox(client)
    first.seed_case(
        tenant_id="tenant-a",
        vendor_id="vendor-1",
        po_id="po-1",
        invoice_id="invoice-1",
        amount=18_400,
    )
    first.onboard_vendor("tenant-a", "vendor-1")
    first.approve_invoice("tenant-a", "invoice-1")
    first.release_payment("tenant-a", "payment-1", "invoice-1")

    reconstructed = FirestoreProcureToPaySandbox(client)

    assert reconstructed.persistence == "firestore"
    assert reconstructed.vendor_snapshot("tenant-a", "vendor-1")["status"] == (
        "ONBOARDED"
    )
    assert reconstructed.invoice_snapshot("tenant-a", "invoice-1")["status"] == (
        "APPROVED"
    )
    assert reconstructed.payment_snapshot("tenant-a", "payment-1")["status"] == (
        "RELEASED_SANDBOX"
    )
    reconstructed.rollback_entity("tenant-a", "vendor", "vendor-1")
    reconstructed.rollback_entity("tenant-a", "invoice", "invoice-1")
    reconstructed.rollback_entity("tenant-a", "payment", "payment-1")
    assert reconstructed.vendor_snapshot("tenant-a", "vendor-1")["status"] == (
        "SUSPENDED"
    )
    assert reconstructed.invoice_snapshot("tenant-a", "invoice-1")["status"] == "HELD"
    assert reconstructed.payment_snapshot("tenant-a", "payment-1")["status"] == (
        "REVERSED_SANDBOX"
    )
    with pytest.raises(KeyError):
        reconstructed.vendor_snapshot("tenant-b", "vendor-1")
