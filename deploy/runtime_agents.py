"""Deploy TENURE's distinct fleet agents on Google Agent Runtime."""

from __future__ import annotations

import argparse
import json
from typing import Any

import agentplatform
from agentplatform import agent_engines, types
from google.adk.agents import Agent
from google.genai import types as genai_types

MODEL = "gemini-3.5-flash"
TENURE_PROJECT = "project-ceca895d-33b0-44b9-b5a"
SUBJECT_DISPLAY_NAME = "TENURE Subject Agent"
SUPERVISOR_DISPLAY_NAME = "TENURE Supervisor Agent"
VENDOR_DISPLAY_NAME = "TENURE Vendor Intelligence Agent"
TREASURY_DISPLAY_NAME = "TENURE Treasury Agent"


def native_identity_probe(nonce: str) -> dict[str, object]:
    """Read one pinned synthetic memory from INSIDE the native agent runtime.

    No model, credential export, impersonation, IAM change, or arbitrary target.
    Error bodies and memory contents never leave this operation.
    """
    import os
    import re
    from datetime import UTC, datetime

    project = TENURE_PROJECT
    project_number = "585333584620"
    location = "us-central1"
    runtime_id = os.getenv("TENURE_NATIVE_RUNTIME_ID") or os.getenv(
        "GOOGLE_CLOUD_AGENT_ENGINE_ID", ""
    )
    if not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise ValueError("Probe requires a fresh 32-character hexadecimal nonce")
    if runtime_id not in {"8053708818447597568", "1415402967703486464"}:
        raise RuntimeError("Probe is only available inside the pinned TENURE runtimes")
    runtime_project = os.getenv("TENURE_NATIVE_PROJECT") or os.getenv(
        "GOOGLE_CLOUD_PROJECT"
    )
    if runtime_project not in {project, project_number}:
        raise RuntimeError("Probe project mismatch")
    binding = os.getenv("GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES", "true")
    if binding.lower() != "true":
        raise RuntimeError("Probe requires native credential binding to remain enabled")
    resource = (
        f"projects/{project_number}/locations/{location}/reasoningEngines/"
        "1415402967703486464/memories/8766834811634450432"
    )
    result = {
        "schema": "tenure.native-identity/v1",
        "nonce": nonce,
        "runtime_id": runtime_id,
        "resource": resource,
        "permission": "aiplatform.memories.get",
        "model_calls": 0,
        "observed_at": datetime.now(UTC).isoformat(),
        "content_returned": False,
    }
    client = agentplatform.Client(
        project=project, location=location,
        http_options={"timeout": 15000, "retry_options": {"attempts": 1}},
    )
    try:
        memory = client.agent_engines.memories.get(name=resource)
        result.update(
            outcome="ALLOW" if memory.name == resource else "ERROR",
            http_status=200,
            permission_denied_verified=False,
        )
    except Exception as exc:
        code = getattr(exc, "code", None)
        details = getattr(exc, "details", {})
        error = details.get("error", details) if isinstance(details, dict) else {}
        reasons = error.get("details", [])
        permission_denied = (
            code == 403 and error.get("status") == "PERMISSION_DENIED"
            and any(
                item.get("reason") == "IAM_PERMISSION_DENIED"
                and item.get("metadata", {}).get("permission") == "aiplatform.memories.get"
                for item in reasons if isinstance(item, dict)
            )
        )
        result.update(
            outcome="DENY" if permission_denied else "ERROR",
            http_status=code if type(code) is int else None,
            permission_denied_verified=permission_denied,
        )
    return result


class TenureProofAdkApp(agent_engines.AdkApp):
    """Preserve the agent API and add a non-model, read-only verification RPC."""

    def identity_probe(self, nonce: str) -> dict[str, object]:
        return native_identity_probe(nonce)

    def register_operations(self) -> dict[str, list[str]]:
        operations = super().register_operations()
        operations[""] = [*operations[""], "identity_probe"]
        return operations


def inspect_vendor_signal(
    vendor_id: str,
    tax_id_verified: bool,
    sanctions_match: bool,
    bank_account_age_days: int,
    geography_allowed: bool,
) -> dict[str, object]:
    """Return vendor evidence only; onboarding remains gateway-controlled."""
    signals = []
    if not tax_id_verified:
        signals.append("tax_id_unverified")
    if sanctions_match:
        signals.append("sanctions_match")
    if bank_account_age_days < 30:
        signals.append("new_bank_account")
    if not geography_allowed:
        signals.append("geography_disallowed")
    return {
        "vendor_id": vendor_id,
        "signals": signals,
        "recommended_action": "hold_for_review" if signals else "eligible_for_gateway",
        "authority_mutated": False,
    }


def inspect_invoice_signal(
    invoice_id: str,
    amount_usd: float,
    vendor_age_days: int,
    bank_account_changed: bool,
) -> dict[str, object]:
    """Return observations only; this tool cannot grant or change authority."""
    signals = []
    if amount_usd >= 10_000:
        signals.append("high_value")
    if vendor_age_days < 30:
        signals.append("new_vendor")
    if bank_account_changed:
        signals.append("bank_change")
    return {
        "invoice_id": invoice_id,
        "signals": signals,
        "recommended_action": "request_supervisor_review" if signals else "continue",
        "authority_mutated": False,
    }


def inspect_payment_signal(
    payment_id: str,
    amount_usd: float,
    invoice_approved: bool,
    bank_account_age_days: int,
    duplicate_release: bool,
    reversible: bool,
) -> dict[str, object]:
    """Return payment evidence only; release remains gateway-controlled."""
    signals = []
    if not invoice_approved:
        signals.append("invoice_not_approved")
    if bank_account_age_days < 30:
        signals.append("new_bank_account")
    if duplicate_release:
        signals.append("duplicate_release")
    if not reversible:
        signals.append("irreversible_payment")
    if amount_usd >= 10_000:
        signals.append("high_value")
    return {
        "payment_id": payment_id,
        "signals": signals,
        "recommended_action": "hold_for_review" if signals else "eligible_for_gateway",
        "authority_mutated": False,
    }


def build_vendor_app() -> agent_engines.AdkApp:
    agent = Agent(
        name="tenure_vendor_intelligence_agent",
        model=MODEL,
        description="Assesses vendor evidence without onboarding or authority tools.",
        instruction=(
            "You are TENURE's Vendor Intelligence Agent. Use the tool to assess tax, "
            "sanctions, bank-age, and geography signals. Cite the supplied evidence, "
            "recommend hold or gateway review, and never claim to onboard a vendor or "
            "mutate authority. Return concise JSON."
        ),
        tools=[inspect_vendor_signal],
        generate_content_config=genai_types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=512,
            thinking_config=genai_types.ThinkingConfig(
                thinking_level=genai_types.ThinkingLevel.MINIMAL
            ),
        ),
    )
    return agent_engines.AdkApp(agent=agent, enable_tracing=True)


def enumerate_blast_radius(
    failed_action_id: str,
    dependent_action_ids: list[str],
    shared_vendor_ids: list[str],
) -> dict[str, object]:
    """Enumerate actions and vendors potentially affected by one failure."""
    return {
        "failed_action_id": failed_action_id,
        "affected_action_ids": sorted(set([failed_action_id, *dependent_action_ids])),
        "shared_vendor_ids": sorted(set(shared_vendor_ids)),
    }


def recommend_demotion(
    evidence_scope: str,
    correlated_failures: int,
    policy_integrity: str,
) -> dict[str, str]:
    """Recommend demotion depth; the deterministic policy kernel applies it."""
    if policy_integrity != "verified":
        depth = "fleet_freeze"
    elif correlated_failures > 1 or evidence_scope == "shared_upstream":
        depth = "capability_family"
    else:
        depth = "single_capability"
    return {"recommended_depth": depth, "authority_mutated": "false"}


def write_incident_narrative(
    incident_id: str,
    evidence_summary: str,
    affected_action_ids: list[str],
) -> dict[str, str]:
    """Draft an auditable incident narrative from evidence provided to the agent."""
    return {
        "incident_id": incident_id,
        "narrative": (
            f"Incident {incident_id}: {evidence_summary}. "
            f"Affected actions: {', '.join(sorted(set(affected_action_ids)))}."
        ),
    }


def file_escalation(
    incident_id: str,
    severity: str,
    human_queue: str,
    reason: str,
) -> dict[str, str]:
    """Create an escalation record without expanding an agent's authority."""
    return {
        "incident_id": incident_id,
        "severity": severity,
        "human_queue": human_queue,
        "reason": reason,
        "status": "filed",
        "authority_mutated": "false",
    }


def build_subject_app() -> agent_engines.AdkApp:
    agent = Agent(
        name="tenure_subject_agent",
        model=MODEL,
        description="Observes invoice risk signals without changing authority.",
        instruction=(
            "You are TENURE's subject agent proof. Inspect the supplied invoice with "
            "the tool, report evidence and a recommendation, and never claim to grant, "
            "promote, demote, or mutate authority. Return concise JSON."
        ),
        tools=[inspect_invoice_signal],
        generate_content_config=genai_types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=512,
            thinking_config=genai_types.ThinkingConfig(
                thinking_level=genai_types.ThinkingLevel.MINIMAL
            ),
        ),
    )
    return TenureProofAdkApp(agent=agent, enable_tracing=True)


def build_treasury_app() -> agent_engines.AdkApp:
    agent = Agent(
        name="tenure_treasury_agent",
        model=MODEL,
        description="Assesses sandbox payment-release evidence without releasing funds.",
        instruction=(
            "You are TENURE's Treasury Agent. Use the tool to inspect approval, bank, "
            "duplicate, value, and reversibility evidence. Recommend hold or gateway "
            "review and never claim to release payment or mutate authority. Return "
            "concise JSON."
        ),
        tools=[inspect_payment_signal],
        generate_content_config=genai_types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=512,
            thinking_config=genai_types.ThinkingConfig(
                thinking_level=genai_types.ThinkingLevel.MINIMAL
            ),
        ),
    )
    return agent_engines.AdkApp(agent=agent, enable_tracing=True)


def build_supervisor_app() -> agent_engines.AdkApp:
    agent = Agent(
        name="tenure_supervisor_agent",
        model=MODEL,
        description=(
            "Investigates failures, scopes blast radius, recommends demotion, writes "
            "incident narratives, and files escalation."
        ),
        instruction=(
            "You are TENURE's Supervisor Agent. Promotion and enforcement are outside "
            "your control and remain deterministic. When an incident arrives, use the "
            "tools to enumerate its blast radius, decide the narrowest defensible "
            "demotion depth, write an evidence-grounded narrative, and file escalation. "
            "You may recommend containment but must never grant, restore, or expand "
            "authority. Return concise JSON with every tool result."
        ),
        tools=[
            enumerate_blast_radius,
            recommend_demotion,
            write_incident_narrative,
            file_escalation,
        ],
        generate_content_config=genai_types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=1024,
            thinking_config=genai_types.ThinkingConfig(
                thinking_level=genai_types.ThinkingLevel.LOW
            ),
        ),
    )
    return TenureProofAdkApp(agent=agent, enable_tracing=True)


def deploy_agent(
    client: agentplatform.Client,
    *,
    app: agent_engines.AdkApp,
    display_name: str,
    description: str,
    staging_bucket: str,
) -> Any:
    config = deployment_config(
        display_name=display_name,
        description=description,
        staging_bucket=staging_bucket,
        project=TENURE_PROJECT,
    )
    return client.agent_engines.create(
        agent=app,
        config=config,
    )


def deployment_config(
    *,
    display_name: str,
    description: str,
    staging_bucket: str,
    project: str,
    native_runtime_id: str | None = None,
) -> dict[str, Any]:
    env_vars = {
        "GOOGLE_GENAI_USE_VERTEXAI": "TRUE",
        "GOOGLE_CLOUD_LOCATION": "global",
        "TENURE_NATIVE_PROJECT": project,
        "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": "TRUE",
    }
    if native_runtime_id:
        env_vars["TENURE_NATIVE_RUNTIME_ID"] = native_runtime_id
    return {
        "display_name": display_name,
        "description": description,
        "requirements": [
            "google-cloud-aiplatform[agent_engines,adk]==1.165.1",
            "google-adk==2.6.3",
            "cloudpickle==3.1.2",
            "pydantic==2.13.4",
        ],
        "staging_bucket": staging_bucket,
        "identity_type": types.IdentityType.AGENT_IDENTITY,
        "env_vars": env_vars,
        "min_instances": 0,
        "max_instances": 1,
        "labels": {"system": "tenure", "gate": "fleet"},
    }


def update_agent(
    client: agentplatform.Client,
    *,
    name: str,
    app: agent_engines.AdkApp,
    display_name: str,
    description: str,
    staging_bucket: str,
    project: str,
    native_runtime_id: str | None = None,
) -> Any:
    return client.agent_engines.update(
        name=name,
        agent=app,
        config=deployment_config(
            display_name=display_name,
            description=description,
            staging_bucket=staging_bucket,
            project=project,
            native_runtime_id=native_runtime_id,
        ),
    )


def resource_evidence(resource: Any) -> dict[str, Any]:
    api_resource = getattr(resource, "api_resource", None)
    return {
        "name": getattr(api_resource, "name", None),
        "display_name": getattr(api_resource, "display_name", None),
        "description": getattr(api_resource, "description", None),
        "identity_type": str(
            getattr(getattr(api_resource, "spec", None), "identity_type", "")
        ),
    }


async def query_agent(resource: Any, *, user_id: str, message: str) -> list[Any]:
    events = []
    async for event in resource.async_stream_query(user_id=user_id, message=message):
        events.append(event)
    return events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "deploy-vendor",
            "deploy-subject",
            "deploy-treasury",
            "deploy-supervisor",
            "update-vendor",
            "update-subject",
            "update-treasury",
            "update-supervisor",
        ),
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--staging-bucket", required=True)
    parser.add_argument("--resource-name")
    args = parser.parse_args()

    if args.project != TENURE_PROJECT:
        parser.error(f"TENURE deployments are pinned to {TENURE_PROJECT}")

    client = agentplatform.Client(project=args.project, location=args.location)
    is_update = args.command.startswith("update")
    role = args.command.split("-", 1)[1]
    apps = {
        "vendor": build_vendor_app,
        "subject": build_subject_app,
        "treasury": build_treasury_app,
        "supervisor": build_supervisor_app,
    }
    display_names = {
        "vendor": VENDOR_DISPLAY_NAME,
        "subject": SUBJECT_DISPLAY_NAME,
        "treasury": TREASURY_DISPLAY_NAME,
        "supervisor": SUPERVISOR_DISPLAY_NAME,
    }
    descriptions = {
        "vendor": "TENURE vendor-evidence agent with no onboarding or authority tools.",
        "subject": "TENURE invoice-risk subject agent with no authority mutation tools.",
        "treasury": "TENURE payment-risk agent with no release or authority tools.",
        "supervisor": (
            "TENURE failure-investigation agent; deterministic policy applies its "
            "containment recommendations."
        ),
    }
    app = apps[role]()
    display_name = display_names[role]
    description = descriptions[role]
    if is_update:
        if not args.resource_name:
            parser.error("--resource-name is required for update commands")
        resource = update_agent(
            client,
            name=args.resource_name,
            app=app,
            display_name=display_name,
            description=description,
            staging_bucket=args.staging_bucket,
            project=args.project,
            native_runtime_id=(
                args.resource_name.rsplit("/", 1)[-1]
                if role in {"subject", "supervisor"}
                else None
            ),
        )
    else:
        resource = deploy_agent(
            client,
            app=app,
            display_name=display_name,
            description=description,
            staging_bucket=args.staging_bucket,
        )
    print(json.dumps(resource_evidence(resource), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
