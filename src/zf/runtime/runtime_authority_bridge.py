"""Thin Orchestrator wiring for Run and TaskAttempt authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision
from zf.runtime.transport import DispatchContext


class RuntimeAuthorityMixin:
    def _reconcile_run_admission(self) -> None:
        from zf.runtime.run_admission import reconcile_run_admission

        reconcile_run_admission(self)

    def _reconcile_run_admission_for_events(
        self,
        events: list[ZfEvent],
    ) -> None:
        from zf.runtime.run_admission import (
            RUN_ADMISSION_RECONCILE_EVENT_TYPES,
        )

        if any(
            event.type in RUN_ADMISSION_RECONCILE_EVENT_TYPES
            for event in events
        ):
            self._reconcile_run_admission()

    def _reconcile_task_attempts(self) -> None:
        from zf.runtime.task_attempt_runtime import reconcile_task_attempts

        reconcile_task_attempts(self)

    def _assert_run_dispatch_allowed(
        self,
        role_name: str,
        context: DispatchContext | None,
    ) -> None:
        if context is None or not context.task_id:
            return
        try:
            task = self.task_store.get(context.task_id)
        except Exception:
            task = None
        from zf.runtime.run_admission import (
            RunDispatchBlocked,
            record_run_dispatch_blocked,
            run_dispatch_block_reason,
        )

        run_id = str(context.run_id or "")
        blocker = run_dispatch_block_reason(self, task=task, run_id=run_id)
        if not blocker:
            return
        record_run_dispatch_blocked(
            self,
            task=task,
            run_id=run_id,
            reason=blocker,
        )
        self._rollback_inflight_dispatch(context)
        raise RunDispatchBlocked(
            role_name=role_name,
            reason=blocker,
            run_id=run_id,
        )

    def _deliver_transport_task(
        self,
        role_name: str,
        context: DispatchContext | None,
        briefing_path: Path,
        prompt: str,
    ) -> DispatchContext | None:
        from zf.runtime.task_attempt_runtime import (
            fail_task_attempt_delivery,
            mark_task_attempt_sent,
            prepare_task_attempt,
        )

        prepared = prepare_task_attempt(
            self,
            context=context,
            briefing_path=briefing_path,
        )
        delivery_context = prepared.context if prepared is not None else context
        try:
            try:
                self.transport.send_task(
                    role_name,
                    briefing_path,
                    prompt,
                    context=delivery_context,
                )
            except TypeError as exc:
                if "context" not in str(exc):
                    raise
                self.transport.send_task(role_name, briefing_path, prompt)
        except Exception as exc:
            fail_task_attempt_delivery(self, prepared, error=exc)
            self._rollback_inflight_dispatch(delivery_context)
            raise
        mark_task_attempt_sent(self, prepared)
        return delivery_context

    def _task_attempt_rejection(
        self,
        event: ZfEvent,
        task: Any,
    ) -> WorkflowRuntimeDecision | None:
        from zf.runtime.task_attempt_runtime import validate_task_attempt_result

        reason = validate_task_attempt_result(self, event, task=task)
        if not reason:
            return None
        return WorkflowRuntimeDecision(
            action="block",
            task_id=event.task_id,
            reason=(
                f"{event.type} rejected: TaskAttempt identity {reason}"
            ),
        )

    def _task_attempt_rejection_for_id(
        self,
        event: ZfEvent,
        task_id: str,
    ) -> WorkflowRuntimeDecision | None:
        task = self.task_store.get(task_id)
        if task is None:
            return None
        return self._task_attempt_rejection(event, task)

    def _reject_resolved_task_attempt(
        self,
        event: ZfEvent,
        task_id: str,
        decisions: list[WorkflowRuntimeDecision],
    ) -> bool:
        rejected = self._task_attempt_rejection_for_id(event, task_id)
        if rejected is None:
            return False
        decisions.append(rejected)
        self._processed_event_ids.add(event.id)
        return True

    def _settle_task_attempt_result(self, event: ZfEvent) -> None:
        from zf.runtime.task_attempt_runtime import settle_task_attempt_result

        settle_task_attempt_result(self, event)

    def _renew_task_attempt_lease(self, event: ZfEvent) -> None:
        try:
            from zf.runtime.task_attempt_runtime import renew_task_attempt_lease

            renew_task_attempt_lease(self, event)
        except Exception:
            pass

    def _run_lifecycle_rejection(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision | None:
        from zf.runtime.run_admission import reject_late_run_result

        reason = reject_late_run_result(self, event)
        if not reason:
            return None
        if reason.startswith("run_terminal:"):
            self._audit_late_fanout_result(event)
        return WorkflowRuntimeDecision(
            action="block",
            task_id=event.task_id,
            reason=f"{event.type} rejected: {reason}",
        )

    def _audit_late_fanout_result(self, event: ZfEvent) -> None:
        """Retain fanout-local stale evidence when Run admission fences a result."""

        payload = self._fanout_result_payload(event)
        fanout_id = str(payload.get("fanout_id") or "").strip()
        child_id = str(
            payload.get("child_id") or payload.get("child_run") or ""
        ).strip()
        if not fanout_id or not child_id:
            return
        manifest = self._fanout_manifest(fanout_id)
        if not manifest or manifest.get("topology") not in {
            "fanout_reader",
            "fanout_writer_scoped",
        }:
            return
        from zf.runtime.terminal_events import successful_terminal_before_event

        terminal = successful_terminal_before_event(
            self.event_log.read_all(),
            event,
        )
        if terminal is None:
            return
        self._emit_fanout_identity_stale_completion(
            event=event,
            payload=payload,
            manifest=manifest,
            child=self._fanout_child(manifest, child_id),
            reason="run_terminal",
            superseded_by=terminal.id,
        )

    def _run_fanout_dispatch_blocked(self, event: ZfEvent) -> bool:
        from zf.runtime.run_admission import (
            record_run_dispatch_blocked,
            run_dispatch_block_reason,
        )

        reason = run_dispatch_block_reason(self, event=event)
        if not reason:
            return False
        record_run_dispatch_blocked(self, event=event, reason=reason)
        return True

    def _admit_light_workflow_invoke(
        self,
        event: ZfEvent,
        *,
        payload: dict[str, Any],
        task_id: str,
        pattern_id: str,
        entry_trigger: str,
    ) -> WorkflowRuntimeDecision:
        """Admit a configured light flow before publishing its entry."""

        from zf.runtime.light_flow import light_flow_metadata

        flow_kind = str(
            payload.get("flow_kind")
            or payload.get("request_kind")
            or payload.get("kind")
            or ""
        )
        metadata = light_flow_metadata(self.config, flow_kind=flow_kind)
        expected_trigger = str(
            (metadata or {}).get("light_entry_trigger") or ""
        )
        raw_entry_payload = payload.get("light_entry_payload")
        run_id = self._workflow_run_id(event, payload)
        entry_run_id = str(
            (raw_entry_payload or {}).get("workflow_run_id")
            or (raw_entry_payload or {}).get("run_id")
            or ""
        ) if isinstance(raw_entry_payload, dict) else ""
        rejection = ""
        if not metadata or entry_trigger != expected_trigger:
            rejection = "light entry trigger is not configured"
        elif not isinstance(raw_entry_payload, dict):
            rejection = "light entry payload is missing"
        elif not run_id or (entry_run_id and entry_run_id != run_id):
            rejection = "light entry Run identity mismatch"
        if rejection:
            self._emit_workflow_invoke_rejected(
                event,
                rejection,
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return WorkflowRuntimeDecision(
                action="block",
                task_id=task_id,
                reason=f"light workflow invoke rejected: {rejection}",
            )

        from zf.runtime.run_admission import admit_workflow_invoke

        admission = admit_workflow_invoke(self, event)
        if admission.status != "admitted":
            action = "observe" if admission.status in {"queued", "paused"} else "block"
            return WorkflowRuntimeDecision(
                action=action,
                task_id=task_id,
                reason=(
                    f"light workflow invoke {admission.status}: "
                    f"{admission.reason or admission.run_id}"
                ),
            )
        events = self.event_log.read_all()
        existing_accept = next(
            (
                candidate
                for candidate in reversed(events)
                if candidate.type == "workflow.invoke.accepted"
                and str((candidate.payload or {}).get("source_event_id") or "")
                == event.id
            ),
            None,
        )
        if existing_accept is not None:
            self._activate_workflow_task(existing_accept)
            return WorkflowRuntimeDecision(
                action="observe",
                task_id=task_id,
                reason="light workflow invoke replayed without duplicate entry",
            )

        accepted = ZfEvent(
            type="workflow.invoke.accepted",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "pattern_id": pattern_id,
                "source_event_id": event.id,
                "topology": "light",
                "source_refs": (
                    dict(payload.get("source_refs") or {})
                    if isinstance(payload.get("source_refs"), dict)
                    else {}
                ),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        )
        from zf.runtime.durable_call_workflow import _WORKFLOW_IDENTITY_KEYS

        for key in _WORKFLOW_IDENTITY_KEYS:
            value = payload.get(key)
            if value not in (None, ""):
                accepted.payload[key] = value
        accepted = self.event_writer.append(accepted)
        self._activate_workflow_task(accepted)
        self._mark_workflow_request_running(payload, accepted_event=accepted)

        entry_payload = dict(raw_entry_payload)
        entry_payload["workflow_run_id"] = run_id
        entry_payload.setdefault("source_event_id", event.id)
        entry_payload.setdefault("source", "workflow-invoke-light")
        self.event_writer.append(ZfEvent(
            type=entry_trigger,
            actor=str(payload.get("requested_by") or event.actor or "zf-cli"),
            task_id=task_id,
            payload=entry_payload,
            causation_id=accepted.id,
            correlation_id=event.correlation_id,
        ))
        return WorkflowRuntimeDecision(
            action="workflow_invoke",
            task_id=task_id,
            reason=f"light workflow invoke accepted: {entry_trigger}",
        )

    def _mark_workflow_request_running(
        self,
        payload: dict[str, Any],
        *,
        accepted_event: ZfEvent,
    ) -> None:
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return
        try:
            from zf.runtime.workflow_requests import (
                load_workflow_request,
                mark_workflow_request,
            )

            request = load_workflow_request(self.state_dir, request_id)
            if str(request.get("status") or "") != "submitted":
                return
            mark_workflow_request(
                self.state_dir,
                request_id,
                status="running",
                actor="orchestrator",
                writer=self.event_writer,
                run_id=str(
                    payload.get("workflow_run_id")
                    or payload.get("run_id")
                    or request_id
                ),
                event_type="workflow.request.running",
            )
        except Exception:
            return

    def _send_scoped_transport_task(
        self,
        role: Any,
        briefing_path: Path,
        prompt: str,
        *,
        trace_id: str | None,
        dispatch_id: str,
        identity_sources: tuple[Any, ...],
    ) -> DispatchContext:
        context = self._dispatch_context(
            role=role,
            briefing_path=briefing_path,
            trace_id=trace_id,
            task_id=_identity_value(identity_sources, "task_id") or None,
            parent_task_id=_identity_value(
                identity_sources,
                "parent_task_id",
            )
            or None,
            run_id=_identity_value(
                identity_sources,
                "workflow_run_id",
                "run_id",
            )
            or None,
            operation_id=_identity_value(
                identity_sources,
                "operation_id",
            )
            or None,
            dispatch_id=dispatch_id,
        )
        return (
            self._send_transport_task(
                role.instance_id,
                briefing_path,
                prompt,
                context,
            )
            or context
        )

    @staticmethod
    def _bind_task_attempt_payload(
        payload: dict[str, Any],
        context: DispatchContext,
    ) -> None:
        from zf.runtime.task_attempt_runtime import dispatch_attempt_payload

        payload.update(
            dispatch_attempt_payload(context, include_run_alias=False)
        )

    @staticmethod
    def _workflow_run_id(event: ZfEvent, payload: dict[str, Any]) -> str:
        return str(
            payload.get("workflow_run_id")
            or payload.get("run_id")
            or event.correlation_id
            or ""
        )

    def _emit_workflow_invoke_rejected(
        self,
        event: ZfEvent,
        reason: str,
        *,
        task_id: str,
        pattern_id: str,
    ) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        rejection_payload = {
            "task_id": task_id,
            "pattern_id": pattern_id,
            "source_event_id": event.id,
            "reason": reason,
            "channel_id": str(payload.get("channel_id") or ""),
            "thread_id": str(payload.get("thread_id") or ""),
        }
        for key in ("request_id", "run_id", "workflow_run_id"):
            value = payload.get(key)
            if value not in (None, ""):
                rejection_payload[key] = value
        with locked_path(Path(self.state_dir) / "locks" / "workflow-invoke-rejection"):
            prior = next((
                row for row in self.event_log.read_all()
                if row.type == "workflow.invoke.rejected"
                and (row.payload or {}).get("source_event_id") == event.id
            ), None)
            if prior is None:
                self.event_writer.append(ZfEvent(
                    type="workflow.invoke.rejected",
                    actor="zf-cli",
                    task_id=task_id or event.task_id,
                    payload=rejection_payload,
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            else:
                reason = str(prior.payload.get("reason") or reason)
        from zf.runtime.run_admission import reject_workflow_invoke_admission

        # Replay must still repair a crash between the two verdict writes.
        reject_workflow_invoke_admission(self, event, reason=reason)


def _identity_value(sources: tuple[Any, ...], *keys: str) -> str:
    for source in sources:
        if source is None:
            continue
        for key in keys:
            value = (
                source.get(key)
                if isinstance(source, dict)
                else getattr(source, key, "")
            )
            text = str(value or "").strip()
            if text:
                return text
    return ""


__all__ = ["RuntimeAuthorityMixin"]
