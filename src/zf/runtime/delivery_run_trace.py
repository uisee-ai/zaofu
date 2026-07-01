"""Additive task-flow/run/span projections for ``delivery-trace.v1``."""

from __future__ import annotations

from typing import Any

from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.runtime.delivery_projection_common import EventSlice
from zf.runtime.delivery_run_groups import build_run_groups
from zf.runtime.delivery_span_trace import build_run_trace
from zf.runtime.delivery_task_flow import build_task_flow


def build_delivery_run_projection(
    *,
    config: Any,
    events: EventSlice,
    tasks: dict[str, Task],
    workflow_trace: dict[str, Any],
    execution_graph: dict[str, Any] | None = None,
    autoresearch_cycles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build Web-facing Delivery cockpit projections without mutating state."""

    task_flow = build_task_flow(
        config=config,
        events=events,
        tasks=tasks,
        workflow_trace=workflow_trace,
        execution_graph=execution_graph or {},
    )
    run_groups = build_run_groups(
        events=events,
        tasks=tasks,
        workflow_trace=workflow_trace,
        task_flow=task_flow,
    )
    trace = build_run_trace(
        events=events,
        tasks=tasks,
        run_groups=run_groups,
        autoresearch_cycles=autoresearch_cycles or [],
    )
    return redact_obj({
        "task_flow": task_flow,
        "run_groups": run_groups,
        "trace": trace,
    })
