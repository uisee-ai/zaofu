"""Dispatch-time binding of a writer Task contract to its prepared worktree."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.orchestrator_dispatch import _capture_head, _git_rev_parse
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    build_task_contract_snapshot,
    contract_snapshot_identity_fields,
    current_task_contract_identity,
    descriptor_from_payload as contract_descriptor_from_payload,
    hydrate_task_contract_snapshot,
    snapshot_payload_fields,
    task_map_generation,
    write_task_contract_snapshot,
)


def prepare_writer_fanout_contract_snapshot(
    *,
    state_dir: Path,
    project_root: Path,
    config: Any,
    task: Any,
    task_item: dict[str, Any],
    context: Any,
    project_path: str,
    dependency_result: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the immutable contract bound to the worktree about to run."""
    task_id = str(task_item.get("task_id") or "")
    if task is None:
        raise TaskContractSnapshotError(
            f"cannot snapshot missing canonical task {task_id!r}"
        )
    workflow_run_id = str(
        task_item.get("workflow_run_id")
        or getattr(context, "trace_id", "")
        or ""
    )
    generation_id = task_map_generation(
        task,
        task_map_ref=str(task_item.get("task_map_ref") or ""),
    )
    dependency_base_commit = _dependency_composed_base_commit(
        dependency_result,
    )
    if dependency_base_commit:
        observed_workdir_head = _capture_head(Path(project_path))
        if observed_workdir_head and observed_workdir_head != dependency_base_commit:
            raise TaskContractSnapshotError(
                "dependency workdir head differs from recorded dependency base"
            )
    try:
        descriptor = contract_descriptor_from_payload(task_item)
    except TaskContractSnapshotError:
        descriptor = {}
    if descriptor:
        snapshot = hydrate_task_contract_snapshot(
            state_dir,
            descriptor,
            expected=current_task_contract_identity(
                task,
                task_map_ref=str(task_item.get("task_map_ref") or ""),
            ),
        )
    if not descriptor or (
        dependency_base_commit
        and str(snapshot.get("base_commit") or "") != dependency_base_commit
    ):
        # A completed dependency changes the code the worker receives. Existing
        # task output from a repair is not a new baseline, so it must not cause
        # a snapshot rebind merely because its worktree is ahead of dispatch.
        base_ref = str(
            task_item.get("base_commit")
            or task_item.get("source_commit")
            or task_item.get("dispatch_base_commit")
            or getattr(context, "target_ref", "")
            or ""
        ).strip()
        base_commit = dependency_base_commit or (
            _git_rev_parse(project_root, base_ref) if base_ref else ""
        ) or _capture_head(project_root)
        snapshot = build_task_contract_snapshot(
            task,
            workflow_run_id=workflow_run_id,
            task_map_generation_id=generation_id,
            base_commit=base_commit,
            task_ref=f"{config.runtime.git.task_ref_prefix}/{task_id}",
        )
        descriptor = write_task_contract_snapshot(
            state_dir,
            snapshot,
            source_event_id=str(getattr(context, "trigger_event_id", "") or ""),
        )
    fields = {
        **snapshot_payload_fields(descriptor),
        **contract_snapshot_identity_fields(snapshot),
    }
    task_item.update(fields)
    task_payload = task_item.get("payload")
    if isinstance(task_payload, dict):
        for key, value in fields.items():
            task_payload.setdefault(key, value)
    return snapshot, descriptor


def _dependency_composed_base_commit(
    dependency_result: Mapping[str, Any] | None,
) -> str:
    if not isinstance(dependency_result, Mapping):
        return ""
    has_dependency_ref = any(
        isinstance(dependency_result.get(key), list)
        and bool(dependency_result.get(key))
        for key in ("applied_dependency_refs", "skipped_dependency_refs")
    )
    if not has_dependency_ref:
        return ""
    return str(dependency_result.get("after") or "").strip()
