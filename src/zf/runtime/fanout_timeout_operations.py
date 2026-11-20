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
    def _reconcile_partial_reader_timeouts(self, events: list[ZfEvent]) -> bool:
        """Repair the legacy premature aggregate, retaining every child outcome.

        Only a current, running Run with unchanged live Reader calls may resume
        waiting. Terminal Runs and real whole-stage deadlines stay terminal.
        """
        from zf.runtime.run_admission import build_run_admission_projection
        from zf.runtime.workflow_operation import reduce_workflow_operations
        from zf.runtime.pause_lifecycle import is_dispatch_paused

        if is_dispatch_paused(self.state_dir, events=events):
            return False
        failures = [e for e in events if e.type == "fanout.aggregate.completed"
                    and e.actor == "zf-cli" and e.origin in {"", "kernel"}
                    and e.payload.get("reason") == "timeout"
                    and e.payload.get("status") == "failed"
                    and e.payload.get("timeout_seconds") == 0]
        if not failures:
            return False
        runs = build_run_admission_projection(events).runs
        operations = reduce_workflow_operations(events)
        for failed in reversed(failures):
            fanout_id = str(failed.payload.get("fanout_id") or "")
            manifest = self._fanout_manifest(fanout_id)
            aggregate = manifest.get("aggregate") or {}
            run_id = str(manifest.get("trace_id") or "")
            run = runs.get(run_id)
            if (run is None or run.status != "running"
                    or manifest.get("topology") != "fanout_reader"
                    or aggregate.get("last_event_id") != failed.id
                    or aggregate.get("status") != "failed"
                    or (manifest.get("aggregate_config") or {}).get("mode") != "wait_for_all"
                    or self._fanout_identity_stale_reason(fanout_id)[0]):
                continue
            timeout = next((e for e in events if e.id == failed.causation_id), None)
            if (timeout is None or timeout.type != "fanout.timed_out"
                    or timeout.actor != "zf-cli" or timeout.origin not in {"", "kernel"}
                    or timeout.payload.get("fanout_id") != fanout_id):
                continue
            children = manifest.get("children") or []
            timed_out = set(timeout.payload.get("pending_children") or [])
            live = [c for c in children if c.get("status") == "dispatched"]
            if (not timed_out or not live
                    or not timed_out <= {c.get("child_id") for c in children if c.get("status") == "failed"}):
                continue
            for child in live:
                payload = child.get("payload") or {}
                operation = operations.get(str(payload.get("operation_id") or ""), {})
                if (operation.get("status") != "running"
                        or operation.get("operation_type") != "fanout_reader_child"
                        or operation.get("workflow_run_id") != run_id
                        or operation.get("role_instance") != child.get("role_instance")
                        or operation.get("active_attempt_id") != payload.get("attempt_id")
                        or not payload.get("request_hash")
                        or operation.get("request_hash") != payload["request_hash"]):
                    break
            else:
                self.event_writer.append(ZfEvent(type="fanout.aggregate.started", actor="zf-cli",
                    origin="kernel", causation_id=failed.id, correlation_id=run_id, payload={
                        "fanout_id": fanout_id, "trace_id": run_id, "workflow_run_id": run_id,
                        "stage_id": manifest.get("stage_id"), "mode": "wait_for_all",
                        "recovery_kind": "partial_reader_idle_timeout",
                        "recovered_from_event_id": failed.id,
                    }))
                return True
        return False

    def _defer_partial_reader_timeout(self, *, manifest: dict,
                                     pending_children: list[str], causation_event: ZfEvent) -> bool:
        """Settle the timed-out children while healthy siblings keep their calls."""
        if (manifest.get("topology") != "fanout_reader"
                or (manifest.get("aggregate_config") or {}).get("mode", "wait_for_all") != "wait_for_all"):
            return False
        if not any(child.get("child_id") not in pending_children
                   and child.get("status") not in {"completed", "failed", "cancelled", "timed_out"}
                   for child in manifest.get("children", [])):
            return False
        fail_reader_child_operations(self, manifest=manifest, pending_children=pending_children,
                                     causation_event=causation_event, reason="reader_idle_timeout")
        return True

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
        identity = self._fanout_flow_identity_payload(manifest)
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
                    + (f"the {timeout_seconds}s runtime liveness deadline" if timeout_seconds > 0
                       else "the configured per-worker idle deadline")
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
                **identity,
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
                    **identity,
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
