"""Mechanical predecessor and evidence bindings for recovery action steps."""

from collections.abc import Iterable, Mapping
from typing import Any

from zf.core.events.model import ZfEvent


IDENTITY_FIELDS = (
    "workflow_run_id", "task_id", "task_map_generation", "contract_revision",
    "target_commit", "active_attempt_id", "plan_artifact_package_digest",
)


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
    for key in (*IDENTITY_FIELDS, "recovery_case_id"):
        value = request.payload.get(key)
        if value:
            if bound.get(key) and bound[key] != value:
                raise ValueError(f"recommendation_binding_mismatch:{key}")
            bound[key] = value
    bound["checkpoint_id"] = f"recommendation:{event.id}"
    bound["recorded_event_id"] = event.id
    return bound
