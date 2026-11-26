"""Operator-approved excerpts from one actual assistant reply in one provider turn."""
from zf.core.security.redaction import redact_text
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.run_admission import RUN_TERMINAL_EVENT_TYPES
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations

SCHEMA = "workflow-turn-excerpt.v1"
FIELDS = ("workflow_run_id", "operation_id", "attempt_id", "dispatch_id", "lease_id", "instance_id",
          "session_id", "turn_id")


def capture_public_reply(state, opened, text):
    identity = {k: opened.payload[k] for k in FIELDS}
    if opened.origin != "kernel" or not all(identity.values()) or opened.actor != identity["instance_id"]:
        raise ValueError("Unbound disclosure")
    public = redact_text(text)
    return write_immutable_json_sidecar(state, {"schema_version": SCHEMA, **identity, "task_id": opened.task_id,
        "opened_event_id": opened.id, "text": public[:8000], "excerpted": len(public) > 8000},
        root="workflow-turn-excerpts", kind="operator_assistant_reply", schema_version=SCHEMA,
        created_by="kernel", source_event_id=opened.id, required=False)


def current_reply(state, events, *, run, operation, attempt, role, read):
    kernel = [e for e in events if e.origin == "kernel"]
    row = reduce_workflow_operations(kernel).get(operation) or {}
    expected = {"workflow_run_id": run, "operation_id": operation, "attempt_id": attempt, "instance_id": role,
                "dispatch_id": row.get("dispatch_id"), "lease_id": row.get("lease_id")}
    if (row.get("workflow_run_id") != run or row.get("role_instance") != role or row.get("active_attempt_id") != attempt
            or row.get("status") != "running" or row.get("admitted_call_result_ref") or not all(expected.values())):
        return None
    def matches(event):
        return (event.actor == role and all(event.payload.get(k) == v for k, v in expected.items())
                and (event.payload.get("task_id") or "") == (row.get("task_id") or "")
                and (event.task_id or "") == (row.get("task_id") or ""))

    # An old operation may still look active after a crash, while this seat has moved on.
    owner = next((e for e in reversed(kernel) if e.type in {"workflow.operation.started","workflow.operation.retry_started"}
                  and e.payload.get("role_instance") == role), None)
    if not owner or (owner.payload.get("workflow_run_id"), owner.payload.get("operation_id"),
                     owner.payload.get("active_attempt_id")) != (run, operation, attempt):
        return None
    active_turn = next((e for e in reversed(kernel) if e.type == "provider.turn.opened" and e.actor == role), None)
    if not active_turn or not matches(active_turn):
        return None
    if any(e.type in RUN_TERMINAL_EVENT_TYPES and (e.payload.get("workflow_run_id") or e.correlation_id) == run for e in kernel):
        return None
    opened = {}
    latest = None
    for e in kernel:
        if e.type not in {"provider.turn.opened", "provider.turn.closed"} or not matches(e):
            continue
        key = (e.payload.get("session_id"), e.payload.get("turn_id"))
        if not all(key) or row.get("provider_session_id") and key[0] != row["provider_session_id"]:
            continue
        if e.payload.get("transport") != "stream-json":
            continue
        if e.type == "provider.turn.opened":
            opened[key] = e
        elif key in opened and e.payload.get("provider_status") == "completed" and e.payload.get("operator_excerpt_ref"):
            latest = (opened.pop(key), e)
    if not latest:
        return None
    start, end = latest
    ref = end.payload["operator_excerpt_ref"]
    if (not isinstance(ref, dict) or ref.get("kind") != "operator_assistant_reply" or
            ref.get("source_event_id") != start.id or ref.get("ref") != f"artifacts/workflow-turn-excerpts/{ref.get('sha256')}.json"):
        return None
    body = hydrate_sidecar_ref(state, ref).payload
    if (not isinstance(body, dict) or body.get("schema_version") != SCHEMA or body.get("opened_event_id") != start.id
            or (body.get("task_id") or "") != (row.get("task_id") or "")
            or any(body.get(k) != start.payload.get(k) for k in FIELDS) or not isinstance(body.get("text"), str)):
        return None
    return {"available": True, **({"text": body["text"], "excerpted": body["excerpted"],
             "event_id": end.id, "at": end.ts} if read else {})}
