"""One bounded Gemini recovery against an authenticated TENURE repair canary.

Never retries the recovery POST. Polls only the new tenant's authoritative freeze
document to test traffic during investigation. Writes evidence, never tokens.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import requests
from google.cloud import firestore

PROJECT = "project-ceca895d-33b0-44b9-b5a"
CANARY = "https://repair---tenure-control-room-f5uq25dprq-uc.a.run.app"


def verify(url: str) -> dict:
    if url != CANARY:
        raise ValueError("probe is restricted to the observed TENURE repair tag")
    gcloud = shutil.which("gcloud.cmd") or shutil.which("gcloud")
    if not gcloud:
        raise RuntimeError("gcloud is required for authenticated Cloud Run verification")
    token = subprocess.run(
        [gcloud, "auth", "print-identity-token"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    headers = {"Authorization": f"Bearer {token}"}
    tenant_id = f"tenant-repair-{uuid4().hex[:10]}"
    case_id = f"case-{uuid4().hex[:10]}"
    responses = {}

    def call(label, method, path, *, params=None, expected=200):
        response = requests.request(
            method, url + path, headers=headers, params=params, timeout=295,
        )
        try:
            body = response.json()
        except ValueError:
            body = {"body": response.text[:500]}
        responses[label] = {"status": response.status_code, "body": body}
        if response.status_code != expected:
            raise RuntimeError(f"{label}: expected {expected}, got {response.status_code}: {body}")
        return body

    try:
        unauth = requests.get(url + "/api/health", timeout=30)
        assert unauth.status_code == 403, "canary must not allow unauthenticated access"
        assert call("health", "GET", "/api/health")["mode"] == "GOOGLE_CLOUD_LIVE"
        platform = call("platform", "GET", "/api/platform")
        assert platform["cloud_run"]["revision"] == "tenure-control-room-repair-3bb34bf"
        assert platform["project_id"] == PROJECT
        original = call(
            "original", "POST", f"/api/fleet/cases/{case_id}",
            params={"tenant_id": tenant_id},
        )
        assert original["complete"] and original["persistence"] == "firestore"
        print(f"Canary case complete: {tenant_id}/{case_id}", flush=True)
        client = firestore.Client(project=PROJECT, database="tenure")
        ref = client.collection("tenure_p2p_authority").document(
            hashlib.sha256(tenant_id.encode()).hexdigest(),
        )

        def recover():
            return call(
                "recovery", "POST", f"/api/recovery/cases/{case_id}",
                params={"tenant_id": tenant_id, "scenario": "upstream_compromise"},
            )

        freeze_seen = False
        with ThreadPoolExecutor(max_workers=1) as pool:
            recovery_future = pool.submit(recover)
            for _ in range(60):
                state = ref.get(timeout=10, retry=None).to_dict() or {}
                if state and all(entry.get("freezes") for entry in state.values()):
                    freeze_seen = True
                    denied = call(
                        "during_freeze", "POST", "/api/fleet/cases/during-freeze",
                        params={"tenant_id": tenant_id}, expected=403,
                    )
                    assert "frozen" in denied["detail"], "denial must occur during active freeze"
                    print("Fresh traffic denied while durable containment is active", flush=True)
                    break
                if recovery_future.done():
                    break
                time.sleep(1)
            recovered = recovery_future.result(timeout=295)
        assert freeze_seen, "did not observe the active freeze window"
        assert recovered["reasoner_mode"] == "GEMINI_ADK"
        assert recovered["memory_retrieval_verified"]
        assert len(recovered["tool_categories"]) == 7
        assert recovered["freeze_preceded_supervision"] and recovered["ledger_integrity"]
        assert set(recovered["authority_after"].values()) == {"OBSERVE"}
        assert len(recovered["rollback_results"]) == 3
        assert len(recovered["escalation_action_ids"]) == 1
        print("Live Gemini recovery and actual compensation verified", flush=True)

        call("reconstruct", "POST", "/api/fleet/proof/reconstruct")
        replay = call(
            "replay", "POST", f"/api/fleet/cases/{case_id}", params={"tenant_id": tenant_id},
        )
        assert replay["state"] == recovered["state_after"]
        call(
            "after_demotion", "POST", "/api/fleet/cases/after-demotion",
            params={"tenant_id": tenant_id}, expected=403,
        )
        independent = call(
            "other_tenant", "POST", f"/api/fleet/cases/{case_id}",
            params={"tenant_id": tenant_id + "-independent"},
        )
        assert independent["complete"]
        audit = call(
            "audit", "GET", f"/api/fleet/cases/{case_id}/audit", params={"tenant_id": tenant_id},
        )
        assert audit["receipt_count"] == 3 and audit["ledger_integrity"]
        return {
            "status": "PASS", "verified_at": datetime.now(UTC).isoformat(),
            "project_id": PROJECT, "url": url, "revision": "tenure-control-room-repair-3bb34bf",
            "tenant_id": tenant_id, "case_id": case_id,
            "supervisor_invocations": 1,
            "actual_cost": "not measured; one build and one Supervisor invocation plus services",
            "checks": {
                "unauthenticated_denied": True, "live_gemini_recovery": True,
                "seven_tool_categories": True, "verified_memory": True,
                "active_freeze_denied_new_traffic": True, "three_actual_rollbacks": True,
                "irreversible_escalation": True, "demotion_survived_reconstruction": True,
                "replay_reflects_compensation": True, "unrelated_tenant_completes": True,
                "no_duplicate_original_receipts": True, "valid_ledger": True,
            },
            "responses": responses,
        }
    except Exception as exc:
        return {
            "status": "FAIL", "error": str(exc), "tenant_id": tenant_id,
            "case_id": case_id, "responses": responses,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", choices=[CANARY], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "responses"}, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
