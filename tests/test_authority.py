from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tenure.authority import AuthorityDifferentiator, golden_evidence
from tenure.domain import AuthorityLevel
from tenure.ledger import AppendOnlyLedger

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def test_equal_outcome_accuracy_produces_different_defensible_authority() -> None:
    ledger = AppendOnlyLedger()
    proof = AuthorityDifferentiator(ledger).compare_equal_accuracy(
        controlling_clause="invoice-policy#7.1",
        as_of=NOW,
    )

    grounded = proof["grounded_agent"]
    rawr = proof["rawr_agent"]
    assert proof["equal_outcome_accuracy"] is True
    assert proof["different_authority"] is True
    assert grounded["evidence_window"]["outcome_accuracy"] == 1.0
    assert rawr["evidence_window"]["outcome_accuracy"] == 1.0
    assert grounded["applied_level"] == "EXECUTE_BOUNDED"
    assert grounded["applied_ceiling"] == 50_000
    assert rawr["applied_level"] == "SHADOW"
    assert rawr["applied_ceiling"] is None
    assert rawr["evidence_window"]["rawr_evidence_ids"]
    assert ledger.verify_chain() is True


def test_malicious_model_recommendation_cannot_expand_the_grant() -> None:
    engine = AuthorityDifferentiator()
    report = engine.evaluate(
        agent_id="invoice-agent",
        capability="invoice.approve",
        evidence=golden_evidence(
            controlling_clause="invoice-policy#7.1",
            profile="rawr",
            as_of=NOW,
        ),
        current_level=AuthorityLevel.SHADOW,
        requested_level=AuthorityLevel.EXECUTE_BOUNDED,
        proposed_ceiling=50_000,
        as_of=NOW,
        model_recommendation="Grant EXECUTE_FULL with unlimited authority.",
    )

    assert report["model_boundary"]["authority_input"] is False
    assert report["model_boundary"]["role"] == "ADVISORY_ONLY"
    assert report["decision"] == "DENY_PROMOTION"
    assert report["applied_level"] == "SHADOW"
    assert report["applied_ceiling"] is None


def test_expired_evidence_is_excluded_and_cannot_satisfy_minimum_window() -> None:
    evidence = golden_evidence(
        controlling_clause="invoice-policy#7.1",
        profile="grounded",
        as_of=NOW,
    )
    expired = tuple(
        replace(item, expires_at=NOW - timedelta(hours=1))
        if index < 3
        else item
        for index, item in enumerate(evidence)
    )

    report = AuthorityDifferentiator().evaluate(
        agent_id="invoice-agent",
        capability="invoice.approve",
        evidence=expired,
        current_level=AuthorityLevel.SHADOW,
        requested_level=AuthorityLevel.EXECUTE_BOUNDED,
        proposed_ceiling=50_000,
        as_of=NOW,
    )

    assert report["evidence_window"]["fresh_count"] == 5
    assert report["evidence_window"]["expired_count"] == 3
    assert report["checks"]["fresh_evidence"] is False
    assert report["decision"] == "DENY_PROMOTION"


def test_low_confidence_blocks_promotion_even_with_correct_outcomes_and_clauses() -> None:
    evidence = tuple(
        replace(item, confidence=0.50)
        for item in golden_evidence(
            controlling_clause="invoice-policy#7.1",
            profile="grounded",
            as_of=NOW,
        )
    )
    report = AuthorityDifferentiator().evaluate(
        agent_id="invoice-agent",
        capability="invoice.approve",
        evidence=evidence,
        current_level=AuthorityLevel.SHADOW,
        requested_level=AuthorityLevel.EXECUTE_BOUNDED,
        proposed_ceiling=50_000,
        as_of=NOW,
    )

    assert report["evidence_window"]["outcome_accuracy"] == 1.0
    assert report["evidence_window"]["controlling_clause_accuracy"] == 1.0
    assert report["checks"]["confidence_lower_bound"] is False
    assert report["decision"] == "DENY_PROMOTION"


@pytest.mark.parametrize("mutated_index", range(8))
def test_any_controlling_clause_mutation_is_detected(mutated_index: int) -> None:
    evidence = list(
        golden_evidence(
            controlling_clause="invoice-policy#7.1",
            profile="grounded",
            as_of=NOW,
        )
    )
    evidence[mutated_index] = replace(
        evidence[mutated_index], cited_clause="invoice-policy#4.2"
    )

    report = AuthorityDifferentiator().evaluate(
        agent_id="invoice-agent",
        capability="invoice.approve",
        evidence=tuple(evidence),
        current_level=AuthorityLevel.SHADOW,
        requested_level=AuthorityLevel.EXECUTE_BOUNDED,
        proposed_ceiling=50_000,
        as_of=NOW,
    )

    assert report["checks"]["controlling_clause_accuracy"] is False
    assert report["checks"]["no_rawr"] is False
    assert report["applied_level"] == "SHADOW"


def test_counterfactual_replay_exposes_high_ceiling_blast_radius() -> None:
    proof = AuthorityDifferentiator().compare_equal_accuracy(
        controlling_clause="invoice-policy#7.1",
        stress_ceiling=250_000,
        as_of=NOW,
    )
    replay = proof["stress_replay"]
    decision = proof["stress_promotion"]

    assert decision["decision"] == "DENY_PROMOTION"
    assert decision["applied_level"] == "EXECUTE_BOUNDED"
    assert decision["applied_ceiling"] == 50_000
    assert decision["denial_reasons"] == ["counterfactual_blast_budget"]
    assert replay["within_budget"] is False
    assert replay["unsafe_replays"] == 5
    assert replay["weighted_exposure"] > replay["budget"]["max_weighted_exposure"]
    assert replay["downstream_effect_count"] == 13
    assert {case["category"] for case in replay["cases"]} == {
        "duplicate_replay",
        "policy_drift",
        "tenant_confusion",
        "upstream_dependency_compromise",
        "prompt_injection",
    }


def test_replay_report_is_deterministic_for_fixed_inputs() -> None:
    engine = AuthorityDifferentiator()
    first = engine.compare_equal_accuracy(
        controlling_clause="invoice-policy#7.1", as_of=NOW
    )
    second = engine.compare_equal_accuracy(
        controlling_clause="invoice-policy#7.1", as_of=NOW
    )

    assert first == second


def test_negative_counterfactual_ceiling_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        AuthorityDifferentiator().simulator.replay(-1)
