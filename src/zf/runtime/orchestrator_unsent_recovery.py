"""One same-checkpoint retry after a proven pre-send provider gate clears."""
from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.pause_lifecycle import is_run_dispatch_paused
from zf.runtime.provider_turn_liveness import active_codex_turn_from_events
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def unsent_checkpoint_failure(runtime: Any, operation: Mapping[str, Any],
                             events: list[ZfEvent]) -> ZfEvent | None:
    """Bind the failure to the current attempt, never infer from error prose."""
    from zf.runtime.orchestrator_agent_role import orchestrator_instance_id

    if (operation.get("operation_type") != "orchestrator_agent_semantic"
            or operation.get("role_instance") != orchestrator_instance_id(runtime)
            or operation.get("status") != "failed" or operation.get("retry_count")):
        return None
    run_id = str(operation.get("workflow_run_id") or "")
    if not run_id or is_run_dispatch_paused(events, run_id):
        return None
    if active_codex_turn_from_events(events, str(operation.get("role_instance") or "")):
        return None
    from zf.runtime.run_admission import build_run_admission_projection

    run = build_run_admission_projection(events).runs.get(run_id)
    if run is not None and run.status != "running":
        return None
    by_id = {event.id: event for event in events}
    terminal = by_id.get(str(operation.get("last_event_id") or ""))
    checkpoint = by_id.get(str(operation.get("dispatch_id") or ""))
    if (terminal is None or terminal.type != "workflow.operation.failed"
            or terminal.origin != "kernel" or terminal.actor != "zf-cli"
            or checkpoint is None or checkpoint.type != "orchestrator.semantic.checkpoint.requested"
            or terminal.causation_id != checkpoint.id
            or any(terminal.payload.get(k) != operation.get(k)
                   for k in ("operation_id", "request_hash", "workflow_run_id"))
            or any(checkpoint.payload.get(k) != operation.get(k)
                   for k in ("operation_id", "workflow_run_id"))):
        return None
    failure = next((event for event in reversed(events)
        if event.type == "orchestrator.dispatch_failed" and event.actor == "zf-cli"
        and event.origin == "kernel" and event.causation_id == checkpoint.id
        and event.correlation_id == run_id
        and event.payload.get("operation_id") == operation["operation_id"]
        and event.payload.get("trigger_event_id") == checkpoint.id), None)
    if failure is None or failure.payload.get("failure_class") != "provider_interactive_blocked":
        return None
    # Older diagnostics dropped input_not_sent. This exact structured class is
    # emitted only by TmuxReadinessMixin before touching provider input. An
    # explicit contradictory field never receives that compatibility treatment.
    if "input_not_sent" in failure.payload and failure.payload["input_not_sent"] is not True:
        return None
    if not _current_request(runtime, operation, events):
        return None
    return failure


def _current_request(runtime: Any, operation: Mapping[str, Any], events: list[ZfEvent]) -> bool:
    from zf.core.config.render import redact_config, renderable_config_to_primitive
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator_agent_execution_policy import (
        execution_decision_rejection, task_execution_binding,
    )
    from zf.runtime.orchestrator_agent_plan_binding import (
        current_checkpoint_plan_package, semantic_failure_target_task_id,
    )

    try:
        stored = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload
        if any(stored.get(k) != operation.get(k)
               for k in ("operation_id", "workflow_run_id", "operation_type")):
            return False
        request = stored["request"]
        config = hydrate_sidecar_ref(runtime.state_dir, request["effective_config_ref"]).payload
        if config.get("config") != redact_config(renderable_config_to_primitive(runtime.config)):
            return False
        # Candidate admission has a separate supersession protocol; do not
        # extend this recovery into a new plan revision.
        if request.get("checkpoint") == "plan_candidate":
            return False
        target = semantic_failure_target_task_id(runtime.state_dir, request)
        current = current_checkpoint_plan_package(
            events, workflow_run_id=operation["workflow_run_id"], task_id=target,
        )
        if any(request.get("plan_artifact_package_" + key)
               != current.get("package_" + key)
               for key in ("id", "ref", "digest")
               if request.get("plan_artifact_package_" + key) or current.get("package_" + key)):
            return False
        if execution_decision_rejection(runtime, decision={"decision": "escalate"}, request=request):
            return False
        body = hydrate_sidecar_ref(runtime.state_dir, request["checkpoint_input_ref"]).payload
        checkpoint_context = body.get("checkpoint_context", {})
        if checkpoint_context.get("candidate_failure_scope") == "aggregate":
            from zf.runtime.candidate_aggregate_review import candidate_review_binding

            failure = next((e for e in events if e.id == checkpoint_context.get("candidate_failure_id")), None)
            bound = candidate_review_binding(runtime.state_dir, runtime.config, events, failure) if failure else None
            if not bound or bound.get("binding_error"):
                return False
        context_ref = body.get("execution_context_ref")
        if context_ref:
            context = hydrate_sidecar_ref(runtime.state_dir, context_ref).payload
            store = TaskStore(runtime.state_dir / "kanban.json")
            for task_id, expected in context.get("task_bindings", {}).items():
                task = store.get(task_id)
                if task is None or task_execution_binding(task) != expected:
                    return False
        return True
    except (KeyError, OSError, ValueError, TypeError):
        return False


def unsent_checkpoint_retry(runtime: Any, event: ZfEvent, operation: Mapping[str, Any],
                           events: list[ZfEvent]) -> bool:
    """Admission for both normal dispatch and a lost-wake pending sweep."""
    failure = unsent_checkpoint_failure(runtime, operation, events)
    return bool(failure and event.origin == "kernel" and event.actor == "zf-cli"
                and event.payload.get("unsent_failure_event_id") == failure.id
                and event.payload.get("original_checkpoint_event_id") == operation.get("dispatch_id")
                and event.payload.get("checkpoint_dispatch_retry_attempt") == 1
                and event.payload.get("checkpoint_dispatch_retry_max_attempts") == 1
                and event.payload.get("operation_id") == operation.get("operation_id")
                and event.payload.get("workflow_run_id") == operation.get("workflow_run_id"))


def reconcile_unsent_checkpoints(runtime: Any, events: list[ZfEvent],
                                operations: Mapping[str, Mapping[str, Any]]) -> None:
    from zf.runtime.orchestrator_agent_role import orchestrator_instance_id

    role = orchestrator_instance_id(runtime)
    if (active_codex_turn_from_events(events, role)
            or any(o.get("role_instance") == role
                   and o.get("status") in {"requested", "reserved", "running", "suspended"}
                   for o in operations.values())):
        return
    for operation in operations.values():
        failure = unsent_checkpoint_failure(runtime, operation, events)
        if failure is None:
            continue
        if any(e.payload.get("unsent_failure_event_id") == failure.id for e in events
               if e.type == "orchestrator.semantic.checkpoint.requested"):
            continue
        try:
            if not runtime.transport.is_alive(role) or not runtime.transport.wait_ready(role, r"[›>]", timeout=0.2):
                return
        except Exception:
            return
        original = next(e for e in events if e.id == operation["dispatch_id"])
        runtime.event_writer.append(ZfEvent(
            type=original.type, actor="zf-cli", origin="kernel",
            payload={**original.payload, "original_checkpoint_event_id": original.id,
                     "unsent_failure_event_id": failure.id,
                     "checkpoint_dispatch_retry_attempt": 1,
                     "checkpoint_dispatch_retry_max_attempts": 1},
            causation_id=failure.id, correlation_id=operation["workflow_run_id"],
        ))
        return


def claim_unsent_retry(runtime: Any, event: ZfEvent, operation_id: str) -> bool:
    """Serialize the actual retry claim; a duplicate wake may not send again."""
    from zf.core.state.locks import locked_path
    from zf.runtime.orchestrator_agent_operations import workflow_operation_service
    from zf.runtime.workflow_operation import _safe_component, load_workflow_operation

    guard = runtime.state_dir / "projections" / "workflow-operations" / f"{_safe_component(operation_id)}.guard"
    with locked_path(guard):
        operation = load_workflow_operation(runtime.event_log, operation_id)
        if not operation or not unsent_checkpoint_retry(runtime, event, operation, runtime.event_log.read_all()):
            return False
        return workflow_operation_service(runtime).mark_retry_started(
            operation_id=operation_id, request_hash=operation["request_hash"],
            workflow_run_id=operation["workflow_run_id"], retry_attempt=1,
            reason="retry_after_unsent_provider_gate", dispatch_id=event.id,
            role_instance=operation["role_instance"], active_attempt_id=operation["active_attempt_id"],
            lease_id=operation["active_attempt_id"], causation_id=event.id,
            correlation_id=operation["workflow_run_id"],
        ) is not None
