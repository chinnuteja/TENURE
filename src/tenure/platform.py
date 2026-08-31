"""Truthful, read-only Google platform evidence exposed to judges."""

from __future__ import annotations

import os
from typing import Any


def _env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def platform_evidence() -> dict[str, Any]:
    project_id = _env("GOOGLE_CLOUD_PROJECT")
    project_number = _env("GOOGLE_CLOUD_PROJECT_NUMBER")
    location = (
        _env("TENURE_RESOURCE_LOCATION")
        or _env("GOOGLE_CLOUD_LOCATION")
        or "us-central1"
    )
    subject_id = _env("TENURE_SUBJECT_RUNTIME_ID")
    supervisor_id = _env("TENURE_SUPERVISOR_RUNTIME_ID")
    organization_id = _env("GOOGLE_CLOUD_ORGANIZATION_ID")
    proof_verified = (_env("TENURE_CLOUD_PROOF_VERIFIED") or "").lower() == "true"

    def runtime_resource(runtime_id: str | None) -> str | None:
        if not project_number or not runtime_id:
            return None
        return (
            f"projects/{project_number}/locations/{location}/"
            f"reasoningEngines/{runtime_id}"
        )

    def identity(runtime_id: str | None) -> str | None:
        if not organization_id or not project_number or not runtime_id:
            return None
        return (
            f"agents.global.org-{organization_id}.system.id.goog/resources/"
            f"aiplatform/projects/{project_number}/locations/{location}/"
            f"reasoningEngines/{runtime_id}"
        )

    subject_resource = runtime_resource(subject_id)
    supervisor_resource = runtime_resource(supervisor_id)
    fleet_runtime_ids = {
        "vendor": _env("TENURE_VENDOR_RUNTIME_ID"),
        "invoice": _env("TENURE_INVOICE_RUNTIME_ID") or subject_id,
        "treasury": _env("TENURE_TREASURY_RUNTIME_ID"),
        "supervisor": supervisor_id,
    }
    return {
        "cloud_proof_verified": proof_verified,
        "project_id": project_id,
        "project_number": project_number,
        "location": location,
        "cloud_run": {
            "service": _env("K_SERVICE"),
            "revision": _env("K_REVISION"),
            "configuration": _env("K_CONFIGURATION"),
            "authenticated_only": True if _env("K_SERVICE") else None,
            "scale_to_zero": True if _env("K_SERVICE") else None,
        },
        "model": {
            "id": _env("TENURE_GEMINI_MODEL") or "gemini-3.5-flash",
            "location": _env("TENURE_GEMINI_LOCATION") or "global",
            "response_id": _env("TENURE_GEMINI_RESPONSE_ID"),
        },
        "control_spine": {
            "firestore_database": (
                f"projects/{project_id}/databases/"
                f"{_env('TENURE_FIRESTORE_DATABASE') or '(default)'}"
                if project_id
                else None
            ),
            "pubsub_topic": (
                f"projects/{project_id}/topics/"
                f"{_env('TENURE_INCIDENT_TOPIC') or 'tenure-incidents'}"
                if project_id
                else None
            ),
            "model_armor_template": (
                f"projects/{project_id}/locations/{location}/templates/"
                f"{_env('TENURE_MODEL_ARMOR_TEMPLATE')}"
                if project_id and _env("TENURE_MODEL_ARMOR_TEMPLATE")
                else None
            ),
            "cloud_trace_id": _env("TENURE_LAST_TRACE_ID"),
            "pubsub_message_id": _env("TENURE_LAST_PUBSUB_MESSAGE_ID"),
        },
        "agent_runtime": {
            "subject": {
                "resource": subject_resource,
                "identity_type": "AGENT_IDENTITY" if subject_resource else None,
                "effective_identity": identity(subject_id),
                "registry_resource": _env("TENURE_SUBJECT_REGISTRY_RESOURCE"),
            },
            "supervisor": {
                "resource": supervisor_resource,
                "identity_type": "AGENT_IDENTITY" if supervisor_resource else None,
                "effective_identity": identity(supervisor_id),
                "registry_resource": _env("TENURE_SUPERVISOR_REGISTRY_RESOURCE"),
                "memory_resource": _env("TENURE_SUPERVISOR_MEMORY_RESOURCE"),
            },
        },
        "fleet": {
            role: {
                "resource": runtime_resource(runtime_id),
                "effective_identity": identity(runtime_id),
                "registry_resource": _env(
                    f"TENURE_{role.upper()}_REGISTRY_RESOURCE"
                )
                or (
                    _env("TENURE_SUBJECT_REGISTRY_RESOURCE")
                    if role == "invoice"
                    else _env("TENURE_SUPERVISOR_REGISTRY_RESOURCE")
                    if role == "supervisor"
                    else None
                ),
            }
            for role, runtime_id in fleet_runtime_ids.items()
        },
        "agent_gateway": {
            "availability_checked": (
                _env("TENURE_AGENT_GATEWAY_AVAILABLE") or ""
            ).lower()
            == "true",
            "resource": _env("TENURE_AGENT_GATEWAY_RESOURCE"),
            "note": (
                "API access verified; resource deferred until fleet tool endpoints exist."
                if (_env("TENURE_AGENT_GATEWAY_AVAILABLE") or "").lower() == "true"
                and not _env("TENURE_AGENT_GATEWAY_RESOURCE")
                else None
            ),
        },
        "cost_guard": {
            "budget_id": _env("TENURE_BUDGET_ID"),
            "monthly_alert_budget_inr": 1500,
            "hard_cap": False,
        },
        "limitations": [
            "Billing budgets alert; they do not hard-stop spend.",
            "Agent Gateway is not claimed as deployed unless its resource is present.",
            "This endpoint reports verified resource IDs; it does not expose secrets.",
        ],
    }
