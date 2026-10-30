"""Read-only budget headroom and replay-deduplicated early observations."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import load_workflow_operation


def budget_headroom(
    limits: Mapping[str, Any], measurement: Mapping[str, Any],
) -> dict[str, Any]:
    """Report measured headroom, not an estimate of remaining model turns."""
    dimensions = {}
    for name, limit_key, used_key in (
        ("tokens", "token_budget", "total_tokens"),
        ("usd", "cost_budget_usd", "total_usd"),
        ("usage_samples", "max_usage_samples", "usage_samples"),
        ("wall_clock", "timeout_seconds", "elapsed_seconds"),
    ):
        limit = limits.get(limit_key, 0)
        if not limit:
            continue
        known = name == "wall_clock" or bool(measurement.get("meter_available"))
        if name == "usd" and (
            measurement.get("unpriced_entries") or measurement.get("partial_entries")
        ):
            known = False
        used = measurement.get(used_key, 0)
        dimensions[name] = {
            "limit": limit, "used": used if known else None,
            "remaining": max(0, limit - used) if known else None,
            "ratio": used / limit if known else None,
        }
    highest = max((row["ratio"] or 0 for row in dimensions.values()), default=0)
    level = 100 if highest >= 1 else 80 if highest >= .8 else 50 if highest >= .5 else 0
    return {
        "dimensions": dimensions,
        "warning_level": level,
        "pricing_status": (
            "unpriced" if measurement.get("unpriced_entries")
            else "partial" if measurement.get("partial_entries") else "priced"
        ) if measurement.get("meter_available") else "unavailable",
    }


def emit_budget_warning(
    runtime: Any, *, events: list[ZfEvent], scope: str, scope_id: str,
    workflow_run_id: str, task_id: str, limits: Mapping[str, Any],
    measurement: Mapping[str, Any], causation_id: str,
) -> ZfEvent | None:
    status = budget_headroom(limits, measurement)
    level = status["warning_level"]
    if level not in {50, 80}:
        return None
    for event in events:
        payload = event.payload or {}
        if (
            event.type == "workflow.budget.warning"
            and payload.get("scope") == scope
            and payload.get("scope_id") == scope_id
            and event.causation_id == causation_id
            and int(payload.get("warning_level") or 0) >= level
        ):
            return None
    return runtime.event_writer.append(ZfEvent(
        type="workflow.budget.warning", actor="kernel", task_id=task_id or None,
        causation_id=causation_id or None, correlation_id=workflow_run_id or None,
        payload={
            "schema_version": "workflow-budget-warning.v1",
            "workflow_run_id": workflow_run_id, "scope": scope, "scope_id": scope_id,
            "limits": dict(limits), "measurement": dict(measurement), **status,
        },
    ))


def operation_budget_status(
    state_dir: Path, config: Any, operation_id: str,
) -> dict[str, Any]:
    from zf.runtime.workflow_budget_guard import (
        _exceeded_dimensions, _measurement, _operation_limits, _timestamp_epoch,
        usage_meter_snapshot,
    )

    log = EventLog(state_dir / "events.jsonl")
    operation = load_workflow_operation(log, operation_id)
    if operation is None:
        raise ValueError("unknown workflow operation")
    if operation.get("status") != "running":
        # A role may already be serving another task. Its current usage cannot
        # be attributed to a settled operation by subtracting an old baseline.
        raise ValueError("budget status requires a running workflow operation")
    runtime = SimpleNamespace(
        state_dir=state_dir, cost_tracker=CostTracker(state_dir / "cost.jsonl"),
    )
    limits = _operation_limits(state_dir, operation)
    measurement = _measurement(
        baseline=operation.get("budget_snapshot"),
        current=usage_meter_snapshot(
            runtime, instance_id=str(operation.get("role_instance") or ""),
        ),
        elapsed_seconds=max(
            0, time.time() - _timestamp_epoch(str(operation.get("started_at") or "")),
        ),
        cost_fail_closed=bool(getattr(config, "budget_fail_closed", False)),
    )
    return {
        "operation_id": operation_id, "workflow_run_id": operation.get("workflow_run_id"),
        "role_instance": operation.get("role_instance"),
        "limits": limits,
        "measurement": {
            key: value for key, value in measurement.items()
            if key not in {"baseline", "current"}
        },
        **budget_headroom(limits, measurement),
        "exceeded": _exceeded_dimensions(limits, measurement),
        "guidance": (
            "Preserve a continuation note with current identity, source digests, "
            "HEAD, open items and evidence refs. Retain full sources; do not "
            "reset budgets or skip gates."
        ),
    }
