"""Restart-safe Task Map materialization journal over canonical stores."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.task_doc import write_task_doc


MATERIALIZATION_PLAN_SCHEMA = "task-map-materialization-plan.v1"


def prepare_task_map_materialization(
    *,
    state_dir: Path,
    tasks: Iterable[Task],
    task_map_ref: str,
    source_index_ref: str = "",
    package_id: str = "",
    package_ref: str = "",
    package_digest: str = "",
    writer: EventWriter | None = None,
    causation_id: str = "",
    correlation_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    task_list = list(tasks)
    plan = {
        "schema_version": MATERIALIZATION_PLAN_SCHEMA,
        "task_map_ref": task_map_ref,
        "source_index_ref": source_index_ref,
        "plan_artifact_package_id": package_id,
        "plan_artifact_package_ref": package_ref,
        "plan_artifact_package_digest": package_digest,
        "tasks": [asdict(task) for task in task_list],
    }
    descriptor = write_immutable_json_sidecar(
        state_dir,
        plan,
        root="task-map-materialization",
        kind="task_map_materialization_plan",
        schema_version=MATERIALIZATION_PLAN_SCHEMA,
        created_by="task-map-materializer",
        source_event_id=causation_id,
    )
    if writer is not None and not _journal_event_exists(
        writer,
        "task_map.materialization.prepared",
        descriptor["sha256"],
    ):
        writer.append(ZfEvent(
            type="task_map.materialization.prepared",
            actor="zf-cli",
            causation_id=causation_id or None,
            correlation_id=correlation_id or None,
            payload={
                "status": "prepared",
                "materialization_plan_ref": descriptor["ref"],
                "materialization_plan_digest": descriptor["sha256"],
                "task_map_ref": task_map_ref,
                "plan_artifact_package_id": package_id,
                "plan_artifact_package_ref": package_ref,
                "plan_artifact_package_digest": package_digest,
                "task_ids": [task.id for task in task_list],
            },
        ))
    return plan, descriptor


def commit_task_map_materialization(
    *,
    state_dir: Path,
    plan: dict[str, Any],
    descriptor: dict[str, Any],
    writer: EventWriter | None = None,
    actor: str = "zf-cli",
    causation_id: str = "",
    correlation_id: str = "",
    project_root: Path | None = None,
    fail_after_store_write: bool = False,
) -> dict[str, Any]:
    digest = str(descriptor.get("sha256") or "")
    if writer is not None:
        committed = _journal_payload(
            writer,
            "task_map.materialization.committed",
            digest,
        )
        if committed:
            return dict(committed)
    store = TaskStore(state_dir / "kanban.json")
    tasks = [store._to_task(row) for row in plan["tasks"]]
    created, skipped = store.add_many(tasks)
    if fail_after_store_write:
        if writer is not None:
            writer.append(ZfEvent(
                type="task_map.materialization.failed",
                actor=actor,
                causation_id=causation_id or None,
                correlation_id=correlation_id or None,
                payload={
                    "status": "failed",
                    "materialization_plan_ref": str(descriptor.get("ref") or ""),
                    "materialization_plan_digest": digest,
                    "task_map_ref": str(plan.get("task_map_ref") or ""),
                    "reason": "injected fault after TaskStore write",
                    "roll_forward_required": True,
                },
            ))
        raise RuntimeError("injected materialization fault after TaskStore write")
    existing_created_events = _created_task_event_ids(writer) if writer is not None else set()
    task_doc_failures: list[str] = []
    for task in tasks:
        try:
            write_task_doc(
                state_dir,
                task,
                source_event="task_map_materialization",
                project_root=project_root or state_dir.parent,
            )
            store.update(task.id, contract=task.contract)
        except Exception as exc:
            task_doc_failures.append(f"{task.id}: {exc}")
        if writer is None or task.id in existing_created_events:
            continue
        created_event = writer.append(ZfEvent(
            type="task.created",
            actor=actor,
            task_id=task.id,
            causation_id=causation_id or None,
            correlation_id=correlation_id or None,
            payload={
                "source": "task_map_materialization",
                "task_map_ref": str(plan.get("task_map_ref") or ""),
                "source_index_ref": str(plan.get("source_index_ref") or ""),
                "plan_artifact_package_ref": str(
                    plan.get("plan_artifact_package_ref") or ""
                ),
                "plan_artifact_package_digest": str(
                    plan.get("plan_artifact_package_digest") or ""
                ),
                "task": asdict(task),
            },
        ))
        writer.append(ZfEvent(
            type="task.contract.update",
            actor=actor,
            task_id=task.id,
            causation_id=created_event.id,
            correlation_id=created_event.correlation_id,
            payload={
                "source": "task_map_materialization",
                "task_map_ref": str(plan.get("task_map_ref") or ""),
                "contract": asdict(task.contract),
            },
        ))
    result = {
        "status": "committed",
        "materialization_plan_ref": str(descriptor.get("ref") or ""),
        "materialization_plan_digest": digest,
        "task_map_ref": str(plan.get("task_map_ref") or ""),
        "plan_artifact_package_id": str(
            plan.get("plan_artifact_package_id") or ""
        ),
        "plan_artifact_package_ref": str(
            plan.get("plan_artifact_package_ref") or ""
        ),
        "plan_artifact_package_digest": str(
            plan.get("plan_artifact_package_digest") or ""
        ),
        "created_task_ids": created,
        "skipped_task_ids": skipped,
        "task_ids": [task.id for task in tasks],
        "task_doc_failures": task_doc_failures,
    }
    if writer is not None:
        writer.append(ZfEvent(
            type="task_map.materialization.committed",
            actor=actor,
            causation_id=causation_id or None,
            correlation_id=correlation_id or None,
            payload=result,
        ))
    return result


def _journal_event_exists(
    writer: EventWriter,
    event_type: str,
    digest: str,
) -> bool:
    return bool(_journal_payload(writer, event_type, digest))


def _journal_payload(
    writer: EventWriter,
    event_type: str,
    digest: str,
) -> dict[str, Any]:
    for event in reversed(writer.event_log.read_all()):
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == event_type
            and str(payload.get("materialization_plan_digest") or "") == digest
        ):
            return payload
    return {}


def _created_task_event_ids(writer: EventWriter) -> set[str]:
    return {
        str(event.task_id or (event.payload or {}).get("task_id") or "")
        for event in writer.event_log.read_all()
        if event.type == "task.created"
    }


__all__ = [
    "MATERIALIZATION_PLAN_SCHEMA",
    "commit_task_map_materialization",
    "prepare_task_map_materialization",
]
