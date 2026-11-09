"""Mechanical input cost checks; never decide which requirements to omit."""
from __future__ import annotations

from typing import Any


class PlanReviewInputBudgetExceeded(ValueError):
    def __init__(self, diagnostic: dict[str, Any]):
        self.diagnostic = diagnostic
        super().__init__(
            "Plan review input exceeds its configured delivery budget; "
            "prepare a bounded, coverage-preserving package before redispatch"
        )


def check_review_input_budget(config: Any, stage_id: str, diagnostic: dict[str, Any]) -> None:
    stage = next((s for s in getattr(config.workflow, "stages", [])
                  if s.id == stage_id), None)
    aggregate = getattr(stage, "aggregate", None)
    limits = {key: int(getattr(aggregate, key, 0)) for key in
              ("review_input_max_bytes", "review_input_max_pages")}
    size = diagnostic["after"]["distinct_selected_bytes"]
    # This is a lower bound, not a promise: metadata/escaping takes page space.
    pages = diagnostic['after'].get('minimum_page_count', (size + 32767) // 32768)
    diagnostic.update(limits=limits, minimum_page_count=pages,
                      page_estimate_kind="lower_bound_excluding_metadata")
    enabled = any(limits.values())
    exceeded = (
        enabled and diagnostic["after"]["unknown_rows"] > 0
        or limits["review_input_max_bytes"] > 0 and size > limits["review_input_max_bytes"]
        or limits["review_input_max_pages"] > 0 and pages > limits["review_input_max_pages"]
    )
    diagnostic["preflight_status"] = "blocked" if exceeded else "passed" if enabled else "not_configured"
    if exceeded:
        raise PlanReviewInputBudgetExceeded(diagnostic)
