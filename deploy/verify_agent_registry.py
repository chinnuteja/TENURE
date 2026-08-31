"""Resolve deployed TENURE Runtime resources through Google Agent Registry."""

from __future__ import annotations

import argparse
import json
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession


def contains_runtime(agent: dict[str, Any], runtime_id: str) -> bool:
    return runtime_id in json.dumps(agent, sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", default="us-central1")
    parser.add_argument("--runtime-id", action="append", required=True)
    args = parser.parse_args()

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    response_http = session.get(
        (
            "https://agentregistry.googleapis.com/v1/"
            f"projects/{args.project}/locations/{args.location}/agents"
        ),
        params={"pageSize": 100},
        headers={"x-goog-user-project": args.project},
        timeout=30,
    )
    response_http.raise_for_status()
    response = response_http.json()
    agents = response.get("agents", [])
    resolved: dict[str, Any] = {}
    for runtime_id in args.runtime_id:
        matches = [agent for agent in agents if contains_runtime(agent, runtime_id)]
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one Registry entry for Runtime {runtime_id}; found {len(matches)}"
            )
        agent = matches[0]
        resolved[runtime_id] = {
            "name": agent.get("name"),
            "display_name": agent.get("displayName"),
            "description": agent.get("description"),
            "interfaces": agent.get("interfaces", []),
        }
    print(json.dumps({"resolved": resolved}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
