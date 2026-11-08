"""Apply explicit task replacement metadata during direct writer adoption."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore


def apply_explicit_task_supersedes(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    loaded: Any,
    trigger_event: ZfEvent | None = None,
) -> list[str]:
    """Cancel only ids explicitly replaced by a validated amended task-map."""

    payload = _task_map_payload(loaded)
    if not payload:
        return []
    amend = payload.get("amend") if isinstance(payload, dict) else {}
    amend = amend if isinstance(amend, dict) else {}
    raw_ids = amend.get("superseded_task_ids")
    if not isinstance(raw_ids, list):
        return []
    task_ids = _unique_strings(raw_ids)
    if not task_ids:
        return []
    cancelled: list[str] = []
    for task_id in task_ids:
        task = task_store.get(task_id)
        if task is None or task.status in {"done", "cancelled"}:
            continue
        updated = task_store.update(
            task_id,
            status="cancelled",
            blocked_reason=f"superseded by {loaded.task_map_ref}",
            active_dispatch_id="",
        )
        if updated is None:
            continue
        cancelled.append(task_id)
        event_writer.append(ZfEvent(
            type="task.superseded",
            actor="zf-cli",
            task_id=task_id,
            causation_id=trigger_event.id if trigger_event is not None else None,
            correlation_id=str(getattr(loaded, "workflow_run_id", "") or "") or None,
            payload={
                "workflow_run_id": str(getattr(loaded, "workflow_run_id", "") or ""),
                "source": "writer_task_map_adoption",
                "superseded_by_task_map_ref": str(loaded.task_map_ref or ""),
                "superseded_task_ids": task_ids,
                "status": "cancelled",
            },
        ))
    return cancelled


def reconcile_retained_task_dependencies(
    *,
    task_store: TaskStore,
    event_writer: EventWriter,
    loaded: Any,
    state_dir: Path,
    project_root: Path,
) -> list[str]:
    """Adopt amended dependency edges without reopening retained tasks."""

    payload = _task_map_payload(loaded)
    amend = payload.get("amend") if isinstance(payload, dict) else {}
    amend = amend if isinstance(amend, dict) else {}
    superseded = set(_unique_strings(amend.get("superseded_task_ids") or []))
    raw_tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if not superseded or not isinstance(raw_tasks, list):
        return []

    requested = set(_unique_strings(
        list(getattr(loaded, "requested_task_ids", []) or [])
    ))
    updated_ids: list[str] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            continue
        task_id = str(
            raw.get("task_id") or raw.get("id") or raw.get("task") or ""
        ).strip()
        if not task_id or task_id in requested or task_id in superseded:
            continue
        task = task_store.get(task_id)
        if task is None or task.status in {"done", "cancelled"}:
            continue
        current = _unique_strings(list(task.blocked_by or []))
        desired = _unique_strings(_dependency_values(raw.get("blocked_by")))
        if current == desired:
            continue
        removed = set(current) - set(desired)
        if not removed or not removed.issubset(superseded):
            continue
        missing = [
            dependency
            for dependency in desired
            if task_store.get(dependency) is None
        ]
        if missing:
            raise ValueError(
                f"amended task dependencies are not materialized: "
                f"{task_id} -> {', '.join(missing)}"
            )

        refreshed = deepcopy(task)
        refreshed.blocked_by = desired
        from zf.runtime.task_doc import write_task_doc

        task_doc = write_task_doc(
            state_dir,
            refreshed,
            source_event="gap_task_map_dependency_adoption",
            project_root=project_root,
        )
        updated = task_store.update(
            task_id,
            blocked_by=desired,
        )
        if updated is None:
            continue
        from zf.runtime.task_doc import task_doc_contract_metadata

        updated = task_store.patch_contract_fields(
            task_id,
            task_doc_contract_metadata(task_doc),
        ) or updated
        updated_ids.append(task_id)
        event_writer.append(ZfEvent(
            type="task.updated",
            actor="zf-cli",
            task_id=task_id,
            correlation_id=str(getattr(loaded, "workflow_run_id", "") or "") or None,
            payload={
                "source": "gap_task_map_dependency_adoption",
                "task_map_ref": str(getattr(loaded, "task_map_ref", "") or ""),
                "superseded_task_ids": sorted(superseded),
                "previous_blocked_by": current,
                "updates": {"blocked_by": desired},
                "task": asdict(updated),
            },
        ))
    return updated_ids


def _task_map_payload(loaded: Any) -> dict[str, Any]:
    try:
        payload = json.loads(
            Path(loaded.task_map_path).read_text(encoding="utf-8")
        )
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dependency_values(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _unique_strings(values: list[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


__all__ = [
    "apply_explicit_task_supersedes",
    "reconcile_retained_task_dependencies",
]
