"""EVAL-METRIC-DIAGNOSTICS-001 tests."""

from __future__ import annotations

import math

import pytest

from zf.core.metrics.collector import MetricsSnapshot
from zf.core.metrics.evaluator import (
    DEFAULT_HEALTH_BANDS,
    MetricDiagnostic,
    MetricsEvaluator,
    classify_band,
    detect_trend,
    render_diagnostic_markdown,
)


# ---------------------------------------------------------------------------
# MetricDiagnostic frozen + fields
# ---------------------------------------------------------------------------


def test_diagnostic_is_frozen() -> None:
    d = MetricDiagnostic(metric_name="mtts", value=42.0, health_band="warning")
    with pytest.raises((AttributeError, TypeError)):
        d.value = 99.0  # type: ignore[misc]


def test_diagnostic_is_actionable_only_when_warning_or_critical() -> None:
    healthy = MetricDiagnostic(metric_name="x", value=0, health_band="healthy")
    warn = MetricDiagnostic(metric_name="x", value=0, health_band="warning")
    crit = MetricDiagnostic(metric_name="x", value=0, health_band="critical")
    unknown = MetricDiagnostic(metric_name="x", value=0, health_band="unknown")
    assert healthy.is_actionable is False
    assert warn.is_actionable is True
    assert crit.is_actionable is True
    assert unknown.is_actionable is False


# ---------------------------------------------------------------------------
# classify_band — boundary tests
# ---------------------------------------------------------------------------


def test_classify_band_mtts_healthy_boundary() -> None:
    band = classify_band(100.0, DEFAULT_HEALTH_BANDS["mtts"])
    assert band == "healthy"


def test_classify_band_mtts_warning() -> None:
    assert classify_band(50.0, DEFAULT_HEALTH_BANDS["mtts"]) == "warning"


def test_classify_band_mtts_critical() -> None:
    assert classify_band(10.0, DEFAULT_HEALTH_BANDS["mtts"]) == "critical"


def test_classify_band_vcr_healthy_at_boundary() -> None:
    assert classify_band(0.5, DEFAULT_HEALTH_BANDS["vcr"]) == "healthy"


def test_classify_band_scope_violation_lower_is_better() -> None:
    """scope_violation_rate is "lower is better"."""
    assert classify_band(0.01, DEFAULT_HEALTH_BANDS["scope_violation_rate"]) == "healthy"
    assert classify_band(0.1, DEFAULT_HEALTH_BANDS["scope_violation_rate"]) == "warning"
    assert classify_band(0.2, DEFAULT_HEALTH_BANDS["scope_violation_rate"]) == "critical"


def test_classify_band_discriminator_dual_warning() -> None:
    """Discriminator catch rate has both low and high warning bands."""
    assert classify_band(0.05, DEFAULT_HEALTH_BANDS["discriminator_catch_rate"]) == "warning"
    assert classify_band(0.2, DEFAULT_HEALTH_BANDS["discriminator_catch_rate"]) == "healthy"
    assert classify_band(0.5, DEFAULT_HEALTH_BANDS["discriminator_catch_rate"]) == "warning"


def test_classify_band_unknown_value_outside_all_ranges() -> None:
    """Sparse band map → returns unknown."""
    sparse = {"healthy": (10.0, 20.0)}
    assert classify_band(5.0, sparse) == "unknown"
    assert classify_band(25.0, sparse) == "unknown"


# ---------------------------------------------------------------------------
# detect_trend
# ---------------------------------------------------------------------------


def test_detect_trend_no_history_is_na() -> None:
    assert detect_trend(5.0, []) == "n/a"


def test_detect_trend_rising() -> None:
    assert detect_trend(110.0, [100.0, 95.0, 98.0]) == "rising"


def test_detect_trend_falling() -> None:
    assert detect_trend(80.0, [100.0, 95.0, 98.0]) == "falling"


def test_detect_trend_stable_within_threshold() -> None:
    """Default threshold is 10% — small deltas are stable."""
    assert detect_trend(101.0, [100.0, 100.0, 100.0]) == "stable"
    assert detect_trend(95.0, [100.0, 100.0, 100.0]) == "stable"


def test_detect_trend_zero_baseline_handled() -> None:
    """Baseline of zero — anything positive rises, negative falls."""
    assert detect_trend(1.0, [0.0, 0.0]) == "rising"
    assert detect_trend(-1.0, [0.0, 0.0]) == "falling"
    assert detect_trend(0.0, [0.0, 0.0]) == "stable"


def test_detect_trend_ignores_non_numeric() -> None:
    """Non-numeric history values are skipped silently."""
    assert detect_trend(5.0, [4.0, None, "bad", 4.0]) == "rising"


# ---------------------------------------------------------------------------
# MetricsEvaluator.evaluate_snapshot
# ---------------------------------------------------------------------------


def test_evaluate_snapshot_returns_19_diagnostics() -> None:
    """All 19 band-able fields get a diagnostic."""
    snap = MetricsSnapshot()
    diags = MetricsEvaluator().evaluate_snapshot(snap)
    # DEFAULT_HEALTH_BANDS has 19 entries (excludes meta fields)
    assert len(diags) == 19


def test_evaluate_snapshot_excludes_meta_fields() -> None:
    """Meta fields (window_hours / events_considered / tasks_done /
    alerts) are not band-able — should not appear in diagnostics."""
    diags = MetricsEvaluator().evaluate_snapshot(MetricsSnapshot())
    names = {d.metric_name for d in diags}
    assert "window_hours" not in names
    assert "events_considered" not in names
    assert "tasks_done" not in names
    assert "alerts" not in names


def test_evaluate_warning_includes_hints_and_recs() -> None:
    """Warning band → diagnostic has at least one hint and rec."""
    snap = MetricsSnapshot(mtts=50.0)
    diags = MetricsEvaluator().evaluate_snapshot(snap)
    mtts_d = next(d for d in diags if d.metric_name == "mtts")
    assert mtts_d.health_band == "warning"
    assert len(mtts_d.root_cause_hints) >= 1
    assert len(mtts_d.recommendations) >= 1


def test_evaluate_healthy_has_no_hints() -> None:
    """Healthy band → no hints / recs."""
    snap = MetricsSnapshot(mtts=200.0)  # well-healthy
    diags = MetricsEvaluator().evaluate_snapshot(snap)
    mtts_d = next(d for d in diags if d.metric_name == "mtts")
    assert mtts_d.health_band == "healthy"
    assert mtts_d.root_cause_hints == ()
    assert mtts_d.recommendations == ()


def test_evaluate_trend_detected_with_history() -> None:
    """Two snapshots → trend detected against baseline."""
    history = [
        MetricsSnapshot(throughput_per_hour=1.0),
        MetricsSnapshot(throughput_per_hour=1.1),
    ]
    current = MetricsSnapshot(throughput_per_hour=0.5)
    diags = MetricsEvaluator().evaluate_snapshot(current, history=history)
    tp = next(d for d in diags if d.metric_name == "throughput_per_hour")
    assert tp.trend == "falling"


def test_evaluate_custom_bands_override() -> None:
    """Caller-supplied band overrides default."""
    custom = {
        "mtts": {
            "healthy": (10.0, math.inf),
            "warning": (-math.inf, 10.0),
        }
    }
    snap = MetricsSnapshot(mtts=20.0)
    diags = MetricsEvaluator(bands=custom).evaluate_snapshot(snap)
    assert len(diags) == 1
    assert diags[0].metric_name == "mtts"
    assert diags[0].health_band == "healthy"


# ---------------------------------------------------------------------------
# Discriminator dual-band: low → diagnostic suggests under-catching
# ---------------------------------------------------------------------------


def test_discriminator_low_warning_diagnostic_mentions_false_negative() -> None:
    snap = MetricsSnapshot(discriminator_catch_rate=0.05)
    diags = MetricsEvaluator().evaluate_snapshot(snap)
    d = next(d for d in diags if d.metric_name == "discriminator_catch_rate")
    assert d.health_band == "warning"
    assert any("false-negative" in h.lower() for h in d.root_cause_hints)


def test_discriminator_high_warning_diagnostic_mentions_over_tight() -> None:
    snap = MetricsSnapshot(discriminator_catch_rate=0.6)
    diags = MetricsEvaluator().evaluate_snapshot(snap)
    d = next(d for d in diags if d.metric_name == "discriminator_catch_rate")
    assert d.health_band == "warning"
    assert any("over-tight" in h.lower() or "too often" in h.lower()
               for h in d.root_cause_hints)


# ---------------------------------------------------------------------------
# render_diagnostic_markdown
# ---------------------------------------------------------------------------


def test_render_markdown_groups_by_band() -> None:
    diags = [
        MetricDiagnostic(metric_name="a", value=1, health_band="critical"),
        MetricDiagnostic(metric_name="b", value=2, health_band="warning"),
        MetricDiagnostic(metric_name="c", value=3, health_band="healthy"),
    ]
    md = render_diagnostic_markdown(diags)
    assert "## Critical (1)" in md
    assert "## Warning (1)" in md
    assert "## Healthy (1)" in md
    # critical comes before healthy in the output
    assert md.index("Critical") < md.index("Warning") < md.index("Healthy")


def test_render_markdown_includes_hints_and_recs() -> None:
    snap = MetricsSnapshot(mtts=50.0)
    diags = MetricsEvaluator().evaluate_snapshot(snap)
    md = render_diagnostic_markdown(diags)
    assert "hint:" in md
    assert "rec:" in md


def test_render_markdown_omits_empty_groups() -> None:
    diags = [
        MetricDiagnostic(metric_name="a", value=1, health_band="healthy"),
    ]
    md = render_diagnostic_markdown(diags)
    assert "## Healthy" in md
    assert "## Critical" not in md
    assert "## Warning" not in md
