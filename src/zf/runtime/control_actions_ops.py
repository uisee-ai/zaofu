"""OpsActionsMixin — controlled-action handlers (moved verbatim from control_actions.py)."""
from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Any
from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.automation_projection import AUTOMATIONS
from zf.runtime.automation_projection import project_automations
from zf.runtime.control_actions_helpers import _approval_ref
from zf.runtime.control_actions_helpers import _automation_output_summary
from zf.runtime.control_actions_helpers import _compact_automation_outputs
from zf.runtime.control_actions_helpers import _dedupe_ids
from zf.runtime.control_actions_helpers import _proposal_id
from zf.runtime.control_actions_helpers import _required_text
from zf.runtime.control_actions_helpers import _runtime_impact_summary
from zf.runtime.control_actions_helpers import _task_id_from_payload
import hashlib


class OpsActionsMixin:
    def _automation_run(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        automation_id = (
            _required_text(payload, "automation_id")
            or _required_text(payload, "id")
        )
        if automation_id not in AUTOMATIONS:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="automation_id must be one of " + ", ".join(AUTOMATIONS),
                status_code=422,
                status="invalid_payload",
            )
        trigger = _required_text(payload, "trigger") or "manual"
        allowed_triggers = {"manual", "schedule", "event-window", "webhook"}
        if trigger not in allowed_triggers:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="trigger must be one of " + ", ".join(sorted(allowed_triggers)),
                status_code=422,
                status="invalid_payload",
            )

        project_name = str(getattr(getattr(self.config, "project", None), "name", "") or "")
        project_id = _required_text(payload, "project_id") or project_name or "default"
        source = _required_text(payload, "source") or self.source or self.surface
        window = {
            "daily-brief": "1d",
            "weekly-review": "7d",
            "project-monitor": "14d",
        }[automation_id]
        started_at = datetime.now(timezone.utc)
        run_id = _required_text(payload, "run_id")
        if not run_id:
            stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
            digest = hashlib.sha1(
                f"{requested.id}:{automation_id}:{project_id}".encode("utf-8"),
            ).hexdigest()[:8]
            run_id = f"{automation_id}-{stamp}-{digest}"

        base_payload = {
            "automation_id": automation_id,
            "project_id": project_id,
            "source": source,
            "run_id": run_id,
            "trigger": trigger,
            "window": window,
        }
        started = self.writer.emit(
            "automation.run.started",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                **base_payload,
                "requested_action": requested_action,
            },
        )
        try:
            projection = project_automations(
                self.state_dir,
                project_id=project_id,
                project_name=project_name or project_id,
            )
            selected = next(
                item for item in projection.get("items", [])
                if item.get("automation_id") == automation_id
            )
            outputs = selected.get("outputs") if isinstance(selected, dict) else []
            outputs_list = outputs if isinstance(outputs, list) else []
            compact_outputs = _compact_automation_outputs(outputs_list)
            proposals = selected.get("proposals") if isinstance(selected, dict) else []
            proposals_list = proposals if isinstance(proposals, list) else []
            source_events = [
                str(ref.get("event_id") or "")
                for ref in (selected.get("source_events") or [])
                if isinstance(ref, dict) and str(ref.get("event_id") or "")
            ]
        except Exception as exc:
            failed = self.writer.emit(
                "automation.run.failed",
                actor=self.actor,
                causation_id=started.id,
                correlation_id=started.correlation_id or requested.correlation_id,
                payload={
                    **base_payload,
                    "reason": str(exc),
                    "source_events": [started.id],
                },
            )
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=str(exc),
                status_code=500,
                status="failed",
            ) | {"automation_event_id": failed.id, "run_id": run_id}

        duration_seconds = round((datetime.now(timezone.utc) - started_at).total_seconds(), 6)
        completed = self.writer.emit(
            "automation.run.completed",
            actor=self.actor,
            causation_id=started.id,
            correlation_id=started.correlation_id or requested.correlation_id,
            payload={
                **base_payload,
                "status": "completed",
                "outputs": compact_outputs,
                "source_events": _dedupe_ids([started.id, *source_events]),
                "duration_seconds": duration_seconds,
                "summary": _automation_output_summary(outputs_list),
                "proposal_count": len(proposals_list),
            },
        )
        self._completed(
            requested=requested,
            event=completed,
            action=action,
            requested_action=requested_action,
            status="completed",
            task_id=None,
            extra={
                "automation_id": automation_id,
                "project_id": project_id,
                "run_id": run_id,
                "automation_event_id": completed.id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "completed",
            "action": action,
            "requested_action": requested_action,
            "automation_id": automation_id,
            "project_id": project_id,
            "run_id": run_id,
            "event_id": completed.id,
            "started_event_id": started.id,
            "outputs": compact_outputs,
            "proposal_count": len(proposals_list),
        }
    def _maintenance_prepare(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        from zf.runtime.maintenance import create_checkpoint, enter_maintenance

        trigger_id = (
            _required_text(payload, "trigger_id")
            or _required_text(payload, "trigger")
            or _required_text(payload, "proposal_id")
        )
        if not trigger_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="trigger_id is required",
                status_code=422,
                status="invalid_payload",
            )
        task_id = _task_id_from_payload(payload)
        wants_checkpoint = bool(
            payload.get("checkpoint")
            or payload.get("create_checkpoint")
            or payload.get("checkpoint_required")
        )
        if wants_checkpoint and not task_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="task_id is required when checkpoint is requested",
                status_code=422,
                status="invalid_payload",
            )

        reason = (
            _required_text(payload, "reason")
            or "supervisor requested maintenance preparation"
        )
        try:
            current_path = enter_maintenance(
                self.state_dir,
                trigger_id=trigger_id,
                reason=reason,
            )
            checkpoint_payload: dict[str, Any] = {}
            if task_id and (
                wants_checkpoint
                or payload.get("assigned_worker")
                or payload.get("worker")
            ):
                checkpoint = create_checkpoint(
                    self.state_dir,
                    project_root=self.project_root,
                    task_id=task_id,
                    role=_required_text(payload, "role"),
                    assigned_worker=(
                        _required_text(payload, "assigned_worker")
                        or _required_text(payload, "worker")
                        or _required_text(payload, "instance_id")
                    ),
                    session_id=_required_text(payload, "session_id"),
                    tmux_session=_required_text(payload, "tmux_session"),
                    pane_id=_required_text(payload, "pane_id"),
                    last_progress=_required_text(payload, "last_progress"),
                    current_stage=_required_text(payload, "current_stage"),
                    transcript_path=_required_text(payload, "transcript_path"),
                )
                checkpoint_payload = {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "checkpoint_path": checkpoint.resume_packet_path,
                }
        except Exception as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=str(exc),
                status_code=500,
                status="failed",
            )

        extra = {
            "trigger_id": trigger_id,
            "maintenance_current": str(current_path),
            "dispatch_paused": True,
            **checkpoint_payload,
        }
        self._completed(
            requested=requested,
            event=requested,
            action=action,
            requested_action=requested_action,
            status="prepared",
            task_id=task_id,
            extra=extra,
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "prepared",
            "action": action,
            "requested_action": requested_action,
            "trigger_id": trigger_id,
            "task_id": task_id,
            "maintenance_current": str(current_path),
            **checkpoint_payload,
        }
    def _attention_lifecycle(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        attention_id = _required_text(payload, "attention_id")
        fingerprint = _required_text(payload, "fingerprint")
        if not attention_id and not fingerprint:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="attention_id or fingerprint is required",
                status_code=422,
                status="invalid_payload",
            )
        event_type = {
            "attention-ack": "runtime.attention.acknowledged",
            "attention-snooze": "runtime.attention.snoozed",
            "attention-resolve": "runtime.attention.resolved",
            "attention-feedback": "runtime.attention.feedback.recorded",
            "attention-escalate": "runtime.attention.escalated",
        }[action]
        task_id = _task_id_from_payload(payload)
        reason = _required_text(payload, "reason")
        event_payload = {
            "schema_version": event_type + ".v0",
            "attention_id": attention_id,
            "fingerprint": fingerprint,
            "reason": reason,
            "source": _required_text(payload, "source") or self.source,
            "surface": self.surface,
            "source_event_id": _required_text(payload, "source_event_id"),
            "projection_ref": payload.get("projection_ref")
            if isinstance(payload.get("projection_ref"), dict) else {},
        }
        if action == "attention-snooze":
            event_payload["snooze_until"] = _required_text(payload, "snooze_until")
        if action == "attention-feedback":
            event_payload["feedback"] = (
                payload.get("feedback")
                if isinstance(payload.get("feedback"), dict) else {
                    "useful": bool(payload.get("useful")),
                    "false_positive": bool(payload.get("false_positive")),
                    "category": _required_text(payload, "category"),
                }
            )
        event = self.writer.emit(
            event_type,
            actor=self.actor,
            task_id=task_id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload=redact_obj(event_payload),
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="recorded",
            task_id=task_id,
            extra={
                "attention_id": attention_id,
                "fingerprint": fingerprint,
                "attention_event_type": event_type,
                "attention_event_id": event.id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "recorded",
            "action": action,
            "requested_action": requested_action,
            "event_type": event_type,
            "event_id": event.id,
            "attention_id": attention_id,
            "fingerprint": fingerprint,
        }
    def _runtime_lifecycle_action(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        proposal_only = bool(payload.get("proposal_only") or payload.get("dry_run"))
        requires_approval = action in {"runtime-stop", "runtime-restart"}
        approval_ref = _approval_ref(payload)
        if requires_approval and not proposal_only and not approval_ref:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="owner approval is required before runtime stop/restart",
                status_code=403,
                status="approval_required",
            )
        base = action.replace("runtime-", "runtime.")
        event_type = f"{base}.proposed" if proposal_only else f"{base}.requested"
        proposal_id = _proposal_id(action, payload, requested.id)
        event = self.writer.emit(
            event_type,
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload=redact_obj({
                "schema_version": "runtime.lifecycle.request.v0",
                "proposal_id": proposal_id,
                "reason": _required_text(payload, "reason"),
                "scope": _required_text(payload, "scope") or "project",
                "target": _required_text(payload, "target"),
                "impact_summary": _runtime_impact_summary(self.state_dir),
                "approval_ref": approval_ref,
                "proposal_only": proposal_only,
                "source": self.source,
                "surface": self.surface,
                "request": payload,
            }),
        )
        status = "proposed" if proposal_only else "requested"
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status=status,
            task_id=_task_id_from_payload(payload),
            extra={"proposal_id": proposal_id, "event_type": event_type, "event_id": event.id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": status,
            "action": action,
            "requested_action": requested_action,
            "reason": "runtime lifecycle request recorded; kernel/supervisor owns actual execution",
            "proposal_id": proposal_id,
            "event_type": event_type,
            "event_id": event.id,
        }
