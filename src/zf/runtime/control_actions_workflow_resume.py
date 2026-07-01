"""Workflow resume controlled-action handlers."""

from __future__ import annotations

from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events import ZfEvent


class WorkflowResumeActionsMixin:
    def _workflow_batch_resume(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
        safe_resume_action = str(payload.get("safe_resume_action") or "").strip()
        if not checkpoint_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="checkpoint_id is required",
                status_code=422,
                status="rejected",
            )
        if not safe_resume_action:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="safe_resume_action is required",
                status_code=422,
                status="rejected",
            )
        if safe_resume_action == "trigger_rework" and not bool(
            payload.get("mutating_resume_supported")
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="trigger_rework requires explicit mutating_resume_supported",
                status_code=409,
                status="blocked",
            )

        from zf.runtime.workflow_resume import apply_workflow_resume

        result = apply_workflow_resume(
            self.state_dir,
            self.config or ZfConfig(),
            event_writer=self.writer,
            project_root=self.project_root,
            checkpoint_id=checkpoint_id,
            override_task_map_ref=str(payload.get("override_task_map_ref") or ""),
        )
        status = _workflow_resume_status(result)
        ok = status in {"applied", "no_op"}
        event = self.writer.emit(
            "workflow.resume.control_action.result",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "schema_version": "workflow-resume.control-action-result.v1",
                "checkpoint_id": checkpoint_id,
                "safe_resume_action": safe_resume_action,
                "status": status,
                "applied": int(result.get("applied") or 0),
                "rejected": int(result.get("rejected") or 0),
                "no_op_reason": str(result.get("no_op_reason") or ""),
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status=status,
            task_id=None,
            extra={"checkpoint_id": checkpoint_id, "safe_resume_action": safe_resume_action},
        )
        return {
            "ok": ok,
            "status": status,
            "action": action,
            "requested_action": requested_action,
            "event_id": event.id,
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": safe_resume_action,
            "resume_result": result,
            "reason": str(result.get("no_op_reason") or ""),
        }


def _workflow_resume_status(result: dict[str, Any]) -> str:
    if int(result.get("applied") or 0) > 0:
        return "applied"
    if int(result.get("rejected") or 0) > 0:
        return "rejected"
    if str(result.get("no_op_reason") or ""):
        return "no_op"
    batch_results = result.get("batch_results") or []
    results = result.get("results") or []
    for item in [*results, *batch_results]:
        if isinstance(item, dict) and str(item.get("reason") or "").startswith("rejected:"):
            return "rejected"
    return "no_op"


__all__ = ["WorkflowResumeActionsMixin"]
