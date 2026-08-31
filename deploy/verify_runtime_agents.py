"""Invoke TENURE's managed agents and prove identity plus Memory Bank."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import agentplatform
import vertexai
from vertexai import agent_engines


def json_value(value: Any) -> Any:
    if isinstance(value, dict | list | str | int | float | bool) or value is None:
        return value
    if hasattr(value, "to_json_dict"):
        return value.to_json_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def identity_evidence(client: agentplatform.Client, name: str) -> dict[str, str]:
    resource = client.agent_engines.get(name=name).api_resource
    return {
        "resource": resource.name,
        "display_name": resource.display_name,
        "identity_type": str(resource.spec.identity_type),
        "effective_identity": resource.spec.effective_identity,
    }


async def invoke(
    resource_name: str,
    *,
    user_id: str,
    message: str,
) -> dict[str, Any]:
    remote = agent_engines.get(resource_name)
    session = await remote.async_create_session(user_id=user_id)
    session_id = session.get("id")
    events = []
    async for event in remote.async_stream_query(
        user_id=user_id,
        session_id=session_id,
        message=message,
    ):
        events.append(json_value(event))
    encoded_events = json.dumps(events, sort_keys=True)
    if '"error_code"' in encoded_events or '"code": 498' in encoded_events:
        raise RuntimeError(f"Agent Runtime invocation failed: {encoded_events}")
    return {
        "resource": resource_name,
        "session_id": session_id,
        "event_count": len(events),
        "events": events,
    }


def prove_memory(
    client: agentplatform.Client,
    *,
    resource_name: str,
    user_id: str,
) -> dict[str, Any]:
    fact = (
        "TENURE proof: correlated failures sharing an upstream vendor require "
        "capability-family containment, subject to deterministic policy validation."
    )
    scope = {"user_id": user_id, "app_name": "tenure_supervisor_agent"}
    memories = [
        json_value(memory)
        for memory in client.agent_engines.retrieve_memories(
            name=resource_name,
            scope=scope,
            simple_retrieval_params={"page_size": 20},
        )
    ]
    if fact in json.dumps(memories, sort_keys=True):
        return {
            "resource": resource_name,
            "scope": scope,
            "fact_verified": True,
            "write_performed": False,
            "retrieved_count": len(memories),
            "memories": memories,
        }
    operation = client.agent_engines.generate_memories(
        name=resource_name,
        direct_memories_source={"direct_memories": [{"fact": fact}]},
        scope=scope,
    )
    if hasattr(operation, "result"):
        operation.result(timeout=180)
    memories = [
        json_value(memory)
        for memory in client.agent_engines.retrieve_memories(
            name=resource_name,
            scope=scope,
            simple_retrieval_params={"page_size": 20},
        )
    ]
    encoded = json.dumps(memories, sort_keys=True)
    if fact not in encoded:
        raise RuntimeError("Memory Bank did not return the TENURE proof memory")
    return {
        "resource": resource_name,
        "scope": scope,
        "fact_verified": True,
        "write_performed": True,
        "retrieved_count": len(memories),
        "memories": memories,
    }


async def verify(args: argparse.Namespace) -> dict[str, Any]:
    vertexai.init(project=args.project, location=args.location)
    client = agentplatform.Client(project=args.project, location=args.location)
    evidence: dict[str, Any] = {}

    def runtime_name(runtime_id: str) -> str:
        return (
            f"projects/{args.project_number}/locations/{args.location}/"
            f"reasoningEngines/{runtime_id}"
        )

    if args.subject_id and args.supervisor_id:
        subject_name = runtime_name(args.subject_id)
        supervisor_name = runtime_name(args.supervisor_id)
        evidence["subject_identity"] = identity_evidence(client, subject_name)
        evidence["supervisor_identity"] = identity_evidence(client, supervisor_name)
        evidence["subject_invocation"] = await invoke(
            subject_name,
            user_id=args.user_id,
            message=(
                "Inspect invoice INV-42: amount_usd=12500, vendor_age_days=7, "
                "bank_account_changed=true. Use your tool and return the evidence."
            ),
        )
        evidence["supervisor_invocation"] = await invoke(
            supervisor_name,
            user_id=args.user_id,
            message=(
                "Investigate incident INC-42. Failed action PAY-42 has dependent "
                "actions POST-42 and RECON-42, shared vendor VENDOR-7, three "
                "correlated failures, shared_upstream evidence, and verified policy "
                "integrity. Use every available investigation and escalation tool."
            ),
        )
        evidence["memory_bank"] = prove_memory(
            client,
            resource_name=supervisor_name,
            user_id=args.user_id,
        )

    if args.vendor_id:
        vendor_name = runtime_name(args.vendor_id)
        evidence["vendor_identity"] = identity_evidence(client, vendor_name)
        evidence["vendor_invocation"] = await invoke(
            vendor_name,
            user_id=args.user_id,
            message=(
                "Assess vendor VENDOR-100: tax_id_verified=true, "
                "sanctions_match=false, bank_account_age_days=365, "
                "geography_allowed=true. Use your tool and return the evidence."
            ),
        )

    if args.treasury_id:
        treasury_name = runtime_name(args.treasury_id)
        evidence["treasury_identity"] = identity_evidence(client, treasury_name)
        evidence["treasury_invocation"] = await invoke(
            treasury_name,
            user_id=args.user_id,
            message=(
                "Assess payment PAY-100: amount_usd=5000, invoice_approved=true, "
                "bank_account_age_days=365, duplicate_release=false, reversible=true. "
                "Use your tool and return the evidence."
            ),
        )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--project-number", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--subject-id")
    parser.add_argument("--supervisor-id")
    parser.add_argument("--vendor-id")
    parser.add_argument("--treasury-id")
    parser.add_argument("--user-id", default="tenure-platform-proof")
    args = parser.parse_args()
    if bool(args.subject_id) != bool(args.supervisor_id):
        parser.error("--subject-id and --supervisor-id must be supplied together")
    if not any((args.subject_id, args.vendor_id, args.treasury_id)):
        parser.error("supply a control-spine pair, --vendor-id, or --treasury-id")
    print(json.dumps(asyncio.run(verify(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
