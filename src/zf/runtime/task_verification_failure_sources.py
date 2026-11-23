"""Bound, read-only Task Verify evidence for semantic recovery and replan."""
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def task_verification_failure_sources(
    state_dir: Path, events: list[ZfEvent], failure_ids: set[str], *, task_id: str,
    workflow_run_id: str, package: Mapping[str, Any],
) -> list[dict[str, Any]]:
    failures = [
        event for event in events if event.id in failure_ids and event.origin == "kernel"
        and event.type == "workflow.call.result.admitted"
        and event.payload.get("semantic_verdict") == "rejected"
        and event.payload.get("control_result_schema") == "verification-result.v1"
    ]
    if not failures:
        return []
    operations = reduce_workflow_operations(events, workflow_run_id=workflow_run_id, task_id=task_id)
    sources = []
    for event in failures:
        payload = event.payload
        operation = operations.get(payload.get("operation_id"), {})
        if operation.get("task_pipeline_stage") != "verify":
            continue
        if (payload.get("admission_status") != "admitted" or operation.get("status") != "settled"
                or operation.get("call_result_admitted_event_id") != event.id
                or operation.get("request_hash") != payload.get("request_hash")
                or operation.get("dispatch_id") != payload.get("dispatch_id")
                or operation.get("active_attempt_id") != payload.get("attempt_id")
                or operation.get("task_map_generation") != package.get("task_map_generation")
                or operation.get("admitted_control_result_ref") != payload.get("control_result_ref")):
            raise ValueError("admitted verification operation binding mismatch")
        descriptor = payload.get("control_result_ref")
        if not isinstance(descriptor, Mapping):
            raise ValueError("admitted verification result missing")
        try:
            body = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
        except (ValueError, OSError) as exc:
            raise ValueError("admitted verification result unreadable") from exc
        if not isinstance(body, Mapping):
            raise ValueError("admitted verification result is not an object")
        if body.get("output_profile_id") != "task-verify":
            continue
        identity = {key: payload.get(key) for key in ("operation_id", "attempt_id", "dispatch_id", "request_hash")}
        identity.update(workflow_run_id=workflow_run_id, task_id=task_id,
                        task_map_generation=package.get("task_map_generation"),
                        plan_artifact_package_digest=package.get("package_digest"),
                        schema_version="verification-result.v1", verdict="rejected")
        if event.task_id != task_id or any(
            not value or body.get(key) != value for key, value in identity.items()
        ):
            raise ValueError("admitted verification result binding mismatch")
        sources.append({**descriptor, "source_id": f"task-verification-{event.id}"})
    return sources
