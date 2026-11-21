"""Mechanical consistency for completed Tasks outside a writer refresh."""
from collections.abc import Mapping
from pathlib import Path

from zf.core.task.store import TaskStore


def preserved_task_errors(*, state_dir: Path, previous: Mapping, current: Mapping,
                          trigger: Mapping) -> list[dict[str, str]]:
    # A rejected candidate is not an authoritative preservation baseline. Nor
    # does an input/read scope grant permission to redispatch completed Tasks.
    run_id = str(trigger.get("workflow_run_id") or trigger.get("trace_id") or "")
    if not run_id or not all(trigger.get(key) for key in (
        "plan_artifact_package_ref", "plan_artifact_package_digest",
    )):
        return []
    from zf.runtime.writer_fanout_admission import select_writer_task_items, writer_task_items

    try:
        selected, _, _, _ = select_writer_task_items(dict(previous), writer_task_items(dict(previous)), dict(trigger))
    except (RuntimeError, ValueError) as exc:
        return [{"code": "preserved_task_scope_unresolved", "field": "trigger_payload.task_ids",
                 "message": "Cannot bind preservation scope to the pinned Plan: " + str(exc)}]
    # Candidate-authored amend/resume_scope must not exempt a previously
    # retained Task. Successors of selected predecessors still follow normal
    # writer admission; this comparison does not grant them edit authority.
    selected_ids = {row["task_id"] for row in selected}
    rows = {str(row.get("task_id") or row.get("id") or ""): row
            for row in current.get("tasks", []) if isinstance(row, Mapping)}
    store = TaskStore(state_dir / "kanban.json")
    errors = []
    for row in previous.get("tasks", []):
        if not isinstance(row, Mapping):
            continue
        task_id = str(row.get("task_id") or row.get("id") or "")
        if not task_id or task_id in selected_ids:
            continue
        task = store.get(task_id)
        if task is None or task.status != "done" or task.contract is None:
            continue
        if task.contract.evidence_contract.get("workflow_run_id") != run_id:
            continue
        if rows.get(task_id) != row:
            errors.append({"code": "preserved_task_changed", "field": f"task_map.tasks[{task_id}]",
                "message": "Completed Task is outside the selected writer refresh but its bound Plan row "
                "changed or disappeared. Restore the pinned row, or have the decision owner explicitly "
                "include its replacement/revalidation in the recovery scope; prior completion is not "
                "evidence for a changed contract."})
    return errors
