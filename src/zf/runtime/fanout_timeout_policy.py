"""Timeout policy for fanout children that have not acquired execution."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


def close_expired_queued_wait(
    writer: EventWriter,
    *,
    manifest: dict[str, Any],
    fanout_epoch: float | None,
    now: float,
    timeout_seconds: int,
    eligible_queued_child_ids: set[str] | None = None,
) -> bool:
    """Close a queue-only fanout without charging a semantic child attempt."""

    queued_waiters = [
        child
        for child in manifest.get("children", []) or []
        if isinstance(child, dict)
        and str(child.get("status") or "") == "queued"
        and (
            eligible_queued_child_ids is None
            or str(child.get("child_id") or "") in eligible_queued_child_ids
        )
    ]
    active_execution_children = [
        child
        for child in manifest.get("children", []) or []
        if isinstance(child, dict)
        and str(child.get("status") or "") in {"pending", "assigned", "dispatched"}
    ]
    if (
        not queued_waiters
        or active_execution_children
        or fanout_epoch is None
        or now - fanout_epoch < timeout_seconds
    ):
        return False
    queued_child_ids = [
        str(child.get("child_id") or "")
        for child in queued_waiters
        if str(child.get("child_id") or "")
    ]
    writer.append(ZfEvent(
        type="fanout.cancelled",
        actor="zf-cli",
        payload={
            "fanout_id": str(manifest.get("fanout_id") or ""),
            "trace_id": str(manifest.get("trace_id") or ""),
            "stage_id": str(manifest.get("stage_id") or ""),
            "pdd_id": str(manifest.get("pdd_id") or ""),
            "feature_id": str(manifest.get("feature_id") or ""),
            "task_map_ref": str(manifest.get("task_map_ref") or ""),
            "source_index_ref": str(manifest.get("source_index_ref") or ""),
            "target_ref": str(manifest.get("target_ref") or ""),
            "dispatch_base_commit": str(
                manifest.get("dispatch_base_commit") or ""
            ),
            "reason": "queued_wait_timeout",
            "failure_kind": "scheduler_queue_timeout",
            "queued_children": queued_child_ids,
            "timeout_seconds": timeout_seconds,
            "semantic_attempt_consumed": False,
        },
        correlation_id=str(manifest.get("trace_id") or ""),
    ))
    return True


__all__ = ["close_expired_queued_wait"]
