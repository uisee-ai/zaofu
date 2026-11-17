"""Authenticated receipt of a pinned handoff, separate from business completion."""
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.artifact_read_ledger import seal_read_ledger, validate_required_reads
from zf.runtime.result_submit import SemanticResultSubmitService
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, verify_sidecar_ref
from zf.runtime.task_handoff import SOURCE_ID, validate_handoff_body
from zf.runtime.work_record import authorize_work_record


def _receipt_read_issues(state_dir, binding, descriptor, ledger):
    return validate_required_reads(state_dir, policy={"attempt_id": binding["attempt_id"], "required_reads": [{
        "source_id": SOURCE_ID, "artifact_id": SOURCE_ID, "artifact_sha256": descriptor["sha256"],
        "json_path": "$", "min_returned_bytes": 1, "allow_truncated": False,
    }]}, ledger_descriptor=ledger)


def accept_task_handoff(*, state_dir: Path, event_log, operation_id: str,
                        role: str, credential: str, digest: str) -> dict[str, Any]:
    import hashlib

    service = SemanticResultSubmitService(state_dir=state_dir, event_log=event_log, event_writer=EventWriter(event_log))
    key = hashlib.sha256(operation_id.encode()).hexdigest()
    with locked_path(state_dir / "task-handoff-receipts" / key):
        binding, manifest, _ = authorize_work_record(service, operation_id, role, credential)
        from zf.runtime.workflow_operation import load_workflow_operation

        operation = load_workflow_operation(event_log, operation_id) or {}
        binding["operation_lease_id"] = operation.get("lease_id", "")
        sources = [source for source in manifest.get("sources", []) if source.get("source_id") == SOURCE_ID]
        if len(sources) != 1 or sources[0].get("sha256") != digest:
            raise ValueError("handoff receipt requires the exact current source digest")
        source = sources[0]
        descriptor = {"ref": source["ref"], "sha256": digest, "kind": "task_handoff", "content_type": "application/json"}
        body = hydrate_sidecar_ref(state_dir, descriptor).payload
        target = {key: binding[key] for key in ("workflow_run_id", "task_id", "operation_id")}
        validate_handoff_body(body, target)
        for reference in body.get("references", []):
            verify_sidecar_ref(state_dir, reference)
        events = event_log.read_all()
        previous = [e for e in events if e.type == "task.handoff.accepted" and e.origin == "kernel"
                    and e.payload.get("binding") == binding and e.payload.get("handoff_digest") == digest]
        if previous:
            if _receipt_read_issues(state_dir, binding, descriptor, previous[-1].payload["read_ledger_ref"]):
                raise ValueError("handoff read proof is incomplete")
            return {"status": "accepted", "event_id": previous[-1].id, "replayed": True, "business_completed": False}
        ledger = seal_read_ledger(state_dir, binding["attempt_id"])
        if _receipt_read_issues(state_dir, binding, descriptor, ledger):
            raise ValueError("handoff body has not been completely read by this attempt")
        current_binding, _, _ = authorize_work_record(service, operation_id, role, credential)
        current_operation = load_workflow_operation(event_log, operation_id) or {}
        current_binding["operation_lease_id"] = current_operation.get("lease_id", "")
        if current_binding != binding:
            raise ValueError("handoff target changed during acknowledgement")
        event = service.event_writer.append(ZfEvent(type="task.handoff.accepted", origin="kernel", actor=role,
            task_id=binding["task_id"], correlation_id=binding["workflow_run_id"], payload={
                "binding": binding, "handoff_digest": digest, "handoff_ref": descriptor,
                "read_ledger_ref": ledger, "authorizes_execution": False, "business_completed": False,
            }))
        return {"status": "accepted", "event_id": event.id, "replayed": False, "business_completed": False}


def handoff_receipt_projection(state_dir: Path, events: list, task_id: str) -> dict[str, Any]:
    """Restart-safe read projection; latest prepared body never inherits an old ack."""
    from zf.runtime.workflow_operation import reduce_workflow_operations

    prepared = [e for e in events if e.type == "task.handoff.prepared" and e.origin == "kernel" and e.task_id == task_id]
    if not prepared:
        return {"status": "unavailable", "business_completed": False}
    latest = prepared[-1]
    descriptor = latest.payload.get("handoff_ref", {})
    result = {"status": "awaiting_receipt", "handoff_ref": descriptor, "business_completed": False,
              "target_operation_id": latest.payload.get("target", {}).get("operation_id", "")}
    try:
        body = hydrate_sidecar_ref(state_dir, descriptor).payload
        validate_handoff_body(body, latest.payload.get("target"))
        for reference in body.get("references", []):
            verify_sidecar_ref(state_dir, reference)
        operation = reduce_workflow_operations(events).get(result["target_operation_id"], {})
        result["delivery_status"] = "dispatched" if any(
            e.type == "task.pipeline.stage.dispatched" and e.origin == "kernel"
            and e.payload.get("operation_id") == operation.get("operation_id")
            and e.payload.get("attempt_id") == operation.get("active_attempt_id")
            and e.payload.get("dispatch_id") == operation.get("dispatch_id") for e in events
        ) else "not_dispatched"
        for event in reversed(events):
            if event.type != "task.handoff.accepted" or event.origin != "kernel" or event.task_id != task_id:
                continue
            binding = event.payload.get("binding", {})
            if (operation and event.payload.get("handoff_digest") == descriptor.get("sha256")
                    and binding.get("operation_id") == operation.get("operation_id")
                    and binding.get("attempt_id") == operation.get("active_attempt_id")
                    and binding.get("dispatch_id") == operation.get("dispatch_id")
                    and binding.get("actor") == operation.get("role_instance")
                    and binding.get("task_id") == task_id
                    and binding.get("workflow_run_id") == operation.get("workflow_run_id")
                    and binding.get("operation_lease_id") == operation.get("lease_id")):
                if _receipt_read_issues(state_dir, binding, descriptor, event.payload["read_ledger_ref"]):
                    raise ValueError("handoff read proof is incomplete")
                result.update(status="accepted", receipt_event_id=event.id)
                break
    except (OSError, ValueError, KeyError, TypeError):
        result["status"] = "incomplete"
    return result


def handoff_admission_issues(state_dir: Path, events: list, operation: dict) -> list[dict[str, str]]:
    """Only new requests with a pinned handoff require a matching acknowledgement."""
    from zf.runtime.workflow_operation import reduce_workflow_operations

    operation = reduce_workflow_operations(events).get(operation.get("operation_id"), operation)
    descriptor = operation.get("request_ref")
    if not isinstance(descriptor, dict):
        return []
    try:
        request = hydrate_sidecar_ref(state_dir, descriptor).payload.get("request", {})
        required = [row for row in request.get("required_reads", []) if row.get("source_id") == SOURCE_ID]
        if not required:
            return []
        receipt = handoff_receipt_projection(state_dir, events, str(operation.get("task_id") or ""))
        if (receipt["status"] == "accepted" and receipt["target_operation_id"] == operation.get("operation_id")
                and receipt["handoff_ref"].get("sha256") == required[0].get("artifact_sha256")):
            return []
        return [{"field": "handoff_receipt", "code": "handoff_receipt_required",
                 "message": "Read the complete task-handoff and run the current briefing's artifact handoff-accept command."}]
    except (OSError, ValueError, KeyError, TypeError):
        return [{"field": "handoff_receipt", "code": "handoff_evidence_unavailable"}]
