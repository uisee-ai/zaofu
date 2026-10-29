"""Join canonical task, run and operation status without changing their owners."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent


def attach_execution_status(
    tasks: list[dict[str, Any]], operations: list[dict[str, Any]], events: list[ZfEvent],
) -> None:
    from zf.runtime.run_manager import build_run_goal_projection

    goals = {
        run_id: build_run_goal_projection(events, run_id=run_id)
        for run_id in {str(task.get("workflow_run_id") or "") for task in tasks}
        if run_id
    }
    # The projection passes operations ordered by task/generation/stage.
    latest_by_task = {row["task_id"]: row for row in operations}
    for task in tasks:
        goal = goals.get(task["workflow_run_id"], {})
        latest = latest_by_task.get(task["task_id"], {})
        task.update({
            "run_status": str(goal.get("status") or "unknown"),
            "run_status_event_id": str(goal.get("source_event_id") or ""),
            "last_operation_status": str(latest.get("status") or ""),
            "last_operation_reason": str(latest.get("reason") or ""),
            "last_operation_event_id": str(latest.get("last_event_id") or ""),
        })


def normalize_task_pipeline_operation_semantics(
    state_dir: Path,
    operations: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Recover a missing implementation verdict from its immutable result.

    Older ``dev.build.done`` producers stored ``passed`` under
    ``self_check.verdict`` while admission recorded ``pending`` at the
    operation level.  The sidecar is the durable semantic body, so the
    projection can derive the effective verdict without mutating events.
    """

    normalized: list[dict[str, Any]] = []
    for raw in operations:
        row = dict(raw)
        stage = str(row.get("stage") or row.get("task_pipeline_stage") or "")
        verdict = str(row.get("semantic_verdict") or "").strip().lower()
        descriptor = row.get("admitted_control_result_ref")
        if stage == "impl" and verdict == "pending" and isinstance(descriptor, Mapping):
            try:
                from zf.runtime.sidecar_refs import hydrate_sidecar_ref

                body = hydrate_sidecar_ref(
                    Path(state_dir), dict(descriptor)
                ).payload
            except Exception:
                body = None
            if isinstance(body, Mapping):
                effective = str(body.get("verdict") or "").strip()
                if not effective and isinstance(body.get("self_check"), Mapping):
                    effective = str(body["self_check"].get("verdict") or "").strip()
                if effective:
                    row["semantic_verdict"] = effective
        normalized.append(row)
    return normalized
