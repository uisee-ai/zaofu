"""Deterministic failed-task rework scope expansion."""

from __future__ import annotations

import json
from pathlib import Path

from zf.runtime.artifact_refs import resolve_runtime_artifact_ref
from zf.runtime.writer_fanout_admission import writer_task_items


def expand_rework_task_ids(
    failed_task_ids: list[str],
    *,
    task_map_ref: str,
    state_dir: Path,
    project_root: Path,
    completed_task_ids: set[str] | None = None,
) -> list[str]:
    """Return failed tasks plus every transitive downstream consumer."""
    failed = _dedupe(failed_task_ids)
    if not failed or not str(task_map_ref or "").strip():
        return failed
    path = resolve_runtime_artifact_ref(
        task_map_ref,
        project_root=Path(project_root),
        state_dir=Path(state_dir),
    )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return failed
    items = writer_task_items(data)
    if not items:
        return failed
    closure = set(failed)
    changed = True
    while changed:
        changed = False
        for item in items:
            task_id = str(item.get("task_id") or "").strip()
            dependencies = {
                str(value).strip()
                for value in (
                    list(item.get("blocked_by") or [])
                    + list(item.get("depends_on") or [])
                )
                if str(value or "").strip()
            }
            if task_id and task_id not in closure and dependencies & closure:
                closure.add(task_id)
                changed = True
    completed = set(completed_task_ids or set()) - set(failed)
    ordered = [
        str(item.get("task_id") or "")
        for item in items
        if str(item.get("task_id") or "") in closure
        and str(item.get("task_id") or "") not in completed
    ]
    return _dedupe([*failed, *ordered])


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(
        str(value).strip() for value in values if str(value or "").strip()
    ))


__all__ = ["expand_rework_task_ids"]
