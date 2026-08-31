from __future__ import annotations

from pathlib import Path

import pytest

from tenure.adk_supervisor import build_root_agent
from tenure.domain import ActionProposal
from tenure.ledger import AppendOnlyLedger, SqliteLedger, TrustLedger
from tenure.supervisor_tools import SupervisorToolbox


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_ledger_contract(kind: str, tmp_path: Path) -> None:
    ledger: TrustLedger
    ledger = AppendOnlyLedger() if kind == "memory" else SqliteLedger(tmp_path / "tenure.db")
    first = ledger.append("ONE", {"value": 1})
    second = ledger.append("TWO", {"value": 2})

    assert first.sequence == 1
    assert second.sequence == 2
    assert second.previous_hash == first.event_hash
    assert len(ledger.find("TWO", value=2)) == 1
    assert ledger.verify_chain()


def test_sqlite_ledger_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "tenure.db"
    SqliteLedger(path).append("PERSISTED", {"ok": True})

    reopened = SqliteLedger(path)
    assert len(reopened.events) == 1
    assert reopened.events[0].payload == {"ok": True}
    assert reopened.verify_chain()


def test_supervisor_toolbox_exposes_only_bounded_recovery_tools() -> None:
    ledger = AppendOnlyLedger()
    reversible = ActionProposal("agent", "invoice.approve", 1, "vendor", "policy", True)
    irreversible = ActionProposal("agent", "invoice.approve", 2, "vendor", "policy", False)
    toolbox = SupervisorToolbox(ledger, [reversible, irreversible])

    tool_names = {tool.__name__ for tool in toolbox.adk_tools()}
    assert tool_names == {
        "query_incident_evidence",
        "enumerate_blast_radius",
        "request_compensating_rollback",
        "file_irreversible_escalation",
    }
    assert all("promot" not in name and "credential" not in name for name in tool_names)
    assert toolbox.request_compensating_rollback("incident", reversible.action_id)[
        "accepted"
    ]
    assert not toolbox.request_compensating_rollback("incident", irreversible.action_id)[
        "accepted"
    ]


def test_adk_supervisor_is_constructed_with_bounded_tools() -> None:
    agent = build_root_agent(SupervisorToolbox(AppendOnlyLedger()))
    assert agent.name == "tenure_supervisor"
    tool_names = {
        getattr(tool, "name", None) or getattr(tool, "__name__", "")
        for tool in agent.tools
    }
    assert tool_names == {
        "query_incident_evidence",
        "enumerate_blast_radius",
        "request_compensating_rollback",
        "file_irreversible_escalation",
    }
