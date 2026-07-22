"""Single-writer fencing and queued task lifecycle for writer fanout."""

from __future__ import annotations

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.runtime.terminal_events import is_task_attempt_terminal_event


class WriterDispatchFenceMixin:
    """Keep one canonical writer attempt active for each task."""

    def _writer_task_dispatch_fence_reason(
        self,
        task_id: str,
        *,
        role_instance: str,
        run_id: str,
    ) -> str:
        if not task_id:
            return ""
        task = self.task_store.get(task_id)
        active_dispatch_id = str(
            getattr(task, "active_dispatch_id", "") or ""
        ) if task is not None else ""
        if (
            active_dispatch_id
            and active_dispatch_id != run_id
            and self._task_dispatch_id_is_active(task_id, active_dispatch_id)
        ):
            return "task_attempt_fence_active"
        states = getattr(self, "_last_worker_state", {})
        task_ids = getattr(self, "_last_worker_task_id", {})
        for instance_id, active_task_id in task_ids.items():
            if (
                str(active_task_id or "") == task_id
                and str(states.get(instance_id) or "") == "busy"
                and (instance_id != role_instance or active_dispatch_id != run_id)
            ):
                return "task_writer_busy_fence_active"
        return ""

    def _task_dispatch_id_is_active(self, task_id: str, dispatch_id: str) -> bool:
        try:
            events = self.event_log.read_all()
        except Exception:
            return True
        start_index = -1
        for index, event in enumerate(events):
            if event.type not in {"task.dispatched", "fanout.child.dispatched"}:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            event_task_id = str(event.task_id or payload.get("task_id") or "")
            event_dispatch_id = str(
                payload.get("run_id") or payload.get("dispatch_id") or ""
            )
            if event_task_id == task_id and event_dispatch_id == dispatch_id:
                start_index = index
        if start_index < 0:
            return False

        for event in events[start_index + 1:]:
            payload = event.payload if isinstance(event.payload, dict) else {}
            event_task_id = str(event.task_id or payload.get("task_id") or "")
            event_dispatch_id = str(
                payload.get("run_id") or payload.get("dispatch_id") or ""
            )
            if event_task_id != task_id or event_dispatch_id != dispatch_id:
                continue
            if (
                event.type == "fanout.child.dispatch_lost"
                or is_task_attempt_terminal_event(event.type)
            ):
                return False
        return True

    def _release_superseded_writer_fanout_dispatches(
        self,
        *,
        fanout_id: str,
        manifest: dict,
        reason: str,
        superseded_by: str,
    ) -> None:
        """Release only task bindings that still point at this old fanout."""

        if str(manifest.get("topology") or "") != "fanout_writer_scoped":
            return
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict) or str(child.get("status") or "") != "dispatched":
                continue
            task_id = str(child.get("task_id") or "")
            run_id = str(child.get("run_id") or "")
            role_instance = str(child.get("role_instance") or "")
            if not task_id or not run_id:
                continue
            task = self.task_store.get(task_id)
            if task is None or str(task.active_dispatch_id or "") != run_id:
                continue
            if self._task_dispatch_id_is_active(task_id, run_id):
                continue
            self.event_writer.append(ZfEvent(
                type="fanout.child.dispatch_lost",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": str(manifest.get("trace_id") or ""),
                    "stage_id": str(manifest.get("stage_id") or ""),
                    "child_id": str(child.get("child_id") or ""),
                    "run_id": run_id,
                    "role_instance": role_instance,
                    "task_id": task_id,
                    "reason": reason,
                    "superseded_by": superseded_by,
                    "source": "superseded_writer_fanout_dispatch_release",
                },
                causation_id=str(child.get("last_event_id") or "") or None,
                correlation_id=str(manifest.get("trace_id") or "") or None,
            ))
            self.task_store.update(
                task_id,
                assigned_to="",
                active_dispatch_id="",
            )
            if getattr(self, "_active_dispatch_ids", {}).get(task_id) == run_id:
                self._active_dispatch_ids.pop(task_id, None)
            if role_instance:
                self._set_worker_state(
                    role_instance,
                    "idle",
                    reason="superseded writer fanout dispatch released",
                    task_id=task_id,
                    force=True,
                )

    def _defer_writer_fanout_dispatch(
        self,
        *,
        context,
        child,
        task_item: dict,
        role: RoleConfig,
        run_id: str,
        causation_id: str,
        reason: str,
        release_slot: bool,
    ) -> None:
        if self._fanout_dispatch_deferred_recently(
            fanout_id=context.fanout_id,
            child_id=child.child_id,
            role_instance=role.instance_id,
        ):
            return
        task_id = str(task_item.get("task_id") or "")
        task = self.task_store.get(task_id) if task_id else None
        self.event_writer.append(ZfEvent(
            type="fanout.child.dispatch_deferred",
            actor="zf-cli",
            task_id=task_id or None,
            payload={
                "fanout_id": context.fanout_id,
                "trace_id": context.trace_id,
                "stage_id": context.stage_id,
                "child_id": child.child_id,
                "run_id": run_id,
                "role_instance": role.instance_id,
                "task_id": task_id,
                "reason": reason,
                "active_dispatch_id": str(
                    getattr(task, "active_dispatch_id", "") or ""
                ) if task is not None else "",
                "active_owner": str(
                    getattr(task, "assigned_to", "") or ""
                ) if task is not None else "",
            },
            causation_id=causation_id,
            correlation_id=context.trace_id,
        ))
        if release_slot:
            self._release_writer_fanout_slot(
                context=context,
                child=child,
                task_item=task_item,
                role=role,
                causation_id=causation_id,
                reason=reason,
            )

    def _release_writer_fanout_slot(
        self,
        *,
        context,
        child,
        task_item: dict,
        role: RoleConfig,
        causation_id: str,
        reason: str,
    ) -> None:
        if str(task_item.get("assignment_strategy") or "") != "affinity_stage_slots":
            return
        payload = {
            "fanout_id": context.fanout_id,
            "trace_id": context.trace_id,
            "stage_id": context.stage_id,
            "child_id": child.child_id,
            "role_instance": role.instance_id,
            "task_id": str(task_item.get("task_id") or ""),
            "reason": reason,
        }
        self._copy_fanout_assignment_metadata(payload, task_item)
        self.event_writer.append(ZfEvent(
            type="fanout.slot.released",
            actor="zf-cli",
            payload=payload,
            causation_id=causation_id,
            correlation_id=context.trace_id,
        ))

    def _park_writer_fanout_queued_task(
        self,
        *,
        task_id: str,
        fanout_id: str,
        child_id: str,
    ) -> None:
        if not task_id:
            return
        task = self.task_store.get(task_id)
        if task is None or task.status != "backlog":
            return
        if not self._move_task(
            task_id,
            "blocked",
            trigger_event="fanout.child.queued",
        ):
            return
        self.task_store.update(
            task_id,
            assigned_to="",
            blocked_reason=f"fanout_queue:{fanout_id}:{child_id}",
        )

    def _park_writer_fanout_deferred_task(
        self,
        *,
        task_id: str,
        fanout_id: str,
        child_id: str,
    ) -> None:
        if not task_id:
            return
        task = self.task_store.get(task_id)
        if task is None or task.status != "backlog":
            return
        if not self._move_task(
            task_id,
            "blocked",
            trigger_event="fanout.child.dispatch_deferred",
        ):
            return
        self.task_store.update(
            task_id,
            assigned_to="",
            active_dispatch_id="",
            blocked_reason=f"fanout_dispatch_deferred:{fanout_id}:{child_id}",
        )

    def _block_writer_fanout_tasks(
        self,
        *,
        task_items: list[dict],
        reason: str,
    ) -> None:
        for task_item in task_items:
            task_id = str(task_item.get("task_id") or "")
            if not task_id:
                continue
            task = self.task_store.get(task_id)
            if task is None or task.status != "backlog":
                continue
            if not self._move_task(
                task_id,
                "blocked",
                trigger_event="fanout.cancelled",
            ):
                continue
            self.task_store.update(
                task_id,
                assigned_to="",
                blocked_reason=f"fanout_affinity_cancelled:{reason}",
            )

    def _unpark_writer_fanout_queued_task(self, task_id: str) -> None:
        if not task_id:
            return
        task = self.task_store.get(task_id)
        if task is None or task.status != "blocked":
            return
        if not str(task.blocked_reason or "").startswith("fanout_queue:"):
            return
        if not self._move_task(
            task_id,
            "backlog",
            trigger_event="fanout.slot.assigned",
        ):
            return
        self.task_store.update(task_id, assigned_to="", blocked_reason="")

    def _unpark_writer_fanout_deferred_task(self, task_id: str) -> None:
        if not task_id:
            return
        task = self.task_store.get(task_id)
        if task is None or task.status != "blocked":
            return
        if not str(task.blocked_reason or "").startswith("fanout_dispatch_deferred:"):
            return
        if self._move_task(
            task_id,
            "backlog",
            trigger_event="fanout.child.dispatch_retry",
        ):
            self.task_store.update(task_id, assigned_to="", blocked_reason="")

    def _claim_writer_fanout_task(
        self,
        task_id: str,
        role_instance: str,
        *,
        run_id: str = "",
    ) -> bool:
        """Reserve a seeded kanban task for a writer fanout child."""
        if not task_id:
            return False
        task = self.task_store.get(task_id)
        if task is None:
            return False
        if task.status == "backlog":
            if not self._move_task(
                task_id,
                "in_progress",
                trigger_event="fanout.child.dispatched",
            ):
                return False
        latest = self.task_store.get(task_id)
        if latest is None or latest.status != "in_progress":
            return False
        active_dispatch_id = str(latest.active_dispatch_id or "")
        if (
            run_id
            and active_dispatch_id
            and active_dispatch_id != run_id
            and self._task_dispatch_id_is_active(task_id, active_dispatch_id)
        ):
            return False
        updates = {"assigned_to": role_instance}
        if run_id:
            updates["active_dispatch_id"] = run_id
        self.task_store.update(task_id, **updates)
        return True
