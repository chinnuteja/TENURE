from __future__ import annotations

from dataclasses import replace

import pytest

from tenure.fleet import ProcureToPayFleet, TenantBoundaryError
from tenure.ledger import AppendOnlyLedger


def test_case_crosses_three_departments_with_real_mutations_and_receipts() -> None:
    fleet = ProcureToPayFleet()

    result = fleet.run_case(tenant_id="tenant-northstar", case_id="case-100")
    audit = fleet.audit_case("tenant-northstar", "case-100")

    assert result["complete"] is True
    assert result["ledger_integrity"] is True
    assert len(result["agents"]) == 4
    assert len({agent["identity_resource"] for agent in result["agents"]}) == 4
    assert {agent["department"] for agent in result["agents"]} == {
        "Procurement",
        "Accounts Payable",
        "Finance",
        "Enterprise Risk",
    }
    assert result["state"]["vendor"]["status"] == "ONBOARDED"
    assert result["state"]["invoice"]["status"] == "APPROVED"
    assert result["state"]["payment"]["status"] == "RELEASED_SANDBOX"
    assert result["state"]["payment"]["reversible"] is True
    assert result["persistence"] == "memory"
    assert len(result["receipts"]) == 3
    assert {receipt["gateway_decision"] for receipt in result["receipts"]} == {
        "ALLOW"
    }
    assert audit["receipt_count"] == 3
    assert audit["ledger_integrity"] is True
    assert all(event["payload"]["tenant_id"] == "tenant-northstar" for event in audit["events"])


def test_passports_are_signed_and_bound_to_build_policy_and_tenant() -> None:
    fleet = ProcureToPayFleet()
    result = fleet.run_case(tenant_id="tenant-a", case_id="case-passport")

    issued = result["passports"]
    assert len(issued) == 3
    assert all(
        passport["schema_version"] == "tenure.capability-passport/v2"
        for passport in issued
    )
    assert all(passport["policy_revision"] == fleet.POLICY_REVISION for passport in issued)
    assert all(passport["grant"]["level"] == "EXECUTE_BOUNDED" for passport in issued)
    assert all(passport["evidence_window"]["fresh_count"] == 8 for passport in issued)
    assert all(
        passport["counterfactual"]["within_budget"] is True for passport in issued
    )
    assert all(passport["expires_at"] > passport["issued_at"] for passport in issued)

    passports = fleet._issue_passports(
        "tenant-a",
        "case-tamper",
        fleet._earn_operating_grants("vendor-tamper"),
    )
    passport = passports["invoice-agent"]
    assert fleet.passport_issuer.verify(passport) is True
    assert fleet.passport_issuer.verify(replace(passport, tenant_id="tenant-b")) is False
    assert (
        fleet.passport_issuer.verify(
            replace(passport, evidence_window={"fresh_count": 999})
        )
        is False
    )


def test_fleet_authority_proof_is_auditable_and_uses_no_model_call() -> None:
    fleet = ProcureToPayFleet()

    proof = fleet.authority_proof(tenant_id="tenant-a")

    assert proof["equal_outcome_accuracy"] is True
    assert proof["different_authority"] is True
    assert proof["model_calls"] == 0
    assert proof["ledger_integrity"] is True
    assert len(fleet.ledger.find("AUTHORITY_PROMOTION_EVALUATED")) == 3


def test_dependency_graph_exposes_transitive_vendor_to_payment_blast_path() -> None:
    fleet = ProcureToPayFleet()

    path = fleet.dependencies.downstream(
        "vendor-intelligence-agent", "vendor.onboard"
    )

    assert [edge["downstream_capability"] for edge in path] == [
        "invoice.approve",
        "payment.release",
    ]


def test_tenant_boundary_blocks_cross_tenant_entity_lookup() -> None:
    fleet = ProcureToPayFleet()
    first = fleet.run_case(tenant_id="tenant-a", case_id="case-isolated")
    vendor_id = first["state"]["vendor"]["vendor_id"]

    with pytest.raises(TenantBoundaryError):
        fleet.sandbox.onboard_vendor("tenant-b", vendor_id)

    assert first["state"]["vendor"]["tenant_id"] == "tenant-a"
    assert fleet.audit_case("tenant-a", "case-isolated")["tenant_id"] == "tenant-a"
    with pytest.raises(TenantBoundaryError):
        fleet.audit_case("tenant-b", "case-isolated")


def test_case_id_is_idempotent_and_does_not_duplicate_receipts() -> None:
    fleet = ProcureToPayFleet()
    first = fleet.run_case(tenant_id="tenant-a", case_id="case-repeat")
    second = fleet.run_case(tenant_id="tenant-a", case_id="case-repeat")

    assert second == first
    assert len(fleet.ledger.find("CAPABILITY_RECEIPT_ISSUED")) == 3


def test_registry_composes_real_cloud_identity_when_runtime_is_configured(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT_NUMBER", "123")
    monkeypatch.setenv("GOOGLE_CLOUD_ORGANIZATION_ID", "456")
    monkeypatch.setenv("TENURE_RESOURCE_LOCATION", "us-central1")
    monkeypatch.setenv("TENURE_VENDOR_RUNTIME_ID", "789")
    monkeypatch.setenv("TENURE_VENDOR_REGISTRY_RESOURCE", "registry/vendor")

    vendor = ProcureToPayFleet().registry.get("vendor-intelligence-agent")

    assert vendor.runtime_resource == (
        "projects/123/locations/us-central1/reasoningEngines/789"
    )
    assert vendor.identity_resource.startswith("agents.global.org-456.system.id.goog/")
    assert vendor.registry_resource == "registry/vendor"


def test_audit_survives_fleet_orchestrator_reconstruction() -> None:
    ledger = AppendOnlyLedger()
    first = ProcureToPayFleet(ledger=ledger)
    first.run_case(tenant_id="tenant-a", case_id="case-durable-audit")

    reconstructed = ProcureToPayFleet(ledger=ledger)
    audit = reconstructed.audit_case("tenant-a", "case-durable-audit")

    assert audit["receipt_count"] == 3
    assert audit["ledger_integrity"] is True


def test_case_is_idempotent_after_orchestrator_reconstruction() -> None:
    ledger = AppendOnlyLedger()
    first_fleet = ProcureToPayFleet(ledger=ledger)
    first = first_fleet.run_case(
        tenant_id="tenant-a", case_id="case-restart-idempotent"
    )

    reconstructed = ProcureToPayFleet(
        ledger=ledger,
        sandbox=first_fleet.sandbox,
    )
    second = reconstructed.run_case(
        tenant_id="tenant-a", case_id="case-restart-idempotent"
    )

    assert second["complete"] is True
    assert second["state"] == first["state"]
    assert len(ledger.find("CAPABILITY_RECEIPT_ISSUED")) == 3
