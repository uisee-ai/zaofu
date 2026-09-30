"""Deterministic contracts for the optional k6 performance tier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.testkit.performance import (
    PerformanceSummaryError,
    PerformanceThreshold,
    evaluate_k6_summary,
    load_k6_summary,
    parse_k6_summary,
)


def _summary(*, p95: float = 120.0, error_rate: float = 0.002) -> dict:
    return {
        "metrics": {
            "http_req_duration": {
                "type": "trend",
                "values": {"p(95)": p95, "count": 10},
                "thresholds": {"p(95)<200": {"ok": p95 < 200}},
            },
            "http_req_failed": {
                "type": "rate",
                "values": {"rate": error_rate, "passes": 998, "fails": 2},
                "thresholds": {"rate<0.01": {"ok": error_rate < 0.01}},
            },
        }
    }


def _thresholds() -> tuple[PerformanceThreshold, ...]:
    return (
        PerformanceThreshold("http_req_duration", "p(95)", "<", 200),
        PerformanceThreshold("http_req_failed", "rate", "<", 0.01),
    )


def test_k6_summary_thresholds_pass_with_finite_samples() -> None:
    verdict = evaluate_k6_summary(_summary(), _thresholds())
    assert verdict.status == "pass"
    assert len(verdict.checked) == 2
    assert not verdict.violations


def test_legacy_k6_rate_metrics_derive_rate_from_passes_and_fails() -> None:
    summary = {
        "metrics": {
            "checks": {
                "passes": 4,
                "fails": 0,
                "thresholds": {"rate>0.99": False},
            },
        }
    }
    verdict = evaluate_k6_summary(
        summary, (PerformanceThreshold("checks", "rate", ">", 0.99),)
    )
    assert verdict.status == "pass"


def test_k6_threshold_violation_is_product_performance_failure() -> None:
    verdict = evaluate_k6_summary(_summary(p95=250), _thresholds(), exit_code=99)
    assert verdict.status == "fail"
    assert "actual=250" in verdict.violations[0]


def test_exit_99_without_reported_threshold_failure_is_harness_failure() -> None:
    verdict = evaluate_k6_summary(_summary(), _thresholds(), exit_code=99)
    assert verdict.status == "harness_failure"
    assert "all thresholds passed" in verdict.reason


def test_missing_structured_threshold_result_is_harness_failure() -> None:
    summary = _summary()
    del summary["metrics"]["http_req_duration"]["thresholds"]
    verdict = evaluate_k6_summary(summary, _thresholds())
    assert verdict.status == "harness_failure"
    assert "missing threshold result" in verdict.reason


def test_zero_sample_metric_cannot_pass_even_when_value_is_present() -> None:
    summary = _summary()
    summary["metrics"]["http_req_duration"]["values"]["count"] = 0
    verdict = evaluate_k6_summary(summary, _thresholds())
    assert verdict.status == "harness_failure"
    assert "no samples" in verdict.reason


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"timed_out": True}, "timed out"),
        ({"exit_code": 7}, "unexplained code 7"),
    ],
)
def test_k6_runtime_failure_is_harness_failure(kwargs: dict, reason: str) -> None:
    verdict = evaluate_k6_summary(_summary(), _thresholds(), **kwargs)
    assert verdict.status == "harness_failure"
    assert reason in verdict.reason


def test_empty_or_missing_metric_is_harness_failure() -> None:
    assert evaluate_k6_summary({"metrics": {}}, _thresholds()).status == "harness_failure"
    assert evaluate_k6_summary(_summary(), ()).status == "harness_failure"
    missing = evaluate_k6_summary(
        {"metrics": {"http_req_duration": {"values": {"p(95)": 1}}}},
        _thresholds(),
    )
    assert missing.status == "harness_failure"
    assert "sample count" in missing.reason


def test_non_finite_and_malformed_values_are_rejected() -> None:
    with pytest.raises(PerformanceSummaryError, match="finite"):
        parse_k6_summary({"metrics": {"x": {"values": {"avg": float("nan")}}}})
    with pytest.raises(PerformanceSummaryError, match="non-empty"):
        parse_k6_summary({"metrics": {}})


@pytest.mark.parametrize("limit", [True, "200"])
def test_threshold_limit_must_be_numeric(limit: object) -> None:
    with pytest.raises(PerformanceSummaryError, match="numeric"):
        PerformanceThreshold("latency", "p(95)", "<", limit)  # type: ignore[arg-type]


def test_k6_summary_file_is_read_as_structured_evidence(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(_summary()), encoding="utf-8")
    parsed = load_k6_summary(path)
    assert parsed["http_req_duration"]["p(95)"] == 120


def test_k6_v1_machine_summary_is_normalized_and_evaluated() -> None:
    summary = {
        "version": "1.0.0",
        "metadata": {"generatedAt": "2026-09-12T00:00:00Z", "k6Version": "1.5.0"},
        "config": {"execution": "local", "duration": 1},
        "results": {
            "metrics": [{
                "name": "requests",
                "type": "counter",
                "contains": "default",
                "values": {"count": 1},
                "thresholds": {"count>0": {"ok": True}},
            }],
        },
    }
    threshold = PerformanceThreshold("requests", "count", ">", 0)
    assert evaluate_k6_summary(summary, (threshold,)).status == "pass"


def test_k6_v1_trend_without_sample_count_fails_closed() -> None:
    summary = {
        "version": "1.0.0",
        "metadata": {"generatedAt": "2026-09-12T00:00:00Z", "k6Version": "1.5.0"},
        "config": {"execution": "local", "duration": 1},
        "results": {"metrics": [{
            "name": "latency",
            "type": "trend",
            "contains": "time",
            "values": {"p(95)": 10},
            "thresholds": {"p(95)<20": {"ok": True}},
        }]},
    }
    verdict = evaluate_k6_summary(
        summary, (PerformanceThreshold("latency", "p(95)", "<", 20),)
    )
    assert verdict.status == "harness_failure"
    assert "sample count" in verdict.reason


def test_k6_v1_summary_requires_top_level_and_metric_contract() -> None:
    summary = {
        "metadata": {"generatedAt": "2026-09-12T00:00:00Z", "k6Version": "1.5.0"},
        "config": {"execution": "local", "duration": 1},
        "results": {"metrics": [{"name": "requests"}]},
    }
    verdict = evaluate_k6_summary(
        summary, (PerformanceThreshold("requests", "count", ">", 0),)
    )
    assert verdict.status == "harness_failure"
    assert "version" in verdict.reason


def test_malformed_legacy_metric_is_not_silently_ignored() -> None:
    summary = _summary()
    summary["metrics"]["corrupt"] = "not-an-object"
    verdict = evaluate_k6_summary(summary, _thresholds())
    assert verdict.status == "harness_failure"
    assert "each metric must be an object" in verdict.reason


def test_unused_zero_sample_metric_does_not_gate_required_thresholds() -> None:
    summary = _summary()
    summary["metrics"]["data_received"] = {
        "type": "counter", "values": {"count": 0, "rate": 0},
    }
    assert evaluate_k6_summary(summary, _thresholds()).status == "pass"
