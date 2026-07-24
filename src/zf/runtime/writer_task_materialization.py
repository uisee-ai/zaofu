"""Plan-package binding and journaled materialization for writer task maps."""

from __future__ import annotations

from typing import Any


def bind_plan_package_source_refs(
    source_refs: dict[str, str],
    loaded: Any,
) -> None:
    package_id = str(getattr(loaded, "plan_artifact_package_id", "") or "")
    package_ref = str(getattr(loaded, "plan_artifact_package_ref", "") or "")
    package_digest = str(
        getattr(loaded, "plan_artifact_package_digest", "") or ""
    )
    if package_id:
        source_refs["plan_artifact_package_id"] = package_id
    if package_ref:
        source_refs["plan_artifact_package_ref"] = package_ref
    if package_digest:
        source_refs["plan_artifact_package_digest"] = package_digest


def materialize_writer_tasks(
    runtime: Any,
    tasks: list[Any],
    loaded: Any,
) -> None:
    if not tasks:
        return
    from zf.runtime.task_map_materialization import (
        commit_task_map_materialization,
        prepare_task_map_materialization,
    )

    plan, descriptor = prepare_task_map_materialization(
        state_dir=runtime.state_dir,
        tasks=tasks,
        task_map_ref=loaded.task_map_ref,
        source_index_ref=loaded.source_index_ref,
        package_id=str(
            getattr(loaded, "plan_artifact_package_id", "") or ""
        ),
        package_ref=str(
            getattr(loaded, "plan_artifact_package_ref", "") or ""
        ),
        package_digest=str(
            getattr(loaded, "plan_artifact_package_digest", "") or ""
        ),
        writer=runtime.event_writer,
    )
    commit_task_map_materialization(
        state_dir=runtime.state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=runtime.event_writer,
        project_root=runtime.project_root,
    )


__all__ = ["bind_plan_package_source_refs", "materialize_writer_tasks"]
