"""Controlled Candidate continuation: review preview, then create one invoke."""
from __future__ import annotations

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_continuation import ACTION, SCHEMA, preview


def execute_action(service, *, requested, action, requested_action, payload):
    try:
        with locked_path(service.state_dir / "locks" / "candidate-continuation"):
            result = _execute(service, requested, payload)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return service._failed(requested=requested, action=action, requested_action=requested_action,
            task_id=None, reason=str(exc), status_code=409, status="candidate_continuation_blocked")
    service._completed(requested=requested, event=requested, action=action, requested_action=requested_action,
        task_id=None, status=result["status"], extra={"workflow_run_id": result.get("workflow_run_id", "")})
    return {"ok": True, "action": ACTION, **result}


def _execute(service, requested, payload):
    manifest = preview(service, source_run_id=str(payload.get("source_run_id") or ""),
                       candidate_event_id=str(payload.get("candidate_event_id") or ""))
    if payload.get("dry_run") is True:
        return {"status": "preview", "manifest": manifest}
    reviewed = str(payload.get("reviewed_digest") or "")
    approval = str(payload.get("approval_ref") or "").strip()
    if reviewed != manifest["manifest_digest"] or not approval:
        raise ValueError("exact reviewed_digest and explicit approval_ref are required")
    run_id = "continuation-" + reviewed[:24]
    events = service.writer.event_log.read_all()
    prior = next((e for e in events if e.type == "workflow.invoke.requested"
                  and e.payload.get("workflow_run_id") == run_id), None)
    if prior:
        return {"status": "already_requested", "workflow_run_id": run_id, "event_id": prior.id}
    # Durable receipt precedes invoke; replay after a crash uses the same body.
    old_receipt = next((e for e in events if e.type == "runtime.action.attempt.completed"
                       and e.payload.get("action") == ACTION
                       and e.payload.get("manifest_digest") == reviewed), None)
    if old_receipt:
        descriptor = old_receipt.payload["receipt_ref"]
    else:
        descriptor = write_immutable_json_sidecar(service.state_dir,
            {"schema_version": SCHEMA, "workflow_run_id": run_id, "manifest": manifest,
             "approval_ref": approval, "request_event_id": requested.id},
            root="candidate-continuations", kind="candidate_continuation",
            schema_version=SCHEMA, created_by=service.actor, source_event_id=requested.id)
        service.writer.append(ZfEvent(type="runtime.action.attempt.completed", actor=service.actor,
            origin="kernel", causation_id=requested.id, correlation_id=run_id,
            payload={"action": ACTION, "manifest_digest": reviewed, "receipt_ref": descriptor,
                     "workflow_run_id": run_id, "status": "authorized", "approval_ref": approval}))
    source_invoke = next(e for e in events if e.id == manifest["source_invoke_event_id"])
    source = source_invoke.payload
    inherited = {k: source[k] for k in (
        "flow_kind", "request_kind", "kind", "requirement_spec_ref", "requirement_spec_digest",
        "completion_profile", "source_refs", "run_contract_ref", "run_contract_digest",
        "workflow_input_manifest_ref", "effective_config_ref", "effective_config_digest",
    ) if k in source}
    task_id = "CONT-" + reviewed[:24].upper()
    event = service.writer.append(ZfEvent(type="workflow.invoke.requested", actor=service.actor,
        origin="kernel", task_id=task_id, causation_id=requested.id, correlation_id=run_id,
        payload={**inherited, "workflow_run_id": run_id, "run_id": run_id, "request_id": run_id,
                 "goal_id": run_id, "task_id": task_id, "pdd_id": task_id, "feature_id": task_id,
                 "pattern_id": manifest["candidate_reader_stage_ids"][0],
                 "candidate_continuation_ref": descriptor,
                 "source": ACTION, "requested_by": service.actor}))
    return {"status": "requested", "workflow_run_id": run_id, "event_id": event.id,
            "receipt_ref": descriptor}
