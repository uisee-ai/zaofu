"""Attempt envelope for deterministic controlled actions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ControlledActionAttempt:
    attempt_id: str
    started_at: str
    event_id: str
    operation_id: str = ""
    request_hash: str = ""


class ControlledActionOrchestrator:
    """Wrap a deterministic action handler with attempt/result events.

    The action handler remains the authority for business state changes. This
    wrapper records execution boundaries and adds a stable result envelope for
    Web/API callers and replay tooling.
    """

    def __init__(
        self,
        *,
        writer: EventWriter,
        actor: str,
        surface: str,
    ) -> None:
        self.writer = writer
        self.actor = actor
        self.surface = surface

    def run(
        self,
        *,
        action: str,
        requested_action: str,
        payload: dict[str, Any],
        requested: ZfEvent,
        task_id: str | None,
        handler: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        attempt = self._started(
            action=action,
            requested_action=requested_action,
            payload=payload,
            requested=requested,
            task_id=task_id,
        )
        try:
            result = handler()
        except Exception as exc:
            self._failed(
                attempt=attempt,
                action=action,
                requested_action=requested_action,
                requested=requested,
                task_id=task_id,
                reason=str(exc),
                status="exception",
            )
            raise

        status = str(result.get("status") or ("completed" if result.get("ok") else "failed"))
        ok = bool(result.get("ok"))
        event = self._completed if ok else self._failed
        event(
            attempt=attempt,
            action=action,
            requested_action=requested_action,
            requested=requested,
            task_id=task_id,
            status=status,
            reason=str(result.get("reason") or result.get("error") or ""),
            result=result,
        )
        return self._with_envelope(
            result,
            action=action,
            requested_action=requested_action,
            attempt=attempt,
            status=status,
        )

    def _started(
        self,
        *,
        action: str,
        requested_action: str,
        payload: dict[str, Any],
        requested: ZfEvent,
        task_id: str | None,
    ) -> ControlledActionAttempt:
        started_at = _now()
        attempt_id = f"act-{requested.id[:12]}"
        event = self.writer.emit(
            "runtime.action.attempt.started",
            actor=self.actor,
            task_id=task_id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload=redact_obj({
                "attempt_id": attempt_id,
                "action": action,
                "requested_action": requested_action,
                "surface": self.surface,
                "started_at": started_at,
                "operation_id": str(payload.get("operation_id") or ""),
                "request_hash": str(payload.get("request_hash") or ""),
                "payload": payload,
            }),
        )
        return ControlledActionAttempt(
            attempt_id=attempt_id,
            started_at=started_at,
            event_id=event.id,
            operation_id=str(payload.get("operation_id") or ""),
            request_hash=str(payload.get("request_hash") or ""),
        )

    def _completed(
        self,
        *,
        attempt: ControlledActionAttempt,
        action: str,
        requested_action: str,
        requested: ZfEvent,
        task_id: str | None,
        status: str,
        reason: str = "",
        result: dict[str, Any] | None = None,
    ) -> None:
        self.writer.emit(
            "runtime.action.attempt.completed",
            actor=self.actor,
            task_id=task_id,
            causation_id=attempt.event_id,
            correlation_id=requested.correlation_id,
            payload=redact_obj({
                "attempt_id": attempt.attempt_id,
                "action": action,
                "requested_action": requested_action,
                "surface": self.surface,
                "operation_id": attempt.operation_id,
                "request_hash": attempt.request_hash,
                "status": status,
                "reason": reason,
                "started_at": attempt.started_at,
                "completed_at": _now(),
                "result_event_id": str((result or {}).get("event_id") or ""),
                "reply_event_id": str((result or {}).get("reply_event_id") or ""),
            }),
        )

    def _failed(
        self,
        *,
        attempt: ControlledActionAttempt,
        action: str,
        requested_action: str,
        requested: ZfEvent,
        task_id: str | None,
        reason: str = "",
        status: str = "failed",
        result: dict[str, Any] | None = None,
    ) -> None:
        self.writer.emit(
            "runtime.action.attempt.failed",
            actor=self.actor,
            task_id=task_id,
            causation_id=attempt.event_id,
            correlation_id=requested.correlation_id,
            payload=redact_obj({
                "attempt_id": attempt.attempt_id,
                "action": action,
                "requested_action": requested_action,
                "surface": self.surface,
                "operation_id": attempt.operation_id,
                "request_hash": attempt.request_hash,
                "status": status,
                "reason": reason,
                "started_at": attempt.started_at,
                "failed_at": _now(),
                "result_event_id": str((result or {}).get("event_id") or ""),
                "reply_event_id": str((result or {}).get("reply_event_id") or ""),
            }),
        )

    @staticmethod
    def _with_envelope(
        result: dict[str, Any],
        *,
        action: str,
        requested_action: str,
        attempt: ControlledActionAttempt,
        status: str,
    ) -> dict[str, Any]:
        envelope = {
            "schema_version": "controlled-action-result.v1",
            "attempt_id": attempt.attempt_id,
            "action": action,
            "requested_action": requested_action,
            "status": status,
            "operation_id": attempt.operation_id,
            "request_hash": attempt.request_hash,
            "ok": bool(result.get("ok")),
            "event_id": str(result.get("event_id") or ""),
            "reply_event_id": str(result.get("reply_event_id") or ""),
            "reason": str(result.get("reason") or result.get("error") or ""),
        }
        return {
            **result,
            "action_result": envelope,
        }


__all__ = ["ControlledActionOrchestrator"]
