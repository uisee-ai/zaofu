"""Current Candidate integration failures owned by resident Run Manager diagnosis."""

import hashlib
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.workflow_operation import reduce_workflow_operations


def integration_review_sources(
    events: list[ZfEvent],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Keep exact unresolved operations; do not retire them on unrelated progress."""
    operations = reduce_workflow_operations(events)
    contexts = task_pipeline_generation_contexts(events)
    current: dict[str, dict[str, Any]] = {}
    covered: set[str] = set()
    for event in events:
        if event.type != "integration.queue.needs_review" or event.origin != "kernel":
            continue
        payload = event.payload
        op = operations.get(str(payload.get("operation_id") or ""), {})
        task_id = str(event.task_id or "")
        if (not task_id or op.get("task_pipeline_stage") != "integration"
                or not op.get("request_hash")
                or any(payload.get(k) != op.get(k) for k in (
                    "task_id", "workflow_run_id", "task_map_generation", "operation_generation",
                )) or op.get("task_id") != task_id):
            continue
        covered.add(str(op["operation_id"]))
        context = contexts.get(task_id, {})
        if (op.get("status") != "blocked" or not context
                or any(op.get(k) != context.get(k) for k in (
                    "workflow_run_id", "task_map_generation",
                ))):
            continue
        peers = [row for row in operations.values()
                 if row.get("task_id") == task_id
                 and row.get("workflow_run_id") == op.get("workflow_run_id")
                 and row.get("task_map_generation") == op.get("task_map_generation")]
        if any(row.get("status") in {"requested", "reserved", "running", "suspended"}
               or int(row.get("operation_generation") or 1) > int(op.get("operation_generation") or 1)
               for row in peers):
            continue
        current[event.id] = op
    return current, covered


def bind_integration_review_action(
    action: dict[str, Any], event: ZfEvent, operation: dict[str, Any],
) -> dict[str, Any]:
    fingerprint = "integration-review:" + str(operation["operation_id"]) + ":" + str(operation["request_hash"])
    return {
        **action,
        "checkpoint_id": "integration-diagnosis-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:24],
        "fingerprint": fingerprint,
        "workflow_run_id": operation["workflow_run_id"],
        "task_map_generation": operation["task_map_generation"],
        "target_operation_id": operation["operation_id"],
        "target_request_hash": operation["request_hash"],
        "source_event_id": event.id,
        "source_event_type": event.type,
        "problem_class": "integration",
        "recommended_actions": [
            "inspect_exact_integration_failure_and_preserved_evidence",
            "classify_environment_failure_source_repair_or_dependency_gap",
            "recommend_bound_controlled_retry_or_in_scope_impl_rework",
            "preserve_verification_and_candidate_admission_gates",
        ],
        "expected_output": [
            "diagnosis_report", "recommended_run_manager_action", "evidence_refs",
            "expected_downstream_event",
        ],
    }
