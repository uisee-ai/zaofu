"""Active Workflow Operation and Run budget circuit breaker."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from zf.core.cost.tracker import CostTracker
from zf.core.events.model import ZfEvent
from zf.runtime.event_window import read_runtime_events
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.run_scope import event_run_id, run_aliases
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)


BUDGET_METER_SCHEMA = "workflow-budget-meter.v1"
BUDGET_EVENT_SCHEMA = "workflow-budget-exceeded.v1"


def usage_meter_snapshot(
    runtime: Any,
    *,
    instance_id: str = "",
) -> dict[str, Any]:
    """Capture the canonical cost projection as a restart-safe baseline."""

    tracker = getattr(runtime, "cost_tracker", None)
    if tracker is None:
        tracker = CostTracker(Path(runtime.state_dir) / "cost.jsonl")
    try:
        totals = tracker.usage_totals(instance_id=instance_id)
    except Exception as exc:
        return {
            "schema_version": BUDGET_METER_SCHEMA,
            "meter_available": False,
            "instance_id": instance_id,
            "reason": f"{type(exc).__name__}: {exc}"[:500],
        }
    return {
        "schema_version": BUDGET_METER_SCHEMA,
        "meter_available": True,
        "instance_id": instance_id,
        **totals,
    }


def enforce_active_workflow_budgets(
    runtime: Any,
    *,
    now_epoch: float | None = None,
) -> list[ZfEvent]:
    """Cancel active provider work as soon as an enforced limit is reached."""

    if not getattr(runtime.config, "budget_enforcement_enabled", True):
        return []
    events = read_runtime_events(runtime.event_log, runtime.state_dir)
    operations = reduce_workflow_operations(events)
    run_projection = build_run_admission_projection(events)
    now = float(now_epoch if now_epoch is not None else runtime._now())
    emitted: list[ZfEvent] = []
    blocked_runs: set[str] = set()
    terminal_runs = {
        entry.run_id
        for entry in run_projection.runs.values()
        if entry.terminal
    }
    configured_run_limits = _limits_dict(
        getattr(getattr(runtime.config, "workflow", None), "run_limits", None)
    )
    events_by_id = {event.id: event for event in events if event.id}
    aliases = run_aliases(events)
    for entry in run_projection.runs.values():
        if not entry.active or not entry.admitted_event_id:
            continue
        admitted = events_by_id.get(entry.admitted_event_id)
        if admitted is None:
            continue
        payload = admitted.payload if isinstance(admitted.payload, dict) else {}
        run_limits, budget_revision_event_id = _effective_run_limits(
            events,
            admitted_event=admitted,
            workflow_run_id=entry.run_id,
            configured_run_limits=configured_run_limits,
            aliases=aliases,
        )
        if not _has_limits(run_limits):
            continue
        measurement = _measurement(
            baseline=payload.get("budget_snapshot"),
            current=usage_meter_snapshot(runtime),
            elapsed_seconds=max(0.0, now - _event_epoch(admitted)),
            cost_fail_closed=bool(
                getattr(runtime.config, "budget_fail_closed", False)
            ),
        )
        exceeded = _exceeded_dimensions(run_limits, measurement)
        if not exceeded:
            continue
        blocked_runs.add(entry.run_id)
        emitted.extend(_trip_run(
            runtime,
            events=events,
            operations=operations,
            workflow_run_id=entry.run_id,
            task_id=entry.task_id,
            scope="run",
            scope_id=entry.run_id,
            limits=run_limits,
            measurement=measurement,
            exceeded=exceeded,
            causation_id=budget_revision_event_id or admitted.id,
            budget_revision_event_id=budget_revision_event_id,
        ))

    for operation in operations.values():
        if str(operation.get("status") or "") != "running":
            continue
        workflow_run_id = str(operation.get("workflow_run_id") or "")
        if workflow_run_id in blocked_runs or workflow_run_id in terminal_runs:
            continue
        limits = _operation_limits(runtime.state_dir, operation)
        if not _has_limits(limits):
            continue
        role_instance = str(operation.get("role_instance") or "")
        measurement = _measurement(
            baseline=operation.get("budget_snapshot"),
            current=usage_meter_snapshot(runtime, instance_id=role_instance),
            elapsed_seconds=max(
                0.0,
                now - _timestamp_epoch(str(operation.get("started_at") or "")),
            ),
            cost_fail_closed=bool(
                getattr(runtime.config, "budget_fail_closed", False)
            ),
        )
        exceeded = _exceeded_dimensions(limits, measurement)
        if not exceeded:
            continue
        task_id = str(
            operation.get("parent_task_id")
            or operation.get("task_id")
            or ""
        )
        emitted.extend(_trip_run(
            runtime,
            events=events,
            operations=operations,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
            scope="operation",
            scope_id=str(operation.get("operation_id") or ""),
            limits=limits,
            measurement=measurement,
            exceeded=exceeded,
            causation_id=str(operation.get("started_event_id") or ""),
            blocked_operation_id=str(operation.get("operation_id") or ""),
        ))
        blocked_runs.add(workflow_run_id)
    return emitted


def _trip_run(
    runtime: Any,
    *,
    events: list[ZfEvent],
    operations: Mapping[str, Mapping[str, Any]],
    workflow_run_id: str,
    task_id: str,
    scope: str,
    scope_id: str,
    limits: Mapping[str, float | int],
    measurement: Mapping[str, Any],
    exceeded: list[str],
    causation_id: str,
    blocked_operation_id: str = "",
    budget_revision_event_id: str = "",
) -> list[ZfEvent]:
    emitted: list[ZfEvent] = []
    if _budget_event_exists(
        events,
        scope=scope,
        scope_id=scope_id,
        budget_revision_event_id=budget_revision_event_id,
    ):
        return emitted
    detail = {
        "schema_version": BUDGET_EVENT_SCHEMA,
        "scope": scope,
        "scope_id": scope_id,
        "workflow_run_id": workflow_run_id,
        "task_id": task_id,
        "failure_class": "workflow_budget_exceeded",
        "exceeded_dimensions": exceeded,
        "limits": dict(limits),
        "measurement": dict(measurement),
    }
    if budget_revision_event_id:
        detail["budget_revision_event_id"] = budget_revision_event_id
    budget_event = runtime.event_writer.append(ZfEvent(
        type="workflow.budget.exceeded",
        actor="zf-cli",
        origin="kernel",
        task_id=task_id or None,
        payload=detail,
        causation_id=causation_id or None,
        correlation_id=workflow_run_id or None,
    ))
    emitted.append(budget_event)
    service = WorkflowOperationService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    terminated_roles: set[str] = set()
    for operation in operations.values():
        if (
            str(operation.get("workflow_run_id") or "") != workflow_run_id
            or str(operation.get("status") or "") != "running"
        ):
            continue
        role_instance = str(operation.get("role_instance") or "")
        if role_instance and role_instance not in terminated_roles:
            try:
                runtime.transport.terminate(role_instance)
            except Exception:
                pass
            terminated_roles.add(role_instance)
        operation_id = str(operation.get("operation_id") or "")
        common = {
            "operation_id": operation_id,
            "request_hash": str(operation.get("request_hash") or ""),
            "workflow_run_id": workflow_run_id,
            "task_id": str(operation.get("task_id") or ""),
            "reason": "workflow_budget_exceeded:" + ",".join(exceeded),
            "causation_id": budget_event.id,
            "correlation_id": workflow_run_id,
        }
        terminal = (
            service.block(
                **common,
                details={
                    "budget_scope": scope,
                    "budget_scope_id": scope_id,
                    "exceeded_dimensions": exceeded,
                },
            )
            if operation_id == blocked_operation_id
            else service.cancel(**common)
        )
        if terminal is not None:
            emitted.append(terminal)
    if not _run_terminal_exists(events, workflow_run_id):
        emitted.append(runtime.event_writer.append(ZfEvent(
            type="run.goal.blocked",
            actor="zf-cli",
            origin="kernel",
            task_id=task_id or None,
            payload={
                **detail,
                "run_id": workflow_run_id,
                "status": "blocked",
                "reason": "workflow_budget_exceeded:" + ",".join(exceeded),
                "origin_event_id": budget_event.id,
            },
            causation_id=budget_event.id,
            correlation_id=workflow_run_id or None,
        )))
    return emitted


def _operation_limits(
    state_dir: Path,
    operation: Mapping[str, Any],
) -> dict[str, float | int]:
    descriptor = operation.get("request_ref")
    if not isinstance(descriptor, Mapping):
        return {}
    try:
        body = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    except Exception:
        return {}
    request = body.get("request") if isinstance(body, Mapping) else None
    explicit = request.get("operation_limits") if isinstance(request, Mapping) else None
    if isinstance(explicit, Mapping) and _has_limits(_limits_dict(explicit)):
        return _limits_dict(explicit)
    execution = request.get("execution_profile") if isinstance(request, Mapping) else None
    profile = execution.get("profile") if isinstance(execution, Mapping) else None
    limits = profile.get("limits") if isinstance(profile, Mapping) else None
    return _limits_dict(limits)


def _effective_run_limits(
    events: list[ZfEvent],
    *,
    admitted_event: ZfEvent,
    workflow_run_id: str,
    configured_run_limits: Mapping[str, float | int],
    aliases: Mapping[str, str],
) -> tuple[dict[str, float | int], str]:
    admitted_payload = (
        admitted_event.payload
        if isinstance(admitted_event.payload, Mapping)
        else {}
    )
    pinned_limits = admitted_payload.get("run_limits")
    effective = (
        _limits_dict(pinned_limits)
        if isinstance(pinned_limits, Mapping)
        else dict(configured_run_limits)
    )
    revision_event_id = ""
    admitted_seen = False
    for event in events:
        if event.id == admitted_event.id:
            admitted_seen = True
            continue
        if not admitted_seen or event.type != "run.goal.updated":
            continue
        if event_run_id(event, aliases=aliases) != workflow_run_id:
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        patch = payload.get("run_limits_patch")
        if not isinstance(patch, Mapping):
            continue
        candidate = dict(effective)
        for key in (
            "timeout_seconds",
            "token_budget",
            "cost_budget_usd",
        ):
            if key in patch:
                candidate[key] = patch[key]
        try:
            effective = _validated_limits(candidate)
        except (TypeError, ValueError):
            continue
        revision_event_id = event.id
    return effective, revision_event_id


def _validated_limits(value: Mapping[str, Any]) -> dict[str, float | int]:
    limits = _limits_dict(value)
    if not 0 <= float(limits["timeout_seconds"]) <= 86_400:
        raise ValueError("invalid timeout_seconds")
    if not 0 <= int(limits["token_budget"]) <= 100_000_000:
        raise ValueError("invalid token_budget")
    if not 0 <= float(limits["cost_budget_usd"]) <= 10_000:
        raise ValueError("invalid cost_budget_usd")
    return limits


def _limits_dict(value: object) -> dict[str, float | int]:
    if isinstance(value, Mapping):
        source = value
    else:
        source = {
            "timeout_seconds": getattr(value, "timeout_seconds", 0.0),
            "max_usage_samples": getattr(value, "max_usage_samples", 0),
            "token_budget": getattr(value, "token_budget", 0),
            "cost_budget_usd": getattr(value, "cost_budget_usd", 0.0),
        }
    return {
        "timeout_seconds": float(source.get("timeout_seconds", 0.0) or 0.0),
        "max_usage_samples": int(source.get("max_usage_samples", 0) or 0),
        "token_budget": int(source.get("token_budget", 0) or 0),
        "cost_budget_usd": float(source.get("cost_budget_usd", 0.0) or 0.0),
    }


def _has_limits(limits: Mapping[str, float | int]) -> bool:
    return any(float(value or 0) > 0 for value in limits.values())


def _measurement(
    *,
    baseline: object,
    current: Mapping[str, Any],
    elapsed_seconds: float,
    cost_fail_closed: bool = False,
) -> dict[str, Any]:
    baseline_map = baseline if isinstance(baseline, Mapping) else {}
    compatible_scope = not (
        "instance_id" in baseline_map and "instance_id" in current
        and baseline_map["instance_id"] != current["instance_id"]
    )
    available = bool(
        baseline_map.get("meter_available")
        and current.get("meter_available")
        and compatible_scope
    )
    return {
        "meter_available": available,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "usage_samples": max(
            0,
            int(current.get("entries") or 0)
            - int(baseline_map.get("entries") or 0),
        ),
        "total_tokens": max(
            0,
            int(current.get("total_tokens") or 0)
            - int(baseline_map.get("total_tokens") or 0),
        ),
        "total_usd": round(max(
            0.0,
            float(current.get("total_usd") or 0.0)
            - float(baseline_map.get("total_usd") or 0.0),
        ), 6),
        "unpriced_entries": max(
            0,
            int(current.get("unpriced_entries") or 0)
            - int(baseline_map.get("unpriced_entries") or 0),
        ),
        "cost_fail_closed": bool(cost_fail_closed),
        "baseline": dict(baseline_map),
        "current": dict(current),
    }


def _exceeded_dimensions(
    limits: Mapping[str, float | int],
    measurement: Mapping[str, Any],
) -> list[str]:
    if not measurement.get("meter_available") and (
        float(limits.get("token_budget") or 0) > 0
        or float(limits.get("cost_budget_usd") or 0) > 0
    ):
        return ["meter_unavailable"]
    if (
        measurement.get("cost_fail_closed")
        and float(limits.get("cost_budget_usd") or 0) > 0
        and int(measurement.get("unpriced_entries") or 0) > 0
    ):
        return ["pricing_unavailable"]
    exceeded: list[str] = []
    checks = (
        ("wall_clock", "timeout_seconds", "elapsed_seconds"),
        ("usage_samples", "max_usage_samples", "usage_samples"),
        ("tokens", "token_budget", "total_tokens"),
        ("usd", "cost_budget_usd", "total_usd"),
    )
    for dimension, limit_key, current_key in checks:
        limit = float(limits.get(limit_key) or 0)
        if limit > 0 and float(measurement.get(current_key) or 0) >= limit:
            exceeded.append(dimension)
    return exceeded


def _event_epoch(event: ZfEvent) -> float:
    return _timestamp_epoch(str(event.ts or ""))


def _timestamp_epoch(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _budget_event_exists(
    events: list[ZfEvent],
    *,
    scope: str,
    scope_id: str,
    budget_revision_event_id: str = "",
) -> bool:
    return any(
        event.type == "workflow.budget.exceeded"
        and str((event.payload or {}).get("scope") or "") == scope
        and str((event.payload or {}).get("scope_id") or "") == scope_id
        and str(
            (event.payload or {}).get("budget_revision_event_id") or ""
        ) == budget_revision_event_id
        for event in events
    )


def _run_terminal_exists(events: list[ZfEvent], workflow_run_id: str) -> bool:
    terminal_types = {
        "run.goal.completed",
        "run.goal.blocked",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.abandoned",
    }
    return any(
        event.type in terminal_types
        and str(
            (event.payload or {}).get("workflow_run_id")
            or (event.payload or {}).get("run_id")
            or event.correlation_id
            or ""
        ) == workflow_run_id
        for event in events
    )


__all__ = [
    "BUDGET_EVENT_SCHEMA",
    "BUDGET_METER_SCHEMA",
    "enforce_active_workflow_budgets",
    "usage_meter_snapshot",
]
