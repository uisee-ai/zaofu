"""Close durable child operations when a reader fanout times out."""

from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_runtime import workflow_operation_service
from zf.runtime.channel_workflow_bridge import emit_fanout_channel_state_update
from zf.runtime.failure_kind import FAILURE_KIND_INFRA
from zf.runtime.workflow_operation import (
    TERMINAL_OPERATION_STATUSES,
    load_workflow_operation,
)


def fail_reader_child_operations(
    runtime: Any,
    *,
    manifest: Mapping[str, Any],
    pending_children: list[str],
    causation_event: ZfEvent | None,
    reason: str,
) -> list[ZfEvent]:
    """Close live operations owned by mechanically failed reader children."""

    pending = {str(item) for item in pending_children if str(item)}
    if not pending:
        return []
    trigger = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), Mapping)
        else {}
    )
    workflow_run_id = str(
        trigger.get("workflow_run_id")
        or trigger.get("run_id")
        or manifest.get("trace_id")
        or ""
    )
    service = workflow_operation_service(runtime)
    emitted: list[ZfEvent] = []
    for child in manifest.get("children") or []:
        if not isinstance(child, Mapping):
            continue
        child_id = str(child.get("child_id") or "")
        if child_id not in pending:
            continue
        payload = (
            child.get("payload")
            if isinstance(child.get("payload"), Mapping)
            else {}
        )
        operation_id = str(
            child.get("operation_id") or payload.get("operation_id") or ""
        )
        request_hash = str(
            child.get("request_hash") or payload.get("request_hash") or ""
        )
        if not operation_id or not request_hash:
            continue
        operation = load_workflow_operation(runtime.event_log, operation_id)
        if (
            operation is None
            or str(operation.get("status") or "")
            in TERMINAL_OPERATION_STATUSES
        ):
            continue
        event = service.fail(
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=(
                str(operation.get("workflow_run_id") or "")
                or workflow_run_id
            ),
            task_id=str(
                operation.get("task_id")
                or child.get("task_id")
                or payload.get("task_id")
                or ""
            ),
            reason=reason,
            causation_id=(
                causation_event.id if causation_event is not None else ""
            ),
            correlation_id=str(manifest.get("trace_id") or workflow_run_id),
        )
        if event is not None:
            emitted.append(event)
    return emitted


class FanoutTimeoutOperationsMixin:
    def _publish_fanout_timeout_failure(
        self,
        *,
        manifest: dict,
        pending_children: list[str],
        timeout_seconds: int,
        causation_event: ZfEvent | None,
        existing_events: list[ZfEvent],
    ) -> None:
        fanout_id = str(manifest.get("fanout_id") or "")
        if not fanout_id:
            return
        stale_reason, _superseded_by = self._fanout_identity_stale_reason(
            fanout_id
        )
        if stale_reason:
            return
        aggregate_config = (
            manifest.get("aggregate_config")
            if isinstance(manifest.get("aggregate_config"), dict)
            else {}
        )
        trace_id = str(manifest.get("trace_id") or "")
        stage_id = str(manifest.get("stage_id") or "")
        failure_event = str(aggregate_config.get("failure_event") or "")
        mode = str(aggregate_config.get("mode") or "wait_for_all")
        has_aggregate_started = any(
            event.type == "fanout.aggregate.started"
            and isinstance(event.payload, dict)
            and event.payload.get("fanout_id") == fanout_id
            for event in existing_events
        )
        causation_id = (
            causation_event.id if causation_event is not None else None
        )
        operation_failures = fail_reader_child_operations(
            self,
            manifest=manifest,
            pending_children=pending_children,
            causation_event=causation_event,
            reason="fanout_timeout",
        )
        runtime_findings = [
            {
                "finding_id": f"{child_id}-runtime-timeout",
                "severity": "high",
                "category": "runtime_failure",
                "child_id": child_id,
                "message": (
                    "fanout worker did not reach a terminal result within "
                    f"the {timeout_seconds}s runtime liveness deadline"
                ),
            }
            for child_id in pending_children
        ]
        if not has_aggregate_started:
            self.event_writer.append(ZfEvent(
                type="fanout.aggregate.started",
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "mode": mode,
                },
                causation_id=causation_id,
                correlation_id=trace_id,
            ))
        aggregate_event = self.event_writer.append(ZfEvent(
            type="fanout.aggregate.completed",
            actor="zf-cli",
            payload={
                "fanout_id": fanout_id,
                "trace_id": trace_id,
                "stage_id": stage_id,
                "status": "failed",
                "reason": "timeout",
                "success_event": "",
                "failure_event": failure_event,
                "failed_children": pending_children,
                "pending_children": pending_children,
                "timeout_seconds": timeout_seconds,
                "failure_kind": FAILURE_KIND_INFRA,
                "findings": runtime_findings,
            },
            causation_id=causation_id,
            correlation_id=trace_id,
        ))
        self._consume_durable_fanout_aggregate_result(aggregate_event)
        emit_fanout_channel_state_update(
            writer=self.event_writer,
            terminal_event=aggregate_event,
            manifest={**manifest, "aggregate": aggregate_event.payload},
        )
        failure_already_published = any(
            event.type == failure_event
            and isinstance(event.payload, dict)
            and event.payload.get("fanout_id") == fanout_id
            for event in existing_events
        )
        if failure_event == "workflow.operation.failed" and operation_failures:
            failure_already_published = True
        if failure_event and not failure_already_published:
            children = [
                child
                for child in manifest.get("children", []) or []
                if isinstance(child, dict)
            ]
            self.event_writer.append(ZfEvent(
                type=failure_event,
                actor="zf-cli",
                payload={
                    "fanout_id": fanout_id,
                    "trace_id": trace_id,
                    "stage_id": stage_id,
                    "status": "failed",
                    "reason": "timeout",
                    "target_ref": manifest.get("target_ref", ""),
                    "child_count": len(children),
                    "failed_children": pending_children,
                    "pending_children": pending_children,
                    "timeout_seconds": timeout_seconds,
                    "failure_kind": FAILURE_KIND_INFRA,
                    "findings": runtime_findings,
                },
                causation_id=aggregate_event.id,
                correlation_id=trace_id,
            ))


__all__ = [
    "FanoutTimeoutOperationsMixin",
    "fail_reader_child_operations",
]
