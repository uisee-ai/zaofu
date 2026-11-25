"""Controlled Project Run pause, resume, and cancellation."""

from __future__ import annotations

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.run_admission import (
    RUN_ADMISSION_SCHEMA_VERSION,
    build_run_admission_projection,
)


class RunControlActionsMixin:
    def _run_control_action(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        with locked_path(self.state_dir / "locks" / "run-admission"):
            return self._run_control_action_locked(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload=payload,
            )

    def _run_control_action_locked(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        run_id = str(
            payload.get("run_id")
            or payload.get("workflow_run_id")
            or ""
        ).strip()
        events = self.writer.event_log.read_all()
        projection = build_run_admission_projection(events)
        entry = projection.runs.get(run_id)
        if entry is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=f"Run not found: {run_id}",
                status_code=404,
                status="not_found",
            )
        reason = str(payload.get("reason") or action).strip()
        request_id = str(payload.get("request_id") or entry.request_id or run_id)
        event_type = {
            "run-pause": "run.paused",
            "run-resume": "run.resumed",
            "run-cancel": "run.cancelled",
        }[action]

        if action == "run-pause":
            if entry.status == "paused":
                return self._idempotent_run_result(
                    action=action,
                    requested_action=requested_action,
                    run_id=run_id,
                    request_id=request_id,
                    status="paused",
                    event_id=_latest_event_id(events, event_type, run_id),
                )
            if entry.status != "running":
                return self._invalid_run_transition(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    entry_status=entry.status,
                    run_id=run_id,
                )
        elif action == "run-resume":
            if entry.status == "running":
                return self._idempotent_run_result(
                    action=action,
                    requested_action=requested_action,
                    run_id=run_id,
                    request_id=request_id,
                    status="running",
                    event_id=_latest_event_id(events, event_type, run_id),
                )
            if entry.status != "paused":
                return self._invalid_run_transition(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    entry_status=entry.status,
                    run_id=run_id,
                )
        else:
            if entry.status == "cancelled":
                return self._idempotent_run_result(
                    action=action,
                    requested_action=requested_action,
                    run_id=run_id,
                    request_id=request_id,
                    status="cancelled",
                    event_id=entry.terminal_event_id,
                )
            if entry.terminal:
                return self._invalid_run_transition(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    entry_status=entry.status,
                    run_id=run_id,
                )

        event_payload = {
            "schema_version": RUN_ADMISSION_SCHEMA_VERSION,
            "run_id": run_id,
            "workflow_run_id": run_id,
            "request_id": request_id,
            "reason": reason,
            "source_event_id": requested.id,
        }
        if action == "run-resume":
            event_payload["paused_event_id"] = _latest_event_id(
                events,
                "run.paused",
                run_id,
            )
        event = self.writer.append(ZfEvent(
            type=event_type,
            origin="kernel" if action == "run-cancel" else "",
            actor=self.actor,
            task_id=entry.task_id or None,
            payload=event_payload,
            causation_id=requested.id,
            correlation_id=run_id,
        ))
        status = {
            "run-pause": "paused",
            "run-resume": "running",
            "run-cancel": "cancelled",
        }[action]
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status=status,
            task_id=entry.task_id or None,
            extra={
                "run_id": run_id,
                "request_id": request_id,
            },
        )
        return {
            "_status_code": 200,
            "ok": True,
            "status": status,
            "action": action,
            "requested_action": requested_action,
            "run_id": run_id,
            "request_id": request_id,
            "event_id": event.id,
            "idempotent_replay": False,
        }

    def _invalid_run_transition(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        entry_status: str,
        run_id: str,
    ) -> dict:
        return self._failed(
            requested=requested,
            action=action,
            requested_action=requested_action,
            task_id=None,
            reason=f"invalid Run transition: {entry_status} -> {action}",
            status_code=409,
            status="invalid_transition",
        )

    @staticmethod
    def _idempotent_run_result(
        *,
        action: str,
        requested_action: str,
        run_id: str,
        request_id: str,
        status: str,
        event_id: str,
    ) -> dict:
        return {
            "_status_code": 200,
            "ok": True,
            "status": status,
            "action": action,
            "requested_action": requested_action,
            "run_id": run_id,
            "request_id": request_id,
            "event_id": event_id,
            "idempotent_replay": True,
        }


def _latest_event_id(
    events: list[ZfEvent],
    event_type: str,
    run_id: str,
) -> str:
    for event in reversed(events):
        if event.type != event_type:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        candidate = str(
            payload.get("workflow_run_id")
            or payload.get("run_id")
            or event.correlation_id
            or ""
        )
        if candidate == run_id:
            return event.id
    return ""


__all__ = ["RunControlActionsMixin"]
