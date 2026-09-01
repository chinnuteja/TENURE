"""Vercel entrypoint for the public TENURE demo."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# Vercel functions have ephemeral writable storage only under /tmp. The public
# deployment intentionally uses the deterministic demo Supervisor so it needs
# no API key and cannot create unexpected model spend. Force these values in
# Vercel so stale project-level cloud variables cannot crash the public demo.
if os.getenv("VERCEL"):
    os.environ["TENURE_RUNTIME"] = "local"
    os.environ["TENURE_SUPERVISOR_PROVIDER"] = "fixture"
    os.environ["TENURE_DATA_DIR"] = str(Path(tempfile.gettempdir()) / "tenure" / "runs")
else:
    os.environ.setdefault("TENURE_RUNTIME", "local")
    os.environ.setdefault("TENURE_SUPERVISOR_PROVIDER", "fixture")
    os.environ.setdefault("TENURE_DATA_DIR", str(ROOT / ".tenure" / "vercel-runs"))

app = FastAPI(title="TENURE")

try:
    from tenure.api import app as tenure_app  # noqa: E402
except Exception as exc:  # pragma: no cover - exercised only by deployment packaging
    startup_error = {
        "error_type": type(exc).__name__,
        "message": str(exc)[:500],
    }

    @app.get("/{path:path}")
    def deployment_diagnostic(path: str) -> dict[str, object]:
        return {
            "service": "tenure",
            "status": "startup_error",
            "path": path,
            **startup_error,
        }
else:
    app.mount("/", tenure_app)

__all__ = ["app"]
