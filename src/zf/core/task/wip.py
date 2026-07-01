"""WIP enforcement — per-worker work-in-progress limits."""

from __future__ import annotations

from typing import Callable

from zf.core.task.store import TaskStore


class WipEnforcer:
    """Enforce WIP limits per worker."""

    def __init__(self, limit: int = 1) -> None:
        self.limit = limit

    def can_accept(
        self,
        worker_id: str,
        store: TaskStore,
        latest_dispatched: dict[str, str] | None = None,
        *,
        equivalent: Callable[[str, str], bool] | None = None,
    ) -> bool:
        """Check if worker can accept a new task.

        R-TASK-STATE-AXIS-01 (2026-04-27): when ``latest_dispatched``
        is supplied, use it as the source of truth for "currently in
        flight on this worker" — the same fix B-REASSIGN-DISPATCH-01
        applied locally to ``_dispatch_ready`` C3 path now extends
        here. Tasks merely *assigned* (re-routed) but not yet
        *dispatched* don't count as occupying a slot. Without this,
        N tasks reassigned to the same single-replica role gridlock
        each other (each sees the others as active peers, all skip).
        Backwards-compatible: callers that don't pass ``latest_dispatched``
        keep the legacy ``assigned_to + status==in_progress`` count.
        """
        # Truthy check: empty dict is treated like None (no events
        # observed → fall back to legacy ``assigned_to + in_progress``
        # count). This preserves fixtures that build kanban state
        # directly without an event log; in production events.jsonl
        # always carries at least session.started, so a non-empty dict
        # is the steady state and the new logic dominates.
        # 2026-06-10 review P1-8: some emitters (workflow resume) key
        # task.dispatched by bare role name ("dev") while dispatch queries
        # by instance_id ("dev-1"). Exact equality missed those in-flight
        # tasks and double-dispatched into the same pane; ``equivalent``
        # (the caller's role-name↔instance_id mapping) closes the gap.
        def _matches(latest: str) -> bool:
            if latest == worker_id:
                return True
            return bool(
                equivalent is not None and latest and equivalent(latest, worker_id)
            )

        if latest_dispatched:
            active = [
                t for t in store.list_all()
                if t.status == "in_progress"
                and _matches(latest_dispatched.get(t.id, ""))
            ]
        else:
            active = [
                t for t in store.list_all()
                if t.assigned_to == worker_id and t.status == "in_progress"
            ]
        return len(active) < self.limit

    def reject_reason(
        self,
        worker_id: str,
        store: TaskStore,
        latest_dispatched: dict[str, str] | None = None,
        *,
        equivalent: Callable[[str, str], bool] | None = None,
    ) -> str | None:
        """Return rejection reason or None if can accept."""
        if self.can_accept(
            worker_id, store, latest_dispatched, equivalent=equivalent,
        ):
            return None
        return f"Worker {worker_id} at WIP limit ({self.limit})"
