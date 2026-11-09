"""Explain admitted budget observations without changing accounting or limits."""
from typing import Any


def operation_budget_observations(events: list, *, run_id: str) -> dict[str, dict[str, Any]]:
    result = {}
    for event in events:
        p = event.payload
        if (event.type != "workflow.budget.exceeded" or event.origin != "kernel"
                or p.get("workflow_run_id") != run_id or p.get("scope") != "operation"):
            continue
        measurement = p.get("measurement") or {}
        current, baseline = measurement.get("current") or {}, measurement.get("baseline") or {}
        token_fields = ("input_tokens", "output_tokens", "cache_read_tokens",
                        "cache_creation_tokens", "cache_creation_1h_tokens")
        unpriced = measurement.get("unpriced_entries", 0)
        partial = max(0, int(current.get("partial_entries") or 0)
                      - int(baseline.get("partial_entries") or 0))
        result[str(p.get("scope_id") or "")] = {
            "source_event_id": event.id, "observed_at": event.ts,
            "limits": p.get("limits") or {},
            "exceeded_dimensions": p.get("exceeded_dimensions") or [],
            "total_tokens": measurement.get("total_tokens"),
            **{key: max(0, int(current[key]) - int(baseline[key]))
               if key in current and key in baseline else None for key in token_fields},
            "elapsed_seconds": measurement.get("elapsed_seconds"),
            "unpriced_entries": unpriced,
            "partial_entries": partial,
            "total_usd": None if unpriced or partial else measurement.get("total_usd"),
        }
    return result
