"""Run one bounded native Agent Identity pair and save non-sensitive evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tenure.native_proof import NativeIdentityProof

OUTPUT = Path("data/gauntlet/native-identity-live.json")


def verify() -> dict:
    """Execute exactly one owner/other pair; the proof object performs no retries."""
    report = NativeIdentityProof(enabled=True).run()
    if report.get("status") != "PASS":
        raise RuntimeError(json.dumps(report, sort_keys=True))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Acknowledge two live Runtime calls against the pinned TENURE memory.",
    )
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required; no live calls were made")
    report = verify()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
