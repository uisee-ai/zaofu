"""Mechanical predecessor and evidence bindings for recovery action steps."""

from collections.abc import Iterable, Mapping
from typing import Any

from zf.core.events.model import ZfEvent


IDENTITY_FIELDS = (
    "workflow_run_id", "task_id", "task_map_generation", "contract_revision",
    "target_commit", "active_attempt_id", "plan_artifact_package_digest",
)


def has_new_reader_diagnosis_input(
    events: Iterable[ZfEvent], action: Mapping[str, Any], state: Mapping[str, Any],
) -> bool:
    """A repaired child consumes its diagnosed input, not later fanout failures.

    Keep the case identity and allow only diagnosis of a newer kernel input.
    Active effects, human waits and execution permissions remain separate gates.
    """
    from zf.runtime.reader_diagnosis_scope import has_retired_reader_diagnosis

    events = list(events)
    if has_retired_reader_diagnosis(events, action, state):
        return True
    if state.get("status") != "resolved" or not (
        action.get("action") == "diagnose-attention"
        or (action.get("action") == "resident-agent-reprompt"
            and action.get("request_kind") == "recovery_diagnosis")
    ):
        return False
    sources = action.get("source_event_ids") or []
    if len(sources) != 1:
        return False
    ordered = list(events)
    by_id = {event.id: (index, event) for index, event in enumerate(ordered)}
    current = by_id.get(sources[0])
    receipt = by_id.get(str(state.get("last_event_id") or ""))
    if not current or not receipt:
        return False
    event = current[1]
    body = event.payload or {}
    if (event.origin != "kernel" or body.get("failure_kind") != "infra"
            or not body.get("fanout_id") or not body.get("workflow_run_id")
            or action.get("workflow_run_id") != body["workflow_run_id"]):
        return False
    effect = receipt[1]
    if (effect.origin != "kernel" or effect.type != "run.manager.action.effect.passed"
            or effect.payload.get("action") != "child-rebuild"
            or any(effect.payload.get(key) != body[key]
                   for key in ("workflow_run_id", "fanout_id"))):
        return False
    diagnosis = next((e for e in reversed(ordered[:receipt[0]]) if
        e.origin == "kernel" and e.type == "run.manager.action.planned"
        and e.payload.get("recovery_case_id") == state.get("recovery_case_id")
        and e.payload.get("action") == "resident-agent-reprompt"
        and e.payload.get("request_kind") == "recovery_diagnosis"), None)
    previous_ids = diagnosis.payload.get("source_event_ids") or [] if diagnosis else []
    previous = by_id.get(previous_ids[0]) if len(previous_ids) == 1 else None
    if not previous or previous[0] >= current[0]:
        return False
    prior = previous[1]
    return (prior.origin == "kernel" and prior.type == event.type
            and prior.payload.get("failure_kind") == "infra"
            and all(prior.payload.get(key) == body[key]
                    for key in ("workflow_run_id", "fanout_id")))


def triage_completion_matches(event: ZfEvent, request: Mapping[str, Any]) -> bool:
    """Only admitted completion of the exact task generation can retire triage."""
    if event.origin != 'kernel' or event.type not in {'task.done', 'task.done.accepted'}:
        return False
    body = event.payload or {}
    for key in ('workflow_run_id', 'task_map_generation'):
        if not request.get(key) or body.get(key) != request[key]:
            return False
    if not request.get('task_id') or (event.task_id or body.get('task_id')) != request['task_id']:
        return False
    return not (
        (body.get('task_id') and body['task_id'] != request['task_id'])
        or (event.correlation_id and event.correlation_id != request['workflow_run_id'])
    )


def diagnosis_reply_matches_request(event: ZfEvent, request: Mapping[str, Any]) -> bool:
    """The exact request and designated responder bind omitted reply identity."""
    body = event.payload or {}
    if (event.type != "run.manager.agent.recommendation"
            or not request.get("request_id") or not request.get("instance_id")
            or body.get("request_id") != request["request_id"]
            or event.actor != request["instance_id"]):
        return False
    for key in (*IDENTITY_FIELDS, "recovery_case_id", "fanout_id"):
        if request.get(key) and body.get(key) and body[key] != request[key]:
            return False
    expected_run = request.get("workflow_run_id") or request.get("run_id")
    if expected_run and any(value and value != expected_run for value in (
        body.get("workflow_run_id"), body.get("run_id"), event.correlation_id,
    )):
        return False
    return not (event.task_id and request.get("task_id")
                and event.task_id != request["task_id"])


def admitted_recovery_continuation(
    events: Iterable[ZfEvent], action: Mapping[str, Any], state: Mapping[str, Any],
) -> bool:
    """Consume a bound result of the current handoff, once.

    This does not authorize an action: the normal action policy, pause and
    precondition gates still run. It only distinguishes a successor from a
    second execution of an outstanding step. Old triage receipts are adapted
    by the projection, not by admitting particular successor action names.
    """
    expected = state.get("continuation_expected_events") or []
    result_id = str(action.get("recorded_event_id") or "")
    request_id = str(state.get("pending_request_id") or "")
    if not expected or not result_id or not request_id:
        return False
    if action.get("request_id") != request_id:
        return False
    if action.get("checkpoint_id") == state.get("pending_checkpoint_id"):
        return False
    bindings = state.get("step_bindings") or {}
    if any(str(action.get(key) or "") != str(value) for key, value in bindings.items()):
        return False
    pending_seen = False
    result: ZfEvent | None = None
    for event in events:
        if event.id == state.get("pending_event_id"):
            pending_seen = True
        if event.id == result_id and pending_seen:
            result = event
        if event.type in {"run.manager.action.planned", "run.manager.action.applied"}:
            payload = event.payload or {}
            if payload.get("recorded_event_id") == result_id:
                return False
    if result is None or result.type not in expected:
        return False
    body = result.payload or {}
    if body.get("request_id") != request_id:
        return False
    actual = {
        **body,
        "task_id": result.task_id or body.get("task_id") or "",
        "workflow_run_id": body.get("workflow_run_id") or body.get("run_id") or "",
    }
    # Legacy receipts can omit version fields, but cannot contradict them.
    # Their request identity is immutable; new producers bind result versions.
    for key, value in bindings.items():
        if actual.get(key):
            if str(actual.get(key) or "") != str(value):
                return False
    return True


def effect_bindings_match(event: ZfEvent, payload: Mapping[str, Any]) -> bool:
    """An explicitly declared binding requires evidence, including presence."""
    bindings = payload.get("effect_bindings")
    if bindings is None:
        return True  # Historical effects retain their existing scope contract.
    if not isinstance(bindings, Mapping):
        return False
    body = event.payload or {}
    return all(value not in (None, "") and body.get(key) == value for key, value in bindings.items())


def bind_recommendation_to_request(events, event: ZfEvent, payload: dict[str, Any]) -> dict[str, Any]:
    """Carry a resident's result through the same case instead of inventing one.

    Legacy unsolicited recommendations retain their existing policy admission.
    A reply to a durable diagnosis request must agree with its immutable scope.
    """
    request_id = str(payload.get("request_id") or "")
    if not request_id:
        return payload
    request = next((e for e in reversed(events) if e.type == "run.manager.action.effect.pending"
                    and e.payload.get("request_id") == request_id
                    and event.type in (e.payload.get("continuation_expected_events") or [])), None)
    if request is None:
        raise ValueError("recommendation_request_missing")
    delivery = next((e for e in events if e.type == "run.manager.resident.prompted"
                     and e.payload.get("request_id") == request_id), None)
    if delivery is None or event.actor != delivery.payload.get("instance_id"):
        raise ValueError("recommendation_responder_mismatch")
    bound = dict(payload)
    for key in (*IDENTITY_FIELDS, "recovery_case_id", "target_operation_id", "operation_precondition"):
        value = request.payload.get(key)
        if value:
            if bound.get(key) and bound[key] != value:
                raise ValueError(f"recommendation_binding_mismatch:{key}")
            bound[key] = value
    bound["checkpoint_id"] = f"recommendation:{event.id}"
    bound["recorded_event_id"] = event.id
    from zf.runtime.reader_diagnosis_scope import bind_reader_diagnosis_scope

    return bind_reader_diagnosis_scope(events, request, bound)


def recommendation_target_current(events, action: Mapping[str, Any]) -> bool:
    """A diagnosis of an old attempt cannot freeze its live successor."""
    target = str(action.get("target_operation_id") or "")
    attempt = str(action.get("active_attempt_id") or "")
    if not target and not attempt:
        return True
    from zf.runtime.orchestrator_agent_execution_policy import operation_bindings
    task_id = str(action.get("task_id") or "")
    run_id = str(action.get("workflow_run_id") or action.get("run_id") or "")
    current = operation_bindings(events, run_id, {task_id}).get(task_id, {})
    if not current:
        # Planning/readers precede the Task Pipeline. Compare that owner's
        # operation lineage, not unrelated parallel readers on the root Task.
        from zf.runtime.workflow_operation import reduce_workflow_operations
        rows = list(reduce_workflow_operations(events).values())
        bound = next((row for row in rows if
            row.get("workflow_run_id") == run_id and row.get("task_id") == task_id
            and (row.get("operation_id") == target if target else
                 row.get("active_attempt_id") == attempt)), None)
        if bound:
            current = next((row for row in reversed(rows) if all(
                row.get(key) == bound.get(key) for key in
                ("workflow_run_id", "task_id", "operation_type", "role_instance"))), {})
    if not current or (target and current.get("operation_id") != target):
        return False
    if attempt and current.get("active_attempt_id") != attempt:
        return False
    return current.get("status") not in {"superseded", "cancelled"}
