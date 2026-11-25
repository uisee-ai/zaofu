"""Exact failed Reader binding for controlled retry, rechecked at execution."""
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events import ZfEvent
from zf.runtime.fanout_identity import fanout_current_status
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def reader_request_error(state_dir: Path, events: list[ZfEvent], request: ZfEvent, config: ZfConfig | None) -> str:
    """Validate the controlled actor and the exact failed Reader at execution."""
    from zf.runtime.recovery_authorization import current_run_grant

    grant, error = current_run_grant(config, events, str(request.payload.get("workflow_run_id") or ""))
    if error:
        return error
    if grant and (request.origin != "kernel" or request.actor not in {"run-manager", "operator", "operator:cli", "web"}):
        return "reader_recovery_requires_controlled_owner_or_run_manager"
    return reader_rebuild_error(state_dir, events, request.payload,
                                config=config if request.actor == "run-manager" else None)


def reader_rebuild_error(state_dir: Path, events: list[ZfEvent], payload: dict[str, Any], *, config=None) -> str:
    required = ("fanout_id", "fanout_child_id", "workflow_run_id",
                "active_attempt_id", "source_event_id")
    if any(not payload.get(k) for k in required):
        return "reader rebuild requires exact fanout, child, Run, attempt and failure"
    if any(e.type == "event.malformed" and e.payload.get("ledger_integrity") == "unknown" for e in events):
        return "reader rebuild ledger integrity is unknown"
    run = build_run_admission_projection(events).runs.get(payload["workflow_run_id"])
    if run is None or run.status != "running" or is_dispatch_paused(state_dir, events=events):
        return "reader rebuild Run is not dispatchable"
    identity = fanout_current_status(events, payload["fanout_id"])
    if not identity.known or not identity.current:
        return "reader rebuild fanout is stale or unknown"
    children = [e for e in events if e.type in {
        "fanout.child.dispatched", "fanout.child.failed", "fanout.child.completed",
    } and e.payload.get("fanout_id") == payload["fanout_id"]
       and e.payload.get("child_id") == payload["fanout_child_id"]]
    if (not children or children[-1].id != payload["source_event_id"]
            or children[-1].type != "fanout.child.failed" or children[-1].origin != "kernel"):
        return "reader rebuild failure is no longer current"
    failed = children[-1]
    if not payload.get("target_operation_id"):
        from zf.runtime.reader_unprepared_wait import unprepared_wait_error
        error = unprepared_wait_error(events, payload)
        if not error and config is not None:
            from zf.runtime.recovery_authorization import reader_grant_error

            return reader_grant_error(config, events, payload["workflow_run_id"], state_dir=state_dir,
                                      operation=None, failed=failed)
        return error
    operation = reduce_workflow_operations(events).get(payload["target_operation_id"], {})
    if (operation.get("operation_type") != "fanout_reader_child"
            or (operation.get("status") not in {"failed", "suspended", "requested"}
                and not _settled_execution_failure(state_dir, events, operation, failed))
            or operation.get("workflow_run_id") != payload["workflow_run_id"]
            or (payload.get("task_id") and payload["task_id"] != operation.get("task_id"))
            or operation.get("active_attempt_id") != payload["active_attempt_id"]
            or failed.payload.get("role_instance") != operation.get("role_instance")):
        return "reader rebuild operation binding is stale"
    dispatch = next((e for e in reversed(children) if e.type == "fanout.child.dispatched"), None)
    if dispatch is None:
        # A never-delivered environment wait has only its durable call identity.
        if failed.payload.get("run_id") != payload["active_attempt_id"]:
            return "reader rebuild operation binding is stale"
    else:
        # Infrastructure retries replace transport dispatch/TaskAttempt identities
        # while preserving the durable call attempt in the nested call payload.
        data = dispatch.payload
        call = data.get("payload")
        if (dispatch.origin != "kernel" or not isinstance(call, dict)
                or failed.payload.get("run_id") != data.get("run_id")
                or data.get("operation_id") != operation.get("operation_id")
                or data.get("workflow_run_id") != operation.get("workflow_run_id")
                or data.get("role_instance") != operation.get("role_instance")
                or any(call.get(key) != operation.get(field) for key, field in (
                    ("operation_id", "operation_id"), ("request_hash", "request_hash"),
                    ("workflow_run_id", "workflow_run_id"), ("attempt_id", "active_attempt_id"),
                ))):
            return "reader rebuild transport/call binding is stale"
    try:
        request = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload["request"]
    except (ValueError, KeyError, OSError):
        return "reader rebuild request evidence is unavailable"
    if (request.get("fanout_id") != payload["fanout_id"]
            or request.get("child_id") != payload["fanout_child_id"]):
        return "reader rebuild request targets another child"
    if payload.get("reader_method_digest"):
        from zf.runtime.candidate_reader_method import validate_resident_reader_method
        try:
            validate_resident_reader_method(state_dir, events, payload, consuming_recommendation=False)
        except ValueError as exc:
            return str(exc)
    if config is not None:
        from zf.runtime.recovery_authorization import reader_grant_error

        return reader_grant_error(config, events, payload["workflow_run_id"], state_dir=state_dir,
                                  operation=operation, failed=failed)
    return ""


def reader_rebuild_key(events: list[ZfEvent], payload: dict[str, Any]) -> str:
    """Deduplicate each authorized recovery of one still-current failed attempt."""
    operation = payload.get("target_operation_id") or payload["active_attempt_id"]
    key = f"reader-rebuild:{operation}:{payload['source_event_id']}"
    rejection_id = payload.get("retry_after_rejection_event_id")
    if not rejection_id:
        return key
    if not isinstance(rejection_id, str):
        raise ValueError("retry requires a prior rejection event ID")
    approval = payload.get("approval_ref")
    if not isinstance(approval, str) or not approval.strip():
        raise ValueError("retry after a rejected rebuild requires an approval ref")
    by_id = {event.id: event for event in events}
    rejection = by_id.get(rejection_id)
    request = by_id.get(rejection.causation_id) if rejection else None
    if (not rejection or rejection.type != "repair.action.rejected" or rejection.origin != "kernel"
            or not request or request.type != "repair.action.requested" or request.origin != "kernel"
            or request.payload.get("recovery_action") != "child-rebuild"
            or request.payload.get("kind") != "rerun_fanout_child"
            or request.payload.get("action_id") != rejection.payload.get("action_id")
            or any(request.payload.get(k) != payload.get(k) for k in (
                "fanout_id", "fanout_child_id", "workflow_run_id", "target_operation_id",
                "active_attempt_id", "source_event_id"))):
        raise ValueError("prior rejection does not bind this exact Reader rebuild")
    return f"{key}:after:{rejection.id}"


def _settled_execution_failure(state_dir: Path, events: list[ZfEvent],
                               operation: dict[str, Any], failed: ZfEvent) -> bool:
    """Retry an admitted inability to evaluate, never a product verdict."""
    ref = operation.get("admitted_control_result_ref") or {}
    if (operation.get("status") != "settled" or operation.get("semantic_verdict") != "abstained"
            or not ref or failed.payload.get("control_result_ref") != ref):
        return False
    admitted = next((e for e in reversed(events) if e.type == "workflow.call.result.admitted"
        and e.origin == "kernel" and e.payload.get("operation_id") == operation.get("operation_id")
        and e.payload.get("request_hash") == operation.get("request_hash")
        and e.payload.get("semantic_verdict") == "abstained" and e.payload.get("control_result_ref") == ref), None)
    if admitted is None:
        return False
    try:
        result = hydrate_sidecar_ref(state_dir, ref).payload
        return (result.get("schema_version") == "verification-result.v1"
            and result.get("execution_status") == "failed" and result.get("verdict") == "abstained"
            and result.get("failure_class") in {"reader_execution_failure", "verifier_execution_failure"}
            and result.get("attempt_id") == operation.get("active_attempt_id")
            and all(result.get(k) == operation.get(k) == failed.payload.get(k)
                for k in ("operation_id", "request_hash", "workflow_run_id", "task_id")))
    except (OSError, ValueError, TypeError):
        return False


def reader_rebuild_effect_matches(event: ZfEvent, action: dict[str, Any],
                                   events: list[ZfEvent]) -> bool:
    """Only the selected retry's terminal fact closes its recovery obligation."""
    if event.origin != "kernel":
        return False
    if event.type == "repair.action.rejected":
        request = next((e for e in events if e.id == event.causation_id), None)
        return bool(request and request.type == "repair.action.requested" and all(
            request.payload.get(k) == action.get(k) for k in (
                "target_operation_id", "active_attempt_id", "source_event_id",
                "fanout_id", "fanout_child_id", "workflow_run_id")))
    body = event.payload
    if (body.get("child_id") != action.get("fanout_child_id")
            or body.get("workflow_run_id", event.correlation_id) != action.get("workflow_run_id")):
        return False
    preceding = []
    for item in events:
        if item.id == event.id:
            break
        if (item.type == "fanout.child.dispatched" and item.origin == "kernel"
                and item.payload.get("fanout_id") == action.get("fanout_id")
                and item.payload.get("child_id") == action.get("fanout_child_id")):
            preceding.append(item)
    if not preceding:
        return False
    dispatch = preceding[-1]
    issued = dispatch.payload
    return bool(reader_rebuild_dispatch_matches(dispatch, action)
                and body.get("run_id") == issued["run_id"]
                and (not body.get("operation_id") or body["operation_id"] == issued["operation_id"]))


def reader_rebuild_dispatch_matches(dispatch: ZfEvent, action: dict[str, Any]) -> bool:
    """A new dispatch for this exact failed call, not another child in its fanout."""
    p = dispatch.payload
    if (dispatch.type != "fanout.child.dispatched" or dispatch.origin != "kernel"
            or not p.get("operation_id") or not p.get("run_id")
            or p.get("fanout_id") != action.get("fanout_id")
            or p.get("child_id") != action.get("fanout_child_id")
            or p.get("workflow_run_id", dispatch.correlation_id) != action.get("workflow_run_id")):
        return False
    if not action.get("target_operation_id"):
        return (p.get("retry_of_run_id") == action.get("active_attempt_id")
                and dispatch.causation_id == action.get("source_event_id"))
    return (p["operation_id"] != action["target_operation_id"]
            and (p.get("payload") or {}).get("retry_of_operation_id") == action["target_operation_id"])
