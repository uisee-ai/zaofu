"""Deterministic executor for structured repair.action.requested intents."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from zf.core.config.schema import RoleConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.integration_queue import (
    STATUS_DISCARDED,
    STATUS_INTEGRATED,
    STATUS_NEEDS_REVIEW,
    build_integration_queue,
)
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.repair_actions import build_repair_action_projection


@dataclass
class RepairActionExecutor:
    event_log: EventLog
    task_store: TaskStore
    event_writer: EventWriter
    roles: Sequence[RoleConfig]
    respawn_worker: Callable[[RoleConfig], OrchestratorDecision]
    cancel_worker: Callable[[RoleConfig], OrchestratorDecision] | None = None
    rerun_fanout_child: Callable[[str, str], OrchestratorDecision] | None = None

    def apply(self, event: ZfEvent) -> None:
        """Apply or reject one repair.action.requested event."""
        if event.type != "repair.action.requested":
            return
        record = self._record_for_event(event)
        if record is None:
            self._reject(
                event,
                action_id="",
                kind="",
                task_id=event.task_id or "",
                reason="missing_repair_action_record",
            )
            return

        action_id = str(record.get("id") or "")
        kind = str(record.get("kind") or "")
        task_id = str(record.get("task_id") or event.task_id or "")
        status = str(record.get("status") or "")
        reason = str(record.get("reason") or "")
        if status in {"applied", "rejected"}:
            return
        if status in {"invalid", "duplicate"}:
            self._reject(
                event,
                action_id=action_id,
                kind=kind,
                task_id=task_id,
                reason=reason or status,
            )
            return

        if kind == "requeue_task":
            self._execute_requeue_task(event, record)
        elif kind == "reemit_trigger":
            self._execute_reemit_trigger(event, record)
        elif kind == "restart_worker":
            self._execute_restart_worker(event, record)
        elif kind == "cancel_worker":
            self._execute_cancel_worker(event, record)
        elif kind == "rerun_fanout_child":
            self._execute_rerun_fanout_child(event, record)
        elif kind == "mark_stale_projection_for_rebuild":
            self._execute_mark_stale_projection_for_rebuild(event, record)
        elif kind == "retry_integration_queue_entry":
            self._execute_retry_integration_queue_entry(event, record)
        elif kind == "discard_integration_queue_entry":
            self._execute_discard_integration_queue_entry(event, record)
        else:
            self._reject(
                event,
                action_id=action_id,
                kind=kind,
                task_id=task_id,
                reason=f"unsupported_action_kind:{kind}",
            )

    def _record_for_event(self, event: ZfEvent) -> dict[str, object] | None:
        try:
            events = self.event_log.read_all()
        except Exception:
            events = [event]
        if not any(candidate.id == event.id for candidate in events):
            events = [*events, event]
        try:
            valid_task_ids = {
                task.id for task in self.task_store.list_all_with_archive()
            }
        except Exception:
            valid_task_ids = None
        projection = build_repair_action_projection(
            events,
            valid_task_ids=valid_task_ids,
        )
        for record in projection.get("actions", []) or []:
            if not isinstance(record, dict):
                continue
            if str(record.get("requested_event_id") or "") == event.id:
                return record
        return None

    def _execute_requeue_task(
        self,
        event: ZfEvent,
        record: dict[str, object],
    ) -> None:
        action_id = str(record.get("id") or "")
        task_id = str(record.get("task_id") or event.task_id or "")
        if not task_id:
            self._reject(
                event,
                action_id=action_id,
                kind="requeue_task",
                task_id="",
                reason="missing_task_id",
            )
            return
        task = self.task_store.get(task_id)
        if task is None:
            self._reject(
                event,
                action_id=action_id,
                kind="requeue_task",
                task_id=task_id,
                reason=f"unknown_task:{task_id}",
            )
            return
        if task.status in {"done", "cancelled"}:
            self._reject(
                event,
                action_id=action_id,
                kind="requeue_task",
                task_id=task_id,
                reason=f"task_not_active:{task.status}",
            )
            return
        updated = self.task_store.update(
            task_id,
            status="backlog",
            assigned_to="",
            active_dispatch_id="",
        )
        if updated is None:
            self._reject(
                event,
                action_id=action_id,
                kind="requeue_task",
                task_id=task_id,
                reason="task_update_failed",
            )
            return
        try:
            self.event_writer.append(ZfEvent(
                type="task.requeued",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "source": "repair_action",
                    "action_id": action_id,
                    "reason": str(record.get("reason") or ""),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
        self._terminal(
            event,
            applied=True,
            action_id=action_id,
            kind="requeue_task",
            task_id=task_id,
            reason="task requeued",
        )

    def _execute_reemit_trigger(
        self,
        event: ZfEvent,
        record: dict[str, object],
    ) -> None:
        action_id = str(record.get("id") or "")
        task_id = str(record.get("task_id") or event.task_id or "")
        request_payload = event.payload if isinstance(event.payload, dict) else {}
        source_event_id = str(
            request_payload.get("source_event_id")
            or request_payload.get("trigger_event_id")
            or "",
        )
        if not source_event_id:
            self._reject(
                event,
                action_id=action_id,
                kind="reemit_trigger",
                task_id=task_id,
                reason="missing_source_event_id",
            )
            return
        source_event = self._find_event(source_event_id)
        if source_event is None:
            self._reject(
                event,
                action_id=action_id,
                kind="reemit_trigger",
                task_id=task_id,
                reason=f"unknown_source_event:{source_event_id}",
            )
            return
        if source_event.type.startswith("repair.action."):
            self._reject(
                event,
                action_id=action_id,
                kind="reemit_trigger",
                task_id=task_id,
                reason=f"source_event_not_reemittable:{source_event.type}",
            )
            return
        source_task_id = str(source_event.task_id or "")
        if task_id and source_task_id and task_id != source_task_id:
            self._reject(
                event,
                action_id=action_id,
                kind="reemit_trigger",
                task_id=task_id,
                reason=f"source_event_task_mismatch:{source_task_id}",
            )
            return
        reemit_payload = (
            dict(source_event.payload)
            if isinstance(source_event.payload, dict)
            else {}
        )
        reemit_payload.update({
            "source": "repair_action_reemit_trigger",
            "repair_action_id": action_id,
            "reemit_source_event_id": source_event.id,
            "reemit_request_event_id": event.id,
        })
        self.event_writer.append(ZfEvent(
            type=source_event.type,
            actor="zf-cli",
            task_id=task_id or source_event.task_id,
            payload=reemit_payload,
            causation_id=event.id,
            correlation_id=event.correlation_id or source_event.correlation_id,
        ))
        self._terminal(
            event,
            applied=True,
            action_id=action_id,
            kind="reemit_trigger",
            task_id=task_id or source_task_id,
            reason="trigger re-emitted",
            extra_payload={
                "source_event_id": source_event.id,
                "reemitted_event_type": source_event.type,
            },
        )

    def _execute_cancel_worker(
        self,
        event: ZfEvent,
        record: dict[str, object],
    ) -> None:
        action_id = str(record.get("id") or "")
        target = str(record.get("worker_id") or record.get("role") or "")
        role = self._find_role(target)
        if role is None:
            self._reject(
                event,
                action_id=action_id,
                kind="cancel_worker",
                task_id=str(record.get("task_id") or event.task_id or ""),
                reason=f"unknown_worker:{target or '(missing)'}",
            )
            return
        if self.cancel_worker is None:
            self._reject(
                event,
                action_id=action_id,
                kind="cancel_worker",
                task_id=str(record.get("task_id") or event.task_id or ""),
                reason="cancel_worker_unavailable",
            )
            return
        decision = self.cancel_worker(role)
        if decision.action == "cancel":
            self._terminal(
                event,
                applied=True,
                action_id=action_id,
                kind="cancel_worker",
                task_id=str(record.get("task_id") or event.task_id or ""),
                reason=decision.reason,
                extra_payload={
                    "worker_id": role.instance_id,
                    "decision_action": decision.action,
                },
            )
            return
        self._reject(
            event,
            action_id=action_id,
            kind="cancel_worker",
            task_id=str(record.get("task_id") or event.task_id or ""),
            reason=decision.reason,
            extra_payload={
                "worker_id": role.instance_id,
                "decision_action": decision.action,
            },
        )

    def _execute_rerun_fanout_child(
        self,
        event: ZfEvent,
        record: dict[str, object],
    ) -> None:
        action_id = str(record.get("id") or "")
        task_id = str(record.get("task_id") or event.task_id or "")
        fanout_id = str(record.get("fanout_id") or "")
        child_id = str(record.get("fanout_child_id") or "")
        if not fanout_id:
            self._reject(
                event,
                action_id=action_id,
                kind="rerun_fanout_child",
                task_id=task_id,
                reason="missing_fanout_id",
            )
            return
        if not child_id:
            self._reject(
                event,
                action_id=action_id,
                kind="rerun_fanout_child",
                task_id=task_id,
                reason="missing_fanout_child_id",
            )
            return
        if self.rerun_fanout_child is None:
            self._reject(
                event,
                action_id=action_id,
                kind="rerun_fanout_child",
                task_id=task_id,
                reason="rerun_fanout_child_unavailable",
            )
            return
        decision = self.rerun_fanout_child(fanout_id, child_id)
        if decision.action == "rerun_fanout_child":
            self._terminal(
                event,
                applied=True,
                action_id=action_id,
                kind="rerun_fanout_child",
                task_id=task_id or decision.task_id,
                reason=decision.reason,
                extra_payload={
                    "fanout_id": fanout_id,
                    "fanout_child_id": child_id,
                    "worker_id": decision.role,
                    "decision_action": decision.action,
                },
            )
            return
        self._reject(
            event,
            action_id=action_id,
            kind="rerun_fanout_child",
            task_id=task_id or decision.task_id,
            reason=decision.reason,
            extra_payload={
                "fanout_id": fanout_id,
                "fanout_child_id": child_id,
                "worker_id": decision.role,
                "decision_action": decision.action,
            },
        )

    def _execute_mark_stale_projection_for_rebuild(
        self,
        event: ZfEvent,
        record: dict[str, object],
    ) -> None:
        action_id = str(record.get("id") or "")
        task_id = str(record.get("task_id") or event.task_id or "")
        projection = str(record.get("projection") or "")
        if not projection:
            self._reject(
                event,
                action_id=action_id,
                kind="mark_stale_projection_for_rebuild",
                task_id=task_id,
                reason="missing_projection",
            )
            return
        rebuild_event = self.event_writer.append(ZfEvent(
            type="projection.rebuild.requested",
            actor="zf-cli",
            task_id=task_id or None,
            payload={
                "source": "repair_action",
                "action_id": action_id,
                "projection": projection,
                "reason": str(record.get("reason") or ""),
                "evidence_refs": list(record.get("evidence_refs") or []),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        self._terminal(
            event,
            applied=True,
            action_id=action_id,
            kind="mark_stale_projection_for_rebuild",
            task_id=task_id,
            reason="projection rebuild requested",
            extra_payload={
                "projection": projection,
                "rebuild_event_id": rebuild_event.id,
            },
        )

    def _execute_retry_integration_queue_entry(
        self,
        event: ZfEvent,
        record: dict[str, object],
    ) -> None:
        action_id = str(record.get("id") or "")
        task_id = str(record.get("task_id") or event.task_id or "")
        queue_entry_id = str(record.get("queue_entry_id") or "")
        entry = self._integration_queue_entry(queue_entry_id)
        if entry is None:
            self._reject(
                event,
                action_id=action_id,
                kind="retry_integration_queue_entry",
                task_id=task_id,
                reason=f"unknown_queue_entry:{queue_entry_id or '(missing)'}",
            )
            return
        entry_task_id = str(entry.get("task_id") or "")
        if task_id and entry_task_id and task_id != entry_task_id:
            self._reject(
                event,
                action_id=action_id,
                kind="retry_integration_queue_entry",
                task_id=task_id,
                reason=f"integration_queue_task_mismatch:{entry_task_id}",
                extra_payload={"queue_entry_id": queue_entry_id},
            )
            return
        status = str(entry.get("status") or "")
        if status != STATUS_NEEDS_REVIEW:
            self._reject(
                event,
                action_id=action_id,
                kind="retry_integration_queue_entry",
                task_id=task_id or entry_task_id,
                reason=f"integration_queue_entry_not_retryable:{status or '(missing)'}",
                extra_payload={"queue_entry_id": queue_entry_id},
            )
            return
        request_payload = event.payload if isinstance(event.payload, dict) else {}
        target_status = str(request_payload.get("target_status") or "queued").strip()
        if target_status not in {"queued", "integrating"}:
            self._reject(
                event,
                action_id=action_id,
                kind="retry_integration_queue_entry",
                task_id=task_id or entry_task_id,
                reason=f"invalid_retry_target_status:{target_status}",
                extra_payload={"queue_entry_id": queue_entry_id},
            )
            return
        queue_event = self.event_writer.append(ZfEvent(
            type="integration.queue.retry_requested",
            actor="zf-cli",
            task_id=task_id or entry_task_id or None,
            payload={
                "source": "repair_action",
                "action_id": action_id,
                "queue_entry_id": queue_entry_id,
                "target_status": target_status,
                "idempotency_key": str(record.get("idempotency_key") or ""),
                "reason": str(record.get("reason") or ""),
                "evidence_refs": list(record.get("evidence_refs") or []),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        self._terminal(
            event,
            applied=True,
            action_id=action_id,
            kind="retry_integration_queue_entry",
            task_id=task_id or entry_task_id,
            reason="integration queue retry requested",
            extra_payload={
                "queue_entry_id": queue_entry_id,
                "queue_status": status,
                "target_status": target_status,
                "queue_event_id": queue_event.id,
            },
        )

    def _execute_discard_integration_queue_entry(
        self,
        event: ZfEvent,
        record: dict[str, object],
    ) -> None:
        action_id = str(record.get("id") or "")
        task_id = str(record.get("task_id") or event.task_id or "")
        queue_entry_id = str(record.get("queue_entry_id") or "")
        entry = self._integration_queue_entry(queue_entry_id)
        if entry is None:
            self._reject(
                event,
                action_id=action_id,
                kind="discard_integration_queue_entry",
                task_id=task_id,
                reason=f"unknown_queue_entry:{queue_entry_id or '(missing)'}",
            )
            return
        entry_task_id = str(entry.get("task_id") or "")
        if task_id and entry_task_id and task_id != entry_task_id:
            self._reject(
                event,
                action_id=action_id,
                kind="discard_integration_queue_entry",
                task_id=task_id,
                reason=f"integration_queue_task_mismatch:{entry_task_id}",
                extra_payload={"queue_entry_id": queue_entry_id},
            )
            return
        status = str(entry.get("status") or "")
        if status in {STATUS_INTEGRATED, STATUS_DISCARDED}:
            self._reject(
                event,
                action_id=action_id,
                kind="discard_integration_queue_entry",
                task_id=task_id or entry_task_id,
                reason=f"integration_queue_entry_terminal:{status}",
                extra_payload={"queue_entry_id": queue_entry_id},
            )
            return
        queue_event = self.event_writer.append(ZfEvent(
            type="integration.queue.discarded",
            actor="zf-cli",
            task_id=task_id or entry_task_id or None,
            payload={
                "source": "repair_action",
                "action_id": action_id,
                "queue_entry_id": queue_entry_id,
                "idempotency_key": str(record.get("idempotency_key") or ""),
                "reason": str(record.get("reason") or ""),
                "evidence_refs": list(record.get("evidence_refs") or []),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        self._terminal(
            event,
            applied=True,
            action_id=action_id,
            kind="discard_integration_queue_entry",
            task_id=task_id or entry_task_id,
            reason="integration queue discard requested",
            extra_payload={
                "queue_entry_id": queue_entry_id,
                "queue_status": status,
                "queue_event_id": queue_event.id,
            },
        )

    def _execute_restart_worker(
        self,
        event: ZfEvent,
        record: dict[str, object],
    ) -> None:
        action_id = str(record.get("id") or "")
        target = str(record.get("worker_id") or record.get("role") or "")
        role = self._find_role(target)
        if role is None:
            self._reject(
                event,
                action_id=action_id,
                kind="restart_worker",
                task_id=str(record.get("task_id") or event.task_id or ""),
                reason=f"unknown_worker:{target or '(missing)'}",
            )
            return
        decision = self.respawn_worker(role)
        if decision.action == "respawn":
            self._terminal(
                event,
                applied=True,
                action_id=action_id,
                kind="restart_worker",
                task_id=str(record.get("task_id") or event.task_id or ""),
                reason=decision.reason,
                extra_payload={
                    "worker_id": role.instance_id,
                    "decision_action": decision.action,
                },
            )
            return
        self._reject(
            event,
            action_id=action_id,
            kind="restart_worker",
            task_id=str(record.get("task_id") or event.task_id or ""),
            reason=decision.reason,
            extra_payload={
                "worker_id": role.instance_id,
                "decision_action": decision.action,
            },
        )

    def _find_role(self, target: str) -> RoleConfig | None:
        for role in self.roles:
            if role.instance_id == target or role.name == target:
                return role
        return None

    def _find_event(self, event_id: str) -> ZfEvent | None:
        if not event_id:
            return None
        try:
            events = self.event_log.read_all()
        except Exception:
            events = []
        for event in reversed(events):
            if event.id == event_id:
                return event
        return None

    def _integration_queue_entry(self, queue_entry_id: str) -> dict[str, Any] | None:
        if not queue_entry_id:
            return None
        try:
            projection = build_integration_queue(self.event_log.read_all())
        except Exception:
            return None
        for entry in projection.get("entries", []) or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("id") or "") == queue_entry_id:
                return entry
        return None

    def _reject(
        self,
        event: ZfEvent,
        *,
        action_id: str,
        kind: str,
        task_id: str,
        reason: str,
        extra_payload: dict[str, object] | None = None,
    ) -> None:
        self._terminal(
            event,
            applied=False,
            action_id=action_id,
            kind=kind,
            task_id=task_id,
            reason=reason,
            extra_payload=extra_payload,
        )

    def _terminal(
        self,
        event: ZfEvent,
        *,
        applied: bool,
        action_id: str,
        kind: str,
        task_id: str,
        reason: str,
        extra_payload: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "action_id": action_id,
            "kind": kind,
            "reason": reason,
            "source": "repair_action_executor",
        }
        if extra_payload:
            payload.update(extra_payload)
        try:
            self.event_writer.append(ZfEvent(
                type=(
                    "repair.action.applied"
                    if applied
                    else "repair.action.rejected"
                ),
                actor="zf-cli",
                task_id=task_id or None,
                payload=payload,
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
        except Exception:
            pass
