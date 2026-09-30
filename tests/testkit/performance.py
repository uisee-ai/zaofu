"""Fail-closed adapter for k6 end-of-test summary evidence.

The TestKit does not invent performance budgets.  A product or release tier
declares thresholds, k6 produces a machine-readable summary, and this module
only checks that the summary has usable samples and that every declared
threshold is satisfied.  A missing/malformed summary or an unexplained
non-zero k6 exit is a harness failure, never a performance pass.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from operator import eq, ge, gt, le, lt
from pathlib import Path
from typing import Any, Callable, Mapping


class PerformanceSummaryError(ValueError):
    """The k6 summary cannot support a trustworthy verdict."""


_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "<": lt,
    "<=": le,
    ">": gt,
    ">=": ge,
    "==": eq,
}


@dataclass(frozen=True)
class PerformanceThreshold:
    """One explicit metric/statistic budget declared by the test owner."""

    metric: str
    statistic: str
    operator: str
    limit: float
    expression: str = ""

    def __post_init__(self) -> None:
        if not self.metric or not self.statistic:
            raise PerformanceSummaryError("metric and statistic are required")
        if self.operator not in _OPERATORS:
            raise PerformanceSummaryError(f"unsupported threshold operator: {self.operator}")
        if isinstance(self.limit, bool) or not isinstance(self.limit, (int, float)):
            raise PerformanceSummaryError("threshold limit must be numeric")
        if not math.isfinite(float(self.limit)):
            raise PerformanceSummaryError("threshold limit must be finite")


@dataclass(frozen=True)
class PerformanceVerdict:
    """Structured result suitable for a TestKit evidence sidecar."""

    status: str
    checked: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    reason: str = ""


def _number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PerformanceSummaryError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise PerformanceSummaryError(f"{label} must be finite")
    return result


def _format_limit(value: float | int) -> str:
    """Match k6's threshold key without turning large integers into exponents."""

    if isinstance(value, int):
        return str(value)
    return format(value, "g")


def _metric_payloads(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Normalize legacy map and k6 v1 machine-readable metric arrays."""

    raw_metrics: Any = payload.get("metrics")
    if isinstance(raw_metrics, Mapping):
        if not raw_metrics:
            raise PerformanceSummaryError("summary.metrics must be non-empty")
        normalized: dict[str, Mapping[str, Any]] = {}
        for name, metric in raw_metrics.items():
            if not isinstance(name, str) or not isinstance(metric, Mapping):
                raise PerformanceSummaryError("each metric must be an object")
            if name in normalized:
                raise PerformanceSummaryError(f"duplicate metric: {name}")
            if "values" in metric:
                normalized[name] = metric
                continue
            # k6 <=1.5 ``--summary-export`` emits metric statistics directly
            # and encodes threshold values as ``true = breached``.  Normalize
            # that legacy shape while retaining the source semantics.
            values = {
                str(key): value
                for key, value in metric.items()
                if key != "thresholds" and isinstance(value, (int, float)) and not isinstance(value, bool)
            }
            thresholds = metric.get("thresholds")
            # Legacy k6 summaries expose Rate/Checks as ``passes`` and
            # ``fails`` (and sometimes only a scalar ``value``), whereas the
            # machine-readable format names the aggregate ``rate``.  Derive
            # the explicit rate from observed samples so a successful real
            # run is not mistaken for missing evidence.  A zero denominator
            # is intentionally left without a rate and is rejected by the
            # sample-count check below.
            if "rate" not in values and "passes" in values and "fails" in values:
                total = float(values["passes"]) + float(values["fails"])
                if total > 0:
                    values["rate"] = float(values["passes"]) / total
            if values:
                values.setdefault("count", 1)
            normalized[name] = {
                "values": values,
                "thresholds": thresholds,
                "_legacy_summary_export": True,
            }
        return normalized
    results = payload.get("results")
    if isinstance(results, Mapping) and isinstance(results.get("metrics"), list):
        if not isinstance(payload.get("version"), str):
            raise PerformanceSummaryError("machine-readable summary needs a version")
        if not isinstance(payload.get("metadata"), Mapping):
            raise PerformanceSummaryError("machine-readable summary needs metadata")
        if not isinstance(payload.get("config"), Mapping):
            raise PerformanceSummaryError("machine-readable summary needs config")
        normalized: dict[str, Mapping[str, Any]] = {}
        for metric in results["metrics"]:
            if not isinstance(metric, Mapping) or not isinstance(metric.get("name"), str):
                raise PerformanceSummaryError("machine-readable metric needs a name")
            for field in ("type", "contains", "values"):
                if field not in metric:
                    raise PerformanceSummaryError(f"machine-readable metric needs {field}")
            if metric["name"] in normalized:
                raise PerformanceSummaryError(f"duplicate metric: {metric['name']}")
            normalized[metric["name"]] = metric
        return normalized
    raise PerformanceSummaryError("summary.metrics must be a non-empty object or array")


def parse_k6_summary(payload: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Extract finite metric values from a k6 ``handleSummary`` object.

    k6 summaries place aggregate values under ``metrics.<name>.values``.  A
    metric with no numeric values is not evidence; silently treating it as
    zero would allow an empty run to pass a budget.
    """

    metrics = _metric_payloads(payload)
    if not metrics:
        raise PerformanceSummaryError("summary.metrics must be non-empty")
    parsed: dict[str, dict[str, float]] = {}
    for metric_name, metric in metrics.items():
        if not isinstance(metric_name, str) or not isinstance(metric, Mapping):
            raise PerformanceSummaryError("each metric must be an object")
        values = metric.get("values")
        # k6 emits optional built-in metrics even when a short local script
        # never exercises their protocol (for example data_received without
        # an HTTP request).  They are not evidence for a mandatory threshold;
        # retain only populated metrics and validate required ones below.
        if not isinstance(values, Mapping) or not values:
            continue
        parsed[metric_name] = {
            str(statistic): _number(value, label=f"{metric_name}.{statistic}")
            for statistic, value in values.items()
        }
    if not parsed:
        raise PerformanceSummaryError("summary has no populated metrics")
    return parsed


def _sample_count(metric_name: str, values: Mapping[str, float]) -> float:
    """Return a conservative sample count for common k6 metric kinds."""

    if "count" in values:
        count = values["count"]
    elif "total" in values:
        count = values["total"]
    elif "passes" in values or "fails" in values:
        count = values.get("passes", 0.0) + values.get("fails", 0.0)
    else:
        raise PerformanceSummaryError(f"metric {metric_name!r} has no sample count")
    if count <= 0:
        raise PerformanceSummaryError(f"metric {metric_name!r} has no samples")
    return count


def load_k6_summary(path: Path) -> dict[str, dict[str, float]]:
    """Read and parse a k6 summary export without accepting partial JSON."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PerformanceSummaryError(f"cannot read k6 summary: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise PerformanceSummaryError("k6 summary root must be an object")
    return parse_k6_summary(payload)


def evaluate_k6_summary(
    payload: Mapping[str, Any],
    thresholds: tuple[PerformanceThreshold, ...],
    *,
    exit_code: int | None = 0,
    timed_out: bool = False,
) -> PerformanceVerdict:
    """Evaluate explicit budgets and classify k6 execution failures.

    A non-zero exit with a failed declared threshold is ``fail``.  A non-zero
    exit while all thresholds pass has no trustworthy cause at this boundary
    (for example script/runtime failure), so it is ``harness_failure``.
    """

    if timed_out or exit_code is None:
        return PerformanceVerdict("harness_failure", reason="k6 execution timed out")
    if not thresholds:
        return PerformanceVerdict("harness_failure", reason="no performance thresholds declared")
    try:
        metrics = parse_k6_summary(payload)
    except PerformanceSummaryError as exc:
        return PerformanceVerdict("harness_failure", reason=str(exc))

    checked: list[str] = []
    violations: list[str] = []
    for threshold in thresholds:
        key = f"{threshold.metric}.{threshold.statistic}{threshold.operator}{_format_limit(threshold.limit)}"
        values = metrics.get(threshold.metric)
        metric_payload = _metric_payloads(payload).get(threshold.metric)
        if values is None or threshold.statistic not in values or not isinstance(metric_payload, Mapping):
            return PerformanceVerdict(
                "harness_failure",
                checked=tuple(checked),
                reason=f"missing metric/statistic: {threshold.metric}.{threshold.statistic}",
            )
        try:
            _sample_count(threshold.metric, values)
        except PerformanceSummaryError as exc:
            return PerformanceVerdict("harness_failure", tuple(checked), reason=str(exc))
        summary_thresholds = metric_payload.get("thresholds")
        if not isinstance(summary_thresholds, Mapping):
            return PerformanceVerdict(
                "harness_failure",
                checked=tuple(checked),
                reason=f"missing threshold result: {threshold.metric}",
            )
        expression = threshold.expression or f"{threshold.statistic}{threshold.operator}{_format_limit(threshold.limit)}"
        threshold_result = summary_thresholds.get(expression)
        if isinstance(threshold_result, Mapping) and isinstance(threshold_result.get("ok"), bool):
            reported_pass = bool(threshold_result["ok"])
        elif isinstance(threshold_result, bool) and metric_payload.get("_legacy_summary_export"):
            # Legacy k6 summary booleans answer "was the threshold crossed?".
            reported_pass = not threshold_result
        else:
            return PerformanceVerdict(
                "harness_failure",
                checked=tuple(checked),
                reason=f"missing threshold result: {threshold.metric}.{expression}",
            )
        actual = values[threshold.statistic]
        checked.append(key)
        computed_pass = _OPERATORS[threshold.operator](actual, float(threshold.limit))
        if computed_pass != reported_pass:
            return PerformanceVerdict(
                "harness_failure",
                checked=tuple(checked),
                reason=f"result inconsistent: {threshold.metric}.{expression}",
            )
        if not reported_pass:
            violations.append(f"{key} (actual={actual:g})")

    if violations:
        if exit_code != 99:
            return PerformanceVerdict(
                "harness_failure",
                tuple(checked),
                tuple(violations),
                reason=f"threshold failure with unexpected k6 exit code {exit_code}",
            )
        return PerformanceVerdict("fail", tuple(checked), tuple(violations))
    if exit_code == 99:
        return PerformanceVerdict(
            "harness_failure",
            tuple(checked),
            reason="k6 exit 99 but all thresholds passed",
        )
    if exit_code != 0:
        return PerformanceVerdict(
            "harness_failure",
            tuple(checked),
            reason=f"k6 exited with unexplained code {exit_code}",
        )
    return PerformanceVerdict("pass", tuple(checked))


__all__ = [
    "PerformanceSummaryError",
    "PerformanceThreshold",
    "PerformanceVerdict",
    "evaluate_k6_summary",
    "load_k6_summary",
    "parse_k6_summary",
]
