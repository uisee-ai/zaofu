"""Board transport summaries; canonical projections and metrics stay unchanged."""

from __future__ import annotations

from typing import Any

from zf.core.security.redaction import redact_obj


def board_snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Omit large detail bodies and redact task summaries without mutating the cache.

    Event identity and child report refs remain available. The existing task /
    fanout detail APIs still own complete payloads; numeric metrics, counts,
    display states, archive rows and controlled-action metadata are preserved.
    """
    result = {**snapshot, "snapshot_slice": "board"}
    for key in ("tasks", "archive_tasks"):
        result[key] = [_task_summary(task) for task in snapshot.get(key, [])]
    return result


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    result = dict(task)
    event = task.get("latest_event")
    if isinstance(event, dict):
        result["latest_event"] = {key: value for key, value in event.items() if key != "payload"}
    fanout = task.get("fanout")
    if isinstance(fanout, dict) and isinstance(fanout.get("current_child"), dict):
        result["fanout"] = {
            **fanout,
            "current_child": {key: value for key, value in fanout["current_child"].items() if key != "report"},
        }
    return redact_obj(result)
