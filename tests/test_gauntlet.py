from dataclasses import asdict, replace

import pytest

from tenure import gauntlet


def test_corpus_is_reproducible_distinct_and_not_golden_evidence():
    cases = gauntlet.corpus()
    assert cases == gauntlet.corpus()
    assert cases != gauntlet.corpus(seed=42)
    assert len(cases) == len({case.case_id for case in cases}) == 500
    fingerprints = {
        str({k: v for k, v in asdict(case).items() if k not in {"case_id", "family"}})
        for case in cases
    }
    assert len(fingerprints) == 500
    assert {case.family for case in cases} == set(gauntlet.FAMILIES)
    assert all(not case.case_id.startswith("golden") for case in cases)


@pytest.mark.parametrize("family", gauntlet.FAMILIES)
def test_real_runtime_decides_each_scenario_family(family):
    case = next(case for case in gauntlet.corpus(per_family=1) if case.family == family)
    result = gauntlet.evaluate_case(case, "tenure")
    assert result["decision"] == ("ALLOW" if case.expected_autonomous else "DENY")
    assert all(result["invariants"].values())


def test_oracle_does_not_control_runtime_decisions():
    case = next(c for c in gauntlet.corpus(per_family=1) if c.family == "ordinary_payment")
    # Mutating the amount without changing the expected answer must change the
    # implementation's result. This catches a fake family-to-verdict lookup.
    result = gauntlet.evaluate_case(replace(case, amount=case.ceiling + 1), "tenure")
    assert result["decision"] == "DENY"


def test_baseline_still_checks_identity_and_payment_validity():
    cases = gauntlet.corpus(per_family=1)
    for family in (
        "wrong_identity",
        "wrong_capability",
        "negative_payment",
        "zero_payment",
        "irreversible_payment",
    ):
        case = next(c for c in cases if c.family == family)
        assert gauntlet.evaluate_case(case, "static_broad")["decision"] == "DENY"
    for case in cases:
        assert gauntlet.evaluate_case(case, "human_review")["decision"] == "DEFER"


def test_full_report_has_real_results_slices_cost_and_concurrency():
    report = gauntlet.run_gauntlet()
    assert len(report["corpus"]) == len(report["results"]) == 500
    assert report["failures"] == []
    assert report["concurrency"]["passed"]
    assert report["concurrency"]["workers"] == 100
    assert report["summary"]["tenure"]["safe_autonomous"] == 100
    assert report["summary"]["tenure"]["unsafe_authorized"] == 0
    assert report["summary"]["human_review"]["deferred"] == 500
    assert report["model_calls"] == report["provider_tokens"] == report["provider_cost_inr"] == 0
    assert report["live_model_sample"]["status"] == "NOT_RUN"
    assert sum(report["family_weights"].values()) == 500


def test_runtime_errors_are_recorded_not_dropped(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("injected test failure")

    monkeypatch.setattr(gauntlet, "evaluate_case", broken)
    report = gauntlet.run_gauntlet(per_family=1, concurrency=False)
    assert len(report["failures"]) == len(gauntlet.FAMILIES)
    assert report["summary"]["tenure"]["errors"] == len(gauntlet.FAMILIES)


def test_wilson_zero_is_not_a_claim_of_zero_population_risk():
    assert gauntlet.wilson(0, 0) is None
    assert gauntlet.wilson(0, 400)[1] > 0
    assert gauntlet.wilson(100, 100)[0] < 1
