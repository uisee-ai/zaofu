"""Apply explicit task replacement metadata during direct writer adoption."""

from __future__ import annotations

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

    try:
        payload = json.loads(Path(loaded.task_map_path).read_text(encoding="utf-8"))
    except Exception:
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
            correlation_id=(
                trigger_event.correlation_id if trigger_event is not None else None
            ),
            payload={
                "source": "writer_task_map_adoption",
                "superseded_by_task_map_ref": str(loaded.task_map_ref or ""),
                "superseded_task_ids": task_ids,
                "status": "cancelled",
            },
        ))
    return cancelled


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


__all__ = ["apply_explicit_task_supersedes"]
