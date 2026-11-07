"""Explicit, one-generation Impl recovery after reviewed integration failure."""

from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.workflow_operation import reduce_workflow_operations

SOURCE = "task_pipeline_integration_rework"
_ACTIVE = {"requested", "reserved", "running", "suspended"}


def _target(operation_id: str, operations: Mapping[str, Mapping[str, Any]],
        contexts: Mapping[str, Mapping[str, Any]], events: Sequence[ZfEvent]
        ) -> tuple[Mapping[str, Any], ZfEvent]:
    op = operations.get(operation_id, {})
    task_id = str(op.get("task_id") or "")
    context = contexts.get(task_id, {})
    if (op.get("task_pipeline_stage") != "integration" or op.get("status") != "blocked"
            or not context or any(op.get(k) != context.get(k)
                for k in ("workflow_run_id", "task_map_generation"))):
        raise ValueError("rework requires the current blocked integration")
    generation = int(op.get("operation_generation") or 0)
    rows = [r for r in operations.values() if r.get("task_id") == task_id]
    if any(r.get("status") in _ACTIVE for r in rows):
        raise ValueError("an active operation already owns the task")
    rows = [r for r in rows if all(r.get(k) == op.get(k)
            for k in ("workflow_run_id", "task_map_generation"))]
    if any(int(r.get("operation_generation") or 0) > generation or (
            r.get("task_pipeline_stage") == "integration" and r.get("operation_id") != operation_id
            and int(r.get("operation_generation") or 0) >= generation) for r in rows):
        raise ValueError("a newer operation supersedes this integration")
    source = next((e for e in reversed(events) if e.type == "integration.queue.needs_review"
        and e.origin == "kernel" and e.task_id == task_id
        and e.payload.get("operation_id") == operation_id
        and all(e.payload.get(k) == op.get(k) for k in ("workflow_run_id", "task_map_generation"))), None)
    if source is None:
        raise ValueError("blocked integration lacks canonical needs_review evidence")
    targets = []
    for stage, event_type, commit_key in (("impl", "dev.build.done", "source_commit"),
            ("verify", "task.pipeline.verify.completed", "target_commit")):
        matches = [r for r in rows if r.get("task_pipeline_stage") == stage
            and int(r.get("operation_generation") or 0) == generation]
        if len(matches) != 1:
            raise ValueError("integration rework requires same-generation Impl and Verify")
        row = matches[0]
        source_id = (row.get("admitted_control_result_ref") or {}).get("source_event_id")
        result = next((e for e in events if e.id == source_id), None)
        admitted = next((e for e in events if e.id == row.get("call_result_admitted_event_id")), None)
        if (row.get("status") != "settled" or row.get("semantic_verdict") != "passed"
                or admitted is None or admitted.type != "workflow.call.result.admitted"
                or admitted.task_id != task_id or admitted.payload.get("operation_id") != row.get("operation_id")
                or (admitted.payload.get("control_result_ref") or {}).get("source_event_id") != source_id
                or result is None or result.type != event_type or result.task_id != task_id
                or any(result.payload.get(k) != row.get(k) for k in
                    ("operation_id", "workflow_run_id", "task_map_generation"))
                or not result.payload.get(commit_key)):
            raise ValueError("integration rework requires admitted passing Impl and Verify sources")
        targets.append(result.payload[commit_key])
    if targets[0] != targets[1]:
        raise ValueError("Verify target does not match the admitted Impl commit")
    return op, source


def request_integration_impl_rework(*, state_dir: Path, event_log: Any, event_writer: Any,
        operation_id: str, reason: str, approval_ref: str, apply: bool = False) -> dict[str, Any]:
    if not reason.strip() or not approval_ref.strip():
        raise ValueError("Impl rework requires a reason and explicit approval_ref")
    from zf.runtime.task_pipeline_integration_retry import request_integration_retry

    with locked_path(state_dir / "locks" / "task-pipeline-integration-rework.lock"):
        # Reuse the existing terminal-run/task/attempt/current-integration safety gate.
        request_integration_retry(state_dir=state_dir, event_log=event_log, event_writer=event_writer,
            operation_id=operation_id, reason=reason, apply=False)
        events = event_log.read_all()
        op, source = _target(operation_id, reduce_workflow_operations(events),
            task_pipeline_generation_contexts(events), events)
        generation = int(op["operation_generation"])
        prior = next((e for e in events if e.type == "task.rework.requested" and e.origin == "kernel"
            and e.payload.get("source") == SOURCE and e.payload.get("operation_id") == operation_id
            and e.payload.get("source_event_id") == source.id), None)
        if prior:
            return {"status": "already_requested", "event_id": prior.id, "operation_id": operation_id}
        payload = {"source": SOURCE, "operation_id": operation_id, "task_id": op["task_id"],
            "workflow_run_id": op["workflow_run_id"], "task_map_generation": op["task_map_generation"],
            "operation_generation": generation, "source_event_id": source.id,
            "reason": reason.strip(), "approval_ref": approval_ref.strip(),
            "authorized_next_operation_generation": generation + 1}
        result = {"status": "preview", **payload, "next_operation_generation": generation + 1}
        if apply:
            event = event_writer.append(ZfEvent(type="task.rework.requested", actor="zf-cli",
                origin="kernel", task_id=op["task_id"], causation_id=source.id,
                correlation_id=op["workflow_run_id"], payload=payload))
            result.update(status="requested", event_id=event.id)
        return result


def integration_impl_rework_requests(*, events: Sequence[ZfEvent],
        operations: Mapping[str, Mapping[str, Any]], contexts: Mapping[str, Mapping[str, Any]]) -> dict:
    requests = {}
    for event in events:
        p = event.payload
        if event.type != "task.rework.requested" or event.origin != "kernel" or p.get("source") != SOURCE:
            continue
        try:
            op, source = _target(str(p.get("operation_id") or ""), operations, contexts, events)
        except (ValueError, TypeError):
            continue
        generation = int(op["operation_generation"])
        if (event.task_id != op["task_id"] or p.get("source_event_id") != source.id
                or any(p.get(k) != op.get(k) for k in
                    ("workflow_run_id", "task_map_generation", "operation_generation"))
                or p.get("authorized_next_operation_generation") != generation + 1
                or not str(p.get("approval_ref") or "").strip()):
            continue
        requests[op["task_id"]] = {**p, "fault": SOURCE, "event_id": event.id,
            "expected_action": "repair_reviewed_integration_failure_then_repeat_independent_verify"}
    return requests
