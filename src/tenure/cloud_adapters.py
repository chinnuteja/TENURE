"""Google Cloud infrastructure adapters, kept outside the domain authority core."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from tenure.domain import AuthorityLevel, IncidentEnvelope
from tenure.fleet import (
    InvoiceRecord,
    PaymentRecord,
    PurchaseOrder,
    VendorRecord,
)
from tenure.fleet_control import MUTATION_KEYS, FirestoreAtomicStore, FleetControl
from tenure.ledger import AppendOnlyLedger, LedgerEvent


@dataclass(frozen=True, slots=True)
class CloudSettings:
    project_id: str
    location: str = "us-central1"
    incident_topic: str = "tenure-incidents"
    ledger_collection: str = "tenure_trust_events"
    supervisor_secret: str = "tenure-supervisor-envelope-key"
    model_armor_template: str = ""
    firestore_database: str = "(default)"

    @classmethod
    def from_env(cls) -> CloudSettings:
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        if not project_id:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is required in cloud mode")
        return cls(
            project_id=project_id,
            location=os.getenv(
                "TENURE_RESOURCE_LOCATION",
                os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
            ),
            incident_topic=os.getenv("TENURE_INCIDENT_TOPIC", "tenure-incidents"),
            ledger_collection=os.getenv(
                "TENURE_LEDGER_COLLECTION", "tenure_trust_events"
            ),
            supervisor_secret=os.getenv(
                "TENURE_SUPERVISOR_SECRET", "tenure-supervisor-envelope-key"
            ),
            model_armor_template=os.getenv("TENURE_MODEL_ARMOR_TEMPLATE", ""),
            firestore_database=os.getenv(
                "TENURE_FIRESTORE_DATABASE", "(default)"
            ),
        )


class SecretProvider(Protocol):
    def read(self, secret_id: str) -> bytes: ...


class SecretManagerProvider:
    """Read a pinned/latest Google Secret Manager version through an injected client."""

    def __init__(self, project_id: str, client: Any | None = None) -> None:
        if client is None:
            try:
                from google.cloud import secretmanager
            except ImportError as exc:
                raise RuntimeError(
                    'Install cloud dependencies with: python -m pip install -e ".[cloud]"'
                ) from exc
            client = secretmanager.SecretManagerServiceClient()
        self.project_id = project_id
        self.client = client

    def read(self, secret_id: str) -> bytes:
        name = f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
        response = self.client.access_secret_version(request={"name": name})
        return bytes(response.payload.data)


class AgentMemoryBankReader:
    """Read verified incident lessons from a deployed Agent Runtime Memory Bank."""

    def __init__(
        self,
        project_id: str,
        location: str,
        resource_name: str,
        client: Any | None = None,
        *,
        user_id: str = "tenure-platform-proof",
        app_name: str = "tenure_supervisor_agent",
    ) -> None:
        if client is None:
            try:
                import agentplatform
            except ImportError as exc:
                raise RuntimeError(
                    'Install agent dependencies with: python -m pip install -e ".[agent]"'
                ) from exc
            client = agentplatform.Client(project=project_id, location=location)
        self.client = client
        self.resource_name = resource_name
        self.runtime_resource = resource_name.split("/memories/", 1)[0]
        self.scope = {"user_id": user_id, "app_name": app_name}

    def read(self, query: str) -> dict[str, Any]:
        memories = [
            self._json_value(memory)
            for memory in self.client.agent_engines.retrieve_memories(
                name=self.runtime_resource,
                scope=self.scope,
                simple_retrieval_params={"page_size": 20},
            )
        ]
        return {
            "query": query,
            "resource": self.resource_name,
            "runtime_resource": self.runtime_resource,
            "scope": self.scope,
            "retrieval_mode": "LIVE_AGENT_MEMORY_BANK",
            "retrieval_verified": bool(memories),
            "retrieved_count": len(memories),
            "memories": memories,
        }

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, dict | list | str | int | float | bool) or value is None:
            return value
        if hasattr(value, "to_json_dict"):
            return value.to_json_dict()
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return str(value)


class SignedIncidentPublisher:
    """Publish signed incident envelopes to Pub/Sub for the Supervisor Agent."""

    def __init__(
        self,
        project_id: str,
        topic_id: str,
        signing_key: bytes,
        client: Any | None = None,
    ) -> None:
        if client is None:
            try:
                from google.cloud import pubsub_v1
            except ImportError as exc:
                raise RuntimeError(
                    'Install cloud dependencies with: python -m pip install -e ".[cloud]"'
                ) from exc
            client = pubsub_v1.PublisherClient()
        self.client = client
        self.topic_path = client.topic_path(project_id, topic_id)
        self.signing_key = signing_key

    def publish(self, incident: IncidentEnvelope) -> str:
        body = self._canonical_incident(incident)
        signature = hmac.new(self.signing_key, body, hashlib.sha256).hexdigest()
        future = self.client.publish(
            self.topic_path,
            body,
            content_type="application/json",
            tenure_signature=signature,
            incident_id=incident.incident_id,
        )
        return str(future.result(timeout=30))

    @staticmethod
    def _canonical_incident(incident: IncidentEnvelope) -> bytes:
        payload = asdict(incident)
        payload["previous_level"] = incident.previous_level.name
        payload["opened_at"] = incident.opened_at.isoformat()
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


class FirestoreLedger(AppendOnlyLedger):
    """Immutable Firestore event adapter with a transactional chain head."""

    HEAD_DOCUMENT = "_chain_head"

    def __init__(
        self,
        collection: str,
        client: Any | None = None,
        *,
        project_id: str | None = None,
        database: str | None = None,
    ) -> None:
        if client is None:
            try:
                from google.cloud import firestore
            except ImportError as exc:
                raise RuntimeError(
                    'Install cloud dependencies with: python -m pip install -e ".[cloud]"'
                ) from exc
            client = firestore.Client(project=project_id, database=database)
        self.client = client
        self.collection_name = collection
        self.collection = client.collection(collection)

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        snapshots = self.collection.order_by("sequence").stream()
        return tuple(
            self._from_document(snapshot.to_dict())
            for snapshot in snapshots
            if snapshot.id != self.HEAD_DOCUMENT
        )

    def append(self, event_type: str, payload: dict[str, Any]) -> LedgerEvent:
        try:
            from google.cloud import firestore
        except ImportError as exc:
            raise RuntimeError(
                'Install cloud dependencies with: python -m pip install -e ".[cloud]"'
            ) from exc

        transaction = self.client.transaction()
        head_ref = self.collection.document(self.HEAD_DOCUMENT)

        @firestore.transactional
        def append_transaction(active_transaction: Any) -> LedgerEvent:
            head = head_ref.get(transaction=active_transaction)
            head_data = head.to_dict() if head.exists else {}
            sequence = int(head_data.get("sequence", 0)) + 1
            previous_hash = str(head_data.get("event_hash", self.GENESIS_HASH))
            event_id = f"evt_{uuid4().hex[:16]}"
            occurred_at = datetime.now(UTC).isoformat()
            canonical = self._canonical(
                sequence, event_id, event_type, occurred_at, payload, previous_hash
            )
            event = LedgerEvent(
                sequence=sequence,
                event_id=event_id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
                previous_hash=previous_hash,
                event_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            )
            event_ref = self.collection.document(event.event_id)
            active_transaction.create(event_ref, self._to_document(event))
            active_transaction.set(
                head_ref,
                {
                    "sequence": event.sequence,
                    "event_hash": event.event_hash,
                    "updated_at": event.occurred_at,
                },
            )
            return event

        return append_transaction(transaction)

    def find(
        self, event_type: str | None = None, **payload_match: Any
    ) -> tuple[LedgerEvent, ...]:
        return tuple(
            event
            for event in self.events
            if (event_type is None or event.event_type == event_type)
            and all(event.payload.get(key) == value for key, value in payload_match.items())
        )

    def verify_chain(self) -> bool:
        previous_hash = self.GENESIS_HASH
        for event in self.events:
            canonical = self._canonical(
                event.sequence,
                event.event_id,
                event.event_type,
                event.occurred_at,
                event.payload,
                previous_hash,
            )
            if event.previous_hash != previous_hash:
                return False
            if hashlib.sha256(canonical.encode()).hexdigest() != event.event_hash:
                return False
            previous_hash = event.event_hash
        return True

    def export(self) -> Iterable[dict[str, Any]]:
        return super().export()

    @staticmethod
    def _to_document(event: LedgerEvent) -> dict[str, Any]:
        return {
            "sequence": event.sequence,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "payload": event.payload,
            "previous_hash": event.previous_hash,
            "event_hash": event.event_hash,
        }

    @staticmethod
    def _from_document(document: dict[str, Any]) -> LedgerEvent:
        return LedgerEvent(
            sequence=int(document["sequence"]),
            event_id=str(document["event_id"]),
            event_type=str(document["event_type"]),
            occurred_at=str(document["occurred_at"]),
            payload=dict(document["payload"]),
            previous_hash=str(document["previous_hash"]),
            event_hash=str(document["event_hash"]),
        )


class FirestoreProcureToPaySandbox:
    """Durable, tenant-keyed synthetic business records for the fleet demo."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        project_id: str | None = None,
        database: str | None = None,
        collection_prefix: str = "tenure_p2p",
        transaction_runner=None,
    ) -> None:
        if client is None:
            try:
                from google.cloud import firestore
            except ImportError as exc:
                raise RuntimeError(
                    'Install cloud dependencies with: python -m pip install -e ".[cloud]"'
                ) from exc
            client = firestore.Client(project=project_id, database=database)
        self.client = client
        self.collection_prefix = collection_prefix
        self.control = FleetControl(FirestoreAtomicStore(
            client, collection_prefix, transaction_runner=transaction_runner,
        ))

    def execute(self, tenant_id: str, capability_key: str, operation: str, *args):
        if MUTATION_KEYS.get(operation) != capability_key:
            raise PermissionError("operation does not match capability")
        return self.control.guard(
            tenant_id, capability_key,
            lambda transaction: getattr(self, operation)(
                tenant_id, *args, transaction=transaction,
            ),
        )

    @property
    def persistence(self) -> str:
        return "firestore"

    def seed_case(
        self,
        *,
        tenant_id: str,
        vendor_id: str,
        po_id: str,
        invoice_id: str,
        amount: int,
    ) -> None:
        self._ref("vendors", tenant_id, vendor_id).set(
            asdict(
                VendorRecord(
                    tenant_id,
                    vendor_id,
                    "Aster Components Pvt Ltd",
                    "GSTIN-36AAECA001",
                    "bank-sha256:49d8",
                )
            )
        )
        self._ref("purchase_orders", tenant_id, po_id).set(
            asdict(PurchaseOrder(tenant_id, po_id, vendor_id, amount))
        )
        self._ref("invoices", tenant_id, invoice_id).set(
            asdict(InvoiceRecord(tenant_id, invoice_id, po_id, vendor_id, amount))
        )

    def onboard_vendor(self, tenant_id: str, vendor_id: str, *, transaction=None) -> VendorRecord:
        vendor = VendorRecord(**self._load("vendors", tenant_id, vendor_id, transaction))
        vendor.status = "ONBOARDED"
        self._write(self._ref("vendors", tenant_id, vendor_id), asdict(vendor), transaction)
        return vendor

    def approve_invoice(
        self, tenant_id: str, invoice_id: str, *, transaction=None,
    ) -> InvoiceRecord:
        invoice = InvoiceRecord(**self._load("invoices", tenant_id, invoice_id, transaction))
        vendor = VendorRecord(
            **self._load("vendors", tenant_id, invoice.vendor_id, transaction)
        )
        order = PurchaseOrder(
            **self._load("purchase_orders", tenant_id, invoice.po_id, transaction)
        )
        if vendor.status != "ONBOARDED":
            raise ValueError("invoice authority depends on an onboarded vendor")
        if order.vendor_id != invoice.vendor_id or order.amount != invoice.amount:
            raise ValueError("invoice does not match its purchase order")
        invoice.status = "APPROVED"
        self._write(self._ref("invoices", tenant_id, invoice_id), asdict(invoice), transaction)
        return invoice

    def release_payment(
        self, tenant_id: str, payment_id: str, invoice_id: str, *, transaction=None,
    ) -> PaymentRecord:
        invoice = InvoiceRecord(**self._load("invoices", tenant_id, invoice_id, transaction))
        if invoice.status != "APPROVED":
            raise ValueError("payment authority depends on an approved invoice")
        if type(invoice.amount) is not int or invoice.amount <= 0:
            raise ValueError("sandbox payment must have a positive integer amount")
        payment_ref = self._ref("payments", tenant_id, payment_id)
        snapshot = payment_ref.get(transaction=transaction) if transaction else payment_ref.get()
        if snapshot.exists:
            payment = PaymentRecord(**snapshot.to_dict())
            if (
                payment.invoice_id != invoice_id or payment.amount != invoice.amount
                or payment.vendor_id != invoice.vendor_id or not payment.reversible
                or payment.status not in {"SCHEDULED", "RELEASED_SANDBOX"}
            ):
                raise PermissionError("payment is irreversible, compensated, or conflicts")
        else:
            payment = PaymentRecord(
                tenant_id,
                payment_id,
                invoice.invoice_id,
                invoice.vendor_id,
                invoice.amount,
            )
        payment.status = "RELEASED_SANDBOX"
        self._write(payment_ref, asdict(payment), transaction)
        return payment

    def vendor_snapshot(self, tenant_id: str, vendor_id: str) -> dict[str, Any]:
        return self._load("vendors", tenant_id, vendor_id)

    def invoice_snapshot(self, tenant_id: str, invoice_id: str) -> dict[str, Any]:
        return self._load("invoices", tenant_id, invoice_id)

    def payment_snapshot(self, tenant_id: str, payment_id: str) -> dict[str, Any]:
        return self._load("payments", tenant_id, payment_id)

    def rollback_entity(
        self, tenant_id: str, entity_type: str, entity_id: str
    ) -> dict[str, Any]:
        transitions = {
            "vendor": ("vendors", "SUSPENDED"),
            "invoice": ("invoices", "HELD"),
            "payment": ("payments", "REVERSED_SANDBOX"),
        }
        try:
            collection, target_status = transitions[entity_type]
        except KeyError as exc:
            raise ValueError(f"unsupported rollback entity: {entity_type}") from exc
        record = self._load(collection, tenant_id, entity_id)
        before = record["status"]
        self._ref(collection, tenant_id, entity_id).update({"status": target_status})
        return {
            "tenant_id": tenant_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before": before,
            "after": target_status,
        }

    @staticmethod
    def _write(ref, document, transaction):
        if transaction is not None:
            transaction.set(ref, document)
        else:
            ref.set(document)

    def _load(
        self, entity: str, tenant_id: str, entity_id: str, transaction=None,
    ) -> dict[str, Any]:
        ref = self._ref(entity, tenant_id, entity_id)
        snapshot = ref.get(transaction=transaction) if transaction else ref.get()
        if not snapshot.exists:
            raise KeyError(entity_id)
        document = dict(snapshot.to_dict())
        if document.get("tenant_id") != tenant_id:
            raise PermissionError("Firestore tenant key mismatch")
        return document

    def _ref(self, entity: str, tenant_id: str, entity_id: str) -> Any:
        document_id = hashlib.sha256(
            f"{tenant_id}\0{entity_id}".encode()
        ).hexdigest()
        return self.client.collection(
            f"{self.collection_prefix}_{entity}"
        ).document(document_id)


def verify_incident_signature(body: bytes, signature: str, key: bytes) -> bool:
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def previous_level_from_payload(payload: dict[str, Any]) -> AuthorityLevel:
    return AuthorityLevel[str(payload["previous_level"])]
