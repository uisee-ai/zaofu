"""Preserve exact TaskRefs when a stopped read-only re-dispatch moved them."""
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_continuation import _git_commit
from zf.runtime.task_refs import TaskRefManager
from zf.runtime.task_pipeline_entry import task_pipeline_entry_mode


def validate_reference(service: Any, task: Any, receipt: dict, context: dict, head: str) -> dict:
    manager = TaskRefManager(state_dir=service.state_dir,
        project_root=service.project_root or service.state_dir.parent, config=service.config)
    task_ref = str(receipt.get("task_ref") or "")
    source_commit = str(receipt.get("task_commit") or "")
    if task_ref != f"{service.config.runtime.git.task_ref_prefix}/{task.id}" or not source_commit:
        raise ValueError("completion reuse receipt TaskRef identity differs")
    entry = manager._read_index_unlocked().get(task.id, {})
    actual = _git_commit(Path(manager.project_root), "refs/heads/" + task_ref)
    if entry.get("task_ref") != task_ref or entry.get("source_commit") != actual:
        raise ValueError("completion reuse TaskRef/index disagree")
    readonly = task_pipeline_entry_mode(task) == "verify_only" and not task.contract.scope
    if actual != source_commit:
        diagnostics = entry.get("diagnostics") or {}
        if (not readonly or actual != head or entry.get("run_id") != context["workflow_run_id"]
                or entry.get("trigger_event_id") != context["generation_admitted_event_id"]
                or diagnostics.get("read_only") is not True or diagnostics.get("write_free") is not True):
            raise ValueError("completion reuse TaskRef changed beyond stopped read-only admission")
    # Stable across a partial recovery: the only allowed transition is the
    # reviewed Candidate head back to its original integrated read-only target.
    return {"task_ref": task_ref, "source_commit": source_commit,
            "readonly_restore_from": head if readonly else ""}


def restore_reference(service: Any, row: dict, manifest: dict, descriptor: dict, requested: ZfEvent) -> None:
    ref = row["task_ref_proof"]
    root = service.project_root or service.state_dir.parent
    current = _git_commit(Path(root), "refs/heads/" + ref["task_ref"])
    if current == ref["source_commit"]:
        return
    if not ref["readonly_restore_from"] or current != ref["readonly_restore_from"]:
        raise ValueError("TaskRef changed during completion reuse")
    manager = TaskRefManager(state_dir=service.state_dir, project_root=root, config=service.config)
    entry = manager._read_index_unlocked().get(row["task_id"], {})
    result = manager.admit_read_only_target(task_id=row["task_id"], source_commit=ref["source_commit"],
        expected_current_commit=current, trigger_event_id=requested.id,
        trace_id=manifest["workflow_run_id"], pdd_id=str(entry.get("pdd_id") or ""),
        feature_id=str(entry.get("feature_id") or ""))
    service.writer.append(ZfEvent(type="task.ref.updated", origin="kernel", actor=service.actor,
        task_id=row["task_id"], causation_id=requested.id, correlation_id=manifest["workflow_run_id"],
        payload={**result.payload, "workflow_run_id": manifest["workflow_run_id"],
                 "task_map_generation": manifest["prior_task_map_generation"],
                 "completion_reuse_ref": descriptor, "source": "plan_completion_reuse"}))
