"""FastAPI surface for the local dashboard and future Cloud Run deployment."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tenure.fleet_control import CaseConflict, CaseInProgress
from tenure.native_proof import NativeIdentityProof, ProofBusy, ProofUnavailable
from tenure.platform import platform_evidence
from tenure.recovery import RecoveryPolicyError, RecoveryScenario
from tenure.runtime import (
    build_runtime_fleet,
    build_runtime_recovery,
    build_runtime_scenario,
    cloud_readiness,
)
from tenure.scenario import TenureScenario

STATIC_DIR = Path(__file__).with_name("static")


def create_app(scenario: TenureScenario | None = None) -> FastAPI:
    app = FastAPI(
        title="TENURE",
        version="0.1.0",
        description="Continuous earned authority for enterprise agents.",
    )
    runtime = scenario or build_runtime_scenario()
    fleet = build_runtime_fleet()
    recovery = build_runtime_recovery(fleet)
    identity_proof = NativeIdentityProof()
    app.state.identity_proof = identity_proof
    app.state.scenario = runtime
    app.state.fleet = fleet
    app.state.recovery = recovery
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/proof", include_in_schema=False)
    @app.get("/platform", include_in_schema=False)
    @app.get("/conformance", include_in_schema=False)
    @app.get("/limitations", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "fleet.html")

    @app.get("/kernel", include_in_schema=False)
    def kernel_dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/gauntlet")
    def gauntlet_report() -> dict:
        path = STATIC_DIR / "gauntlet-report.json"
        if not path.exists():
            return {
                "status": "NOT_RUN",
                "message": "Generate the local report with python -m tenure.gauntlet",
            }
        report = json.loads(path.read_text(encoding="utf-8"))
        return {key: value for key, value in report.items() if key not in {"corpus", "results"}}

    @app.get("/api/health")
    def health() -> dict[str, object]:
        snapshot = runtime.snapshot()
        return {
            "service": "tenure",
            "status": "ok",
            "mode": snapshot["mode"],
            "ledger_integrity": snapshot["ledger_integrity"],
            "operating_mode": "DETERMINISTIC_FIXTURES",
            "supervisor_mode": recovery.reasoner.mode,
            "fleet_persistence": fleet.sandbox.persistence,
        }

    @app.get("/api/scenario")
    def get_scenario() -> dict[str, object]:
        return runtime.snapshot()

    @app.post("/api/scenario/reset")
    def reset_scenario() -> dict[str, object]:
        return runtime.reset()

    @app.post("/api/scenario/advance")
    def advance_scenario() -> dict[str, object]:
        return runtime.advance()

    @app.post("/api/scenario/run")
    def run_scenario() -> dict[str, object]:
        return runtime.run_all()

    @app.get("/api/ledger")
    def get_ledger() -> dict[str, object]:
        return {
            "integrity": runtime.ledger.verify_chain(),
            "events": list(runtime.ledger.export()),
        }

    @app.get("/api/receipts")
    def get_receipts() -> dict[str, object]:
        return {
            "receipts": [event.payload for event in runtime.ledger.find("ACTION_TRUST_RECEIPT")]
        }

    @app.get("/api/evidence")
    def get_evidence() -> dict[str, object]:
        return runtime.evidence_report()

    @app.get("/api/cloud-readiness")
    def get_cloud_readiness() -> dict[str, object]:
        return cloud_readiness()

    @app.get("/api/platform")
    def get_platform_evidence() -> dict[str, object]:
        return platform_evidence()

    @app.get("/api/proofs/identity")
    def identity_proof_status() -> dict:
        return identity_proof.status()

    @app.post("/api/proofs/identity")
    def run_identity_proof() -> dict:
        try:
            return identity_proof.run()
        except ProofUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProofBusy as exc:
            raise HTTPException(
                status_code=429, detail=str(exc), headers={"Retry-After": "60"},
            ) from exc

    @app.get("/api/fleet/registry")
    def get_fleet_registry() -> dict[str, object]:
        return {
            "agents": fleet.registry.discover(),
            "dependency_edges": [edge.snapshot() for edge in fleet.dependencies.edges],
        }

    @app.post("/api/fleet/cases/{case_id}")
    def run_fleet_case(
        case_id: str,
        tenant_id: str,
        amount: int = Query(default=18_400, ge=1, le=50_000),
    ) -> dict[str, object]:
        try:
            return fleet.run_case(tenant_id=tenant_id, case_id=case_id, amount=amount)
        except CaseInProgress as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
                headers={"Retry-After": "2"},
            ) from exc
        except CaseConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/fleet/cases/{case_id}/audit")
    def audit_fleet_case(case_id: str, tenant_id: str) -> dict[str, object]:
        try:
            audit = fleet.audit_case(tenant_id, case_id)
            return {**audit, "current_authority": fleet.control.snapshot(tenant_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="Tenant boundary denied") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Fleet case not found") from exc

    @app.post("/api/fleet/proof/reconstruct")
    def reconstruct_fleet_from_durable_state() -> dict[str, object]:
        nonlocal fleet, recovery
        if fleet.sandbox.persistence != "firestore":
            raise HTTPException(
                status_code=409,
                detail="Durable reconstruction is available only in cloud mode",
            )
        fleet = build_runtime_fleet()
        recovery = build_runtime_recovery(fleet)
        app.state.fleet = fleet
        app.state.recovery = recovery
        return {
            "reconstructed": True,
            "persistence": fleet.sandbox.persistence,
            "ledger_integrity": fleet.ledger.verify_chain(),
        }

    @app.post("/api/authority/proof")
    def run_authority_proof(
        tenant_id: str,
        stress_ceiling: int = 250_000,
    ) -> dict[str, object]:
        if stress_ceiling < 0 or stress_ceiling > 10_000_000:
            raise HTTPException(
                status_code=422,
                detail="Stress ceiling must be between 0 and 10000000",
            )
        return fleet.authority_proof(
            tenant_id=tenant_id,
            stress_ceiling=stress_ceiling,
        )

    @app.post("/api/recovery/cases/{case_id}")
    def run_fleet_recovery(
        case_id: str,
        tenant_id: str,
        scenario: str = RecoveryScenario.UPSTREAM_COMPROMISE.value,
        amount: int = Query(default=18_400, ge=1, le=50_000),
    ) -> dict[str, object]:
        try:
            selected = RecoveryScenario(scenario)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Unknown recovery scenario") from exc
        try:
            return recovery.run(
                tenant_id=tenant_id,
                case_id=case_id,
                scenario=selected,
                amount=amount,
            )
        except RecoveryPolicyError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Supervisor proposal rejected: {exc}",
            ) from exc
        except CaseConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("tenure.api:app", host="127.0.0.1", port=8000, reload=False)
