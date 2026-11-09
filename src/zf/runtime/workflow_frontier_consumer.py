"""Kernel/WRC consumer for the configured normal workflow frontier.

The frontier projection remains read-only and registered.  This module only
selects the existing continuation executor when ownership is explicitly
transferred to WRC; it does not create a second scheduler or state machine.
"""

from __future__ import annotations

from typing import Any

from zf.runtime.read_only_dynamic_continuation import (
    execute_read_only_continuation,
    pending_read_only_continuation_actions,
)
from zf.runtime.workflow_frontier_owner import normal_frontier_owner
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


def consume_wrc_normal_frontier(runtime: Any) -> list[WorkflowRuntimeDecision]:
    """Consume current registered frontier actions when WRC owns them."""

    if normal_frontier_owner(runtime.config) != "wrc":
        return []
    if runtime._dispatch_globally_paused():
        return []
    actions = pending_read_only_continuation_actions(
        runtime.state_dir,
        config=runtime.config,
        events=runtime.event_log.read_all(),
    )
    decisions: list[WorkflowRuntimeDecision] = []
    for action in actions:
        result = execute_read_only_continuation(
            runtime.state_dir,
            config=runtime.config,
            event_log=runtime.event_log,
            writer=runtime.event_writer,
            action=action,
            causation_id=str(action.get("source_event_id") or ""),
            project_root=getattr(runtime, "project_root", None),
            dispatch_actor="wrc",
        )
        if result.status in {"dispatched", "already_dispatched"}:
            decisions.append(WorkflowRuntimeDecision(
                action="dispatch",
                task_id=str(action.get("task_id") or ""),
                reason=(
                    "wrc normal frontier consumed: "
                    + (result.status or "dispatched")
                ),
            ))
        elif result.status in {"rejected", "superseded"}:
            decisions.append(WorkflowRuntimeDecision(
                action="blocked",
                task_id=str(action.get("task_id") or ""),
                reason=(
                    "wrc normal frontier "
                    + result.status
                    + (":" + result.reason if result.reason else "")
                ),
            ))
    return decisions


__all__ = ["consume_wrc_normal_frontier"]
