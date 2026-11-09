"""Deterministic dependency-failure closure for writer fanouts."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent


def writer_task_dependency_ids(task_item: Mapping[str, Any]) -> list[str]:
    """Return stable, de-duplicated dependencies from writer task envelopes."""

    def _coerce(value: object) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    out: list[str] = []
    seen: set[str] = set()
    sources: list[Mapping[str, Any]] = [task_item]
    for key in ("payload", "raw_task"):
        value = task_item.get(key)
        if isinstance(value, Mapping):
            sources.append(value)
    for source in sources:
        for key in ("blocked_by", "depends_on"):
            for dependency_id in _coerce(source.get(key)):
                if dependency_id not in seen:
                    seen.add(dependency_id)
                    out.append(dependency_id)
    return out


def writer_task_dependencies_satisfied(
    task_store: Any,
    task_item: Mapping[str, Any],
    *,
    completed_task_ids: set[str] | None = None,
) -> bool:
    """Require admitted completion; cancellation does not fulfill an input."""

    terminal_statuses = {"done"}
    completed_task_ids = completed_task_ids or set()
    for dependency_id in writer_task_dependency_ids(task_item):
        if dependency_id in completed_task_ids:
            continue
        task = task_store.get(dependency_id)
        if task is None or str(task.status or "") not in terminal_statuses:
            return False
    return True


def blocked_writer_children(
    children: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return pending children transitively blocked by failed task dependencies."""

    rows = [dict(child) for child in children]
    failed_task_ids = {
        str(child.get("task_id") or "").strip()
        for child in rows
        if str(child.get("status") or "") == "failed"
        and str(child.get("task_id") or "").strip()
    }
    blocked: list[dict[str, Any]] = []
    pending = [
        child
        for child in rows
        if str(child.get("status") or "") in {"pending", "queued"}
    ]
    while pending:
        progressed = False
        for child in list(pending):
            payload = (
                child.get("payload")
                if isinstance(child.get("payload"), Mapping)
                else {}
            )
            dependencies = set(writer_task_dependency_ids(child))
            failed_dependencies = sorted(dependencies & failed_task_ids)
            if not failed_dependencies:
                continue
            task_id = str(
                child.get("task_id") or payload.get("task_id") or ""
            ).strip()
            blocked.append({
                "child": child,
                "failed_dependencies": failed_dependencies,
            })
            if task_id:
                failed_task_ids.add(task_id)
            pending.remove(child)
            progressed = True
        if not progressed:
            break
    return blocked


def close_blocked_writer_dependencies(
    event_writer: Any,
    *,
    fanout_id: str,
    manifest: Mapping[str, Any],
) -> bool:
    """Fail queued writer descendants whose upstream dependency failed."""

    children = [
        dict(child)
        for child in manifest.get("children", []) or []
        if isinstance(child, Mapping)
    ]
    blocked_children = blocked_writer_children(children)
    if not blocked_children:
        return False
    failure_event_by_task = {
        str(child.get("task_id") or ""): str(child.get("last_event_id") or "")
        for child in children
        if str(child.get("status") or "") == "failed"
    }
    trace_id = str(manifest.get("trace_id") or "")
    stage_id = str(manifest.get("stage_id") or "")
    for blocked in blocked_children:
        child = blocked["child"]
        child_payload = (
            dict(child.get("payload") or {})
            if isinstance(child.get("payload"), Mapping)
            else {}
        )
        task_id = str(
            child.get("task_id") or child_payload.get("task_id") or ""
        )
        failed_dependencies = list(blocked["failed_dependencies"])
        cause_ids = [
            failure_event_by_task[dependency_id]
            for dependency_id in failed_dependencies
            if failure_event_by_task.get(dependency_id)
        ]
        failed_event = event_writer.append(ZfEvent(
            type="fanout.child.failed",
            actor="orchestrator",
            origin="kernel",
            task_id=task_id or None,
            payload={
                **child_payload,
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "child_id": str(child.get("child_id") or ""),
                "run_id": str(child.get("run_id") or ""),
                "role_instance": str(child.get("role_instance") or ""),
                "task_id": task_id,
                "status": "failed",
                "reason": (
                    "upstream dependency failed: "
                    + ", ".join(failed_dependencies)
                ),
                "failure_class": "upstream_dependency_failed",
                "blocked_by_task_ids": failed_dependencies,
                "upstream_failure_event_ids": cause_ids,
            },
            causation_id=cause_ids[0] if cause_ids else None,
            correlation_id=trace_id,
        ))
        if task_id:
            failure_event_by_task[task_id] = failed_event.id
    return True


__all__ = [
    "blocked_writer_children",
    "close_blocked_writer_dependencies",
    "writer_task_dependencies_satisfied",
    "writer_task_dependency_ids",
]
