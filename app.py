"""Vercel entrypoint for the public TENURE demo."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# Vercel functions have ephemeral writable storage only under /tmp. The public
# deployment intentionally uses the deterministic demo Supervisor so it needs
# no API key and cannot create unexpected model spend.
os.environ.setdefault("TENURE_RUNTIME", "local")
os.environ.setdefault("TENURE_SUPERVISOR_PROVIDER", "fixture")
os.environ.setdefault(
    "TENURE_DATA_DIR",
    "/tmp/tenure/runs" if os.getenv("VERCEL") else str(ROOT / ".tenure" / "vercel-runs"),
)

from tenure.api import app  # noqa: E402

__all__ = ["app"]
