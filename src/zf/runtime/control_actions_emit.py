"""ActionEmitMixin — controlled-action handlers (moved verbatim from control_actions.py)."""
from __future__ import annotations

from zf.core.events import ZfEvent


class ActionEmitMixin:
    def _completed(
        self,
        *,
        requested: ZfEvent,
        event: ZfEvent,
        action: str,
        requested_action: str,
        status: str,
        task_id: str | None,
        extra: dict | None = None,
    ) -> None:
        payload = {
            "action": action,
            "requested_action": requested_action,
            "status": status,
            **(extra or {}),
        }
        self.writer.emit(
            "runtime.action.completed",
            actor=self.actor,
            task_id=task_id,
            causation_id=event.id,
            correlation_id=event.correlation_id or requested.correlation_id,
            payload=payload,
        )
        self.writer.emit(
            f"{self.surface}.action.completed",
            actor=self.actor,
            task_id=task_id,
            causation_id=event.id,
            correlation_id=event.correlation_id or requested.correlation_id,
            payload=payload,
        )
    def _failed(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        task_id: str | None,
        reason: str,
        status_code: int = 422,
        status: str = "failed",
    ) -> dict:
        payload = {
            "action": action,
            "requested_action": requested_action,
            "reason": reason,
        }
        event = self.writer.emit(
            "runtime.action.failed",
            actor=self.actor,
            task_id=task_id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload=payload,
        )
        self.writer.emit(
            f"{self.surface}.action.failed",
            actor=self.actor,
            task_id=task_id,
            causation_id=event.id,
            correlation_id=event.correlation_id,
            payload=payload,
        )
        return {
            "_status_code": status_code,
            "ok": False,
            "status": status,
            "action": action,
            "requested_action": requested_action,
            "reason": reason,
            "event_id": event.id,
        }
