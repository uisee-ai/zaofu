"""Product-project performance budget adapter over the existing k6 oracle."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from tests.testkit.performance import PerformanceThreshold, evaluate_k6_summary


def evaluate_product_performance(summary: Mapping[str, Any], thresholds: Sequence[Mapping[str, Any]], *, exit_code: int | None = 0, timed_out: bool = False):
    """Convert manifest threshold rows and return the fail-closed k6 verdict."""

    parsed = tuple(
        PerformanceThreshold(
            metric=str(row["metric"]), statistic=str(row["statistic"]),
            operator=str(row["operator"]), limit=row["limit"],
        )
        for row in thresholds
    )
    return evaluate_k6_summary(summary, parsed, exit_code=exit_code, timed_out=timed_out)

