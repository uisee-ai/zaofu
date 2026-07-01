"""ZF-EVAL-METRIC-DIAGNOSTICS-001 — diagnose MetricsSnapshot fields.

Adds 4-layer evaluation (health-band + trend + root-cause hints +
recommendations) on top of the existing :class:`MetricsSnapshot` /
:func:`MetricsSnapshot.flag_alerts` thresholds.

Coverage goal: take the 13 currently-undiagnosed metric fields and
turn each into a :class:`MetricDiagnostic` so ``zf kanban health``
and ``zf metrics diagnose`` can surface actionable information.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from zf.core.metrics.collector import MetricsSnapshot


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricDiagnostic:
    """One evaluation of a single metric. ``health_band`` is the
    headline judgement; ``trend`` indicates direction vs historical
    snapshots; ``root_cause_hints`` and ``recommendations`` give the
    operator actionable next steps."""

    metric_name: str
    value: float | int | str
    health_band: str  # "healthy" | "warning" | "critical" | "unknown"
    trend: str = "n/a"  # "rising" | "falling" | "stable" | "n/a"
    root_cause_hints: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    reason: str = ""

    @property
    def is_actionable(self) -> bool:
        return self.health_band in ("warning", "critical")


def _compose_reason(
    field_name: str,
    value: float | int | str,
    band: str,
    trend: str,
    hints: Iterable[str],
) -> str:
    """Synthesize a one-line human reason from the diagnostic signals.

    Fills the previously-empty ``MetricDiagnostic.reason`` so each metric
    is self-explaining (value + health band + trend + leading root cause),
    mirroring an Opik ``ScoreResult(value, name, reason)``.
    """
    if isinstance(value, float):
        shown = f"{value:.3g}"
    else:
        shown = str(value)
    parts = [f"{field_name}={shown}: {band}"]
    if trend and trend not in ("n/a", "stable"):
        parts.append(trend)
    head = ", ".join(parts)
    first_hint = next((h for h in hints if h), "")
    return f"{head} — {first_hint}" if first_hint else head


# ---------------------------------------------------------------------------
# Health bands — defaults; can be overridden via zf.yaml metrics.health_bands
# ---------------------------------------------------------------------------


DEFAULT_HEALTH_BANDS: dict[str, dict[str, tuple[float, float]]] = {
    # Range form: (low_inclusive, high_exclusive) for each band.
    # +inf == math.inf, -inf == -math.inf.
    "mtts": {
        "healthy": (100.0, math.inf),
        "warning": (20.0, 100.0),
        "critical": (-math.inf, 20.0),
    },
    "stuck_recovery_rate": {
        "healthy": (0.9, math.inf),
        "warning": (0.7, 0.9),
        "critical": (-math.inf, 0.7),
    },
    "crash_free_hours": {
        "healthy": (24.0, math.inf),
        "warning": (6.0, 24.0),
        "critical": (-math.inf, 6.0),
    },
    "resume_fidelity": {
        "healthy": (1.0, math.inf),
        "warning": (0.9, 1.0),
        "critical": (-math.inf, 0.9),
    },
    "vcr": {
        "healthy": (0.5, math.inf),
        "warning": (0.3, 0.5),
        "critical": (-math.inf, 0.3),
    },
    "scope_violation_rate": {
        "healthy": (-math.inf, 0.05),
        "warning": (0.05, 0.15),
        "critical": (0.15, math.inf),
    },
    "discriminator_catch_rate": {
        # Healthy band is mid-range. Both extremes are warning signals.
        "healthy": (0.1, 0.3),
        "warning_low": (-math.inf, 0.1),
        "warning_high": (0.3, math.inf),
    },
    "goal_drift": {
        "healthy": (-math.inf, 0.05),
        "warning": (0.05, 0.15),
        "critical": (0.15, math.inf),
    },
    "throughput_per_hour": {
        # Healthy depends on baseline. Use defensive fixed bands.
        "healthy": (0.1, math.inf),
        "warning": (0.01, 0.1),
        "critical": (-math.inf, 0.01),
    },
    "rework_ratio": {
        "healthy": (-math.inf, 0.2),
        "warning": (0.2, 0.5),
        "critical": (0.5, math.inf),
    },
    "causal_depth_mean": {
        "healthy": (3.0, 15.0),
        "warning_low": (-math.inf, 3.0),
        "warning_high": (15.0, 30.0),
        "critical": (30.0, math.inf),
    },
    "memory_hit_rate": {
        # Only meaningful when memory is in use; otherwise "unknown".
        "healthy": (0.3, math.inf),
        "warning": (0.1, 0.3),
        "critical": (-math.inf, 0.1),
    },
    "trace_complete_rate": {
        "healthy": (0.9, math.inf),
        "warning": (0.6, 0.9),
        "critical": (-math.inf, 0.6),
    },
    "avg_task_duration_minutes": {
        # Operator can tune; defaults are conservative.
        "healthy": (-math.inf, 120.0),
        "warning": (120.0, 360.0),
        "critical": (360.0, math.inf),
    },
    "avg_events_per_task": {
        "healthy": (-math.inf, 50.0),
        "warning": (50.0, 100.0),
        "critical": (100.0, math.inf),
    },
    "cost_per_task": {
        "healthy": (-math.inf, 1.0),
        "warning": (1.0, 5.0),
        "critical": (5.0, math.inf),
    },
    "token_per_task": {
        "healthy": (-math.inf, 50_000.0),
        "warning": (50_000.0, 200_000.0),
        "critical": (200_000.0, math.inf),
    },
    "recycle_freq_per_hour": {
        "healthy": (-math.inf, 0.5),
        "warning": (0.5, 2.0),
        "critical": (2.0, math.inf),
    },
    "budget_breach_rate": {
        "healthy": (-math.inf, 0.05),
        "warning": (0.05, 0.15),
        "critical": (0.15, math.inf),
    },
}


# ---------------------------------------------------------------------------
# Trend detection
# ---------------------------------------------------------------------------


def detect_trend(
    current: float,
    history: Iterable[float],
    *,
    threshold_pct: float = 0.10,
) -> str:
    """Compare ``current`` against the mean of ``history`` (older snapshots).

    Returns:
        - ``"rising"`` if current > mean * (1 + threshold_pct)
        - ``"falling"`` if current < mean * (1 - threshold_pct)
        - ``"stable"`` if within the band
        - ``"n/a"`` if not enough history
    """
    values = [v for v in history if isinstance(v, (int, float)) and not math.isnan(float(v))]
    if not values:
        return "n/a"
    baseline = sum(values) / len(values)
    if baseline == 0:
        # Degenerate — anything non-zero is rising, zero is stable.
        if current > 0:
            return "rising"
        if current < 0:
            return "falling"
        return "stable"
    delta = (current - baseline) / abs(baseline)
    if delta > threshold_pct:
        return "rising"
    if delta < -threshold_pct:
        return "falling"
    return "stable"


# ---------------------------------------------------------------------------
# Health-band classification
# ---------------------------------------------------------------------------


def classify_band(
    value: float,
    bands: dict[str, tuple[float, float]],
) -> str:
    """Classify ``value`` against a band map. Returns the band name or
    ``"unknown"`` if no band matches.

    Bands with a ``_low`` / ``_high`` suffix collapse back into the
    base band name (e.g. ``warning_low`` → ``warning``).
    """
    for name, (low, high) in bands.items():
        if low <= value < high:
            if "_" in name:
                return name.split("_", 1)[0]
            return name
    return "unknown"


# ---------------------------------------------------------------------------
# Per-metric evaluator
# ---------------------------------------------------------------------------


class MetricsEvaluator:
    """Stateless. Given current ``MetricsSnapshot`` + optional history
    list of older snapshots, produce a diagnostic per field.

    History semantics: ``history`` is the *older* snapshots (the
    caller filters out the current one). Trend = current vs mean of
    history.
    """

    def __init__(
        self,
        *,
        bands: dict[str, dict[str, tuple[float, float]]] | None = None,
    ) -> None:
        self.bands = bands or DEFAULT_HEALTH_BANDS

    def evaluate_snapshot(
        self,
        current: MetricsSnapshot,
        history: list[MetricsSnapshot] | None = None,
    ) -> list[MetricDiagnostic]:
        """Return diagnostics for every band-able field. Excludes meta
        fields (window_hours / events_considered / tasks_done /
        alerts) which are reference dimensions, not health metrics."""
        history = history or []
        out: list[MetricDiagnostic] = []
        for field_name in self.bands.keys():
            if not hasattr(current, field_name):
                continue
            current_value = getattr(current, field_name)
            if not isinstance(current_value, (int, float)):
                continue
            historical_values = [
                getattr(s, field_name) for s in history
                if hasattr(s, field_name)
                and isinstance(getattr(s, field_name), (int, float))
            ]
            out.append(self._evaluate_field(
                field_name=field_name,
                current_value=float(current_value),
                history_values=historical_values,
            ))
        return out

    def _evaluate_field(
        self,
        *,
        field_name: str,
        current_value: float,
        history_values: list[float],
    ) -> MetricDiagnostic:
        bands = self.bands.get(field_name, {})
        band = classify_band(current_value, bands) if bands else "unknown"
        trend = detect_trend(current_value, history_values)
        hints, recs = self._diagnose(field_name, current_value, band, trend)
        return MetricDiagnostic(
            metric_name=field_name,
            value=current_value,
            health_band=band,
            trend=trend,
            root_cause_hints=tuple(hints),
            recommendations=tuple(recs),
            reason=_compose_reason(field_name, current_value, band, trend, hints),
        )

    # ------------------------------------------------------------------
    # Per-metric root_cause_hints + recommendations.
    # Keep the diagnostic logic explicit (not generic) so each metric
    # gets a meaningful actionable message.
    # ------------------------------------------------------------------

    def _diagnose(
        self,
        name: str,
        value: float,
        band: str,
        trend: str,
    ) -> tuple[list[str], list[str]]:
        if band == "healthy":
            return [], []
        # Critical bands get stronger language.
        critical = band == "critical"
        hints: list[str] = []
        recs: list[str] = []

        if name == "mtts":
            hints.append(
                "Workers hitting stuck state too frequently"
                + (" — possible config issue" if critical else "")
            )
            recs.append("Review worker.heartbeat patterns and stuck_threshold_seconds")
        elif name == "stuck_recovery_rate":
            hints.append("Stuck workers not recovering on first try")
            recs.append("Check respawn / recycle path; consider raising orphan_warning_seconds")
        elif name == "crash_free_hours":
            hints.append("Recent pane / orphan crash detected")
            recs.append("Inspect events.jsonl for pane.crash / task.orphaned causes")
        elif name == "resume_fidelity":
            hints.append("Tasks orphaned during resume")
            recs.append("Verify role_sessions.yaml consistency + session_id stability")
        elif name == "vcr":
            hints.append("Verification coverage below target")
            recs.append("Add verification_tiers / acceptance criteria to under-covered tasks")
        elif name == "scope_violation_rate":
            hints.append("Workers writing outside declared scope")
            recs.append("Tighten task.contract.scope + review protected_paths config")
        elif name == "discriminator_catch_rate":
            if value < 0.1:
                hints.append("Discriminator rarely catching — possible false-negative leak")
                recs.append("Review discriminator prompt / threshold; sample passed cases")
            else:
                hints.append("Discriminator catching too often — possible over-tight gate")
                recs.append("Review fix_items content; consider relaxing or splitting tiers")
        elif name == "goal_drift":
            hints.append("Worker thrashing / repeat decisions detected")
            recs.append("Investigate drift events; check task contract clarity + spec_ref")
        elif name == "throughput_per_hour":
            hints.append("Task completion rate is low")
            recs.append("Identify slow stage; consider scaling reviewer / test replicas")
        elif name == "rework_ratio":
            hints.append("Rework count high relative to completed tasks")
            recs.append("Review failure taxonomy (see EVAL-FAILURE-TAXONOMY-001); refine prompts")
        elif name == "causal_depth_mean":
            if value < 3:
                hints.append("Causal chains too shallow — events may lack causation_id")
                recs.append("Audit emit sites for causation_id wiring")
            else:
                hints.append("Causal chains very deep — possible rework loops")
                recs.append("Check task.rework.requested chains in events.jsonl")
        elif name == "memory_hit_rate":
            hints.append("Memory rarely hit — workers may not be writing memory.note")
            recs.append("Audit memory.note emit; review max_days decay policy")
        elif name == "trace_complete_rate":
            hints.append("Some tasks missing required workflow events")
            recs.append("Run `zf workflow audit` to identify missing event types")
        elif name == "avg_task_duration_minutes":
            hints.append("Tasks taking longer than typical")
            recs.append("Look at per-task event_log gaps; identify long stages")
        elif name == "avg_events_per_task":
            hints.append("Event count per task elevated (event bloat / rework)")
            recs.append("Inspect rework + Hook noise; consider event-emit policy review")
        elif name == "cost_per_task":
            hints.append("Cost per task elevated")
            recs.append("Review cost_by_backend; consider cheaper backend for non-critical roles")
        elif name == "token_per_task":
            hints.append("Token consumption per task elevated")
            recs.append("Review briefing length; consider context-pull on demand")
        elif name == "recycle_freq_per_hour":
            hints.append("Context recycling frequently")
            recs.append(
                "Check context thresholds; consider larger context_window_tokens"
            )
        elif name == "budget_breach_rate":
            hints.append("Tasks exceeding cost budget")
            recs.append("Raise budget_usd or tighten task scope")

        if trend == "rising" and band in ("warning", "critical"):
            hints.append(f"Metric is rising — {band} band reached recently")
        elif trend == "falling" and band == "healthy":
            hints.append("Metric improving (falling); trend healthy")
        return hints, recs


# ---------------------------------------------------------------------------
# Convenience renderer for `zf metrics diagnose` CLI output
# ---------------------------------------------------------------------------


def render_diagnostic_markdown(diagnostics: Iterable[MetricDiagnostic]) -> str:
    """Return a markdown summary of the diagnostics, grouped by
    health band (critical / warning / healthy / unknown)."""
    groups: dict[str, list[MetricDiagnostic]] = {
        "critical": [], "warning": [], "healthy": [], "unknown": [],
    }
    for d in diagnostics:
        groups.setdefault(d.health_band, []).append(d)
    lines: list[str] = ["# Metric Diagnostics", ""]
    for band in ("critical", "warning", "healthy", "unknown"):
        items = groups.get(band) or []
        if not items:
            continue
        lines.append(f"## {band.capitalize()} ({len(items)})")
        lines.append("")
        for d in items:
            line = f"- **{d.metric_name}** = {d.value!r}"
            if d.trend != "n/a":
                line += f"  ({d.trend})"
            lines.append(line)
            for hint in d.root_cause_hints:
                lines.append(f"  - hint: {hint}")
            for rec in d.recommendations:
                lines.append(f"  - rec: {rec}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
