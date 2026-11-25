"""Replay an admitted task-ref correction against its original negative handoff."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from zf.core.events.model import ZfEvent


def _same(left: Mapping, right: Mapping, *keys: str) -> bool:
    return all(left.get(k) and left[k] == right.get(k) for k in keys)


def _dispatched_feedback(dispatch: ZfEvent, blocking: ZfEvent, source_id: str) -> bool:
    return any(
        isinstance(item, dict) and item.get("blocking_event_id") == blocking.id
        and item.get("source_event_id") == source_id
        for item in dispatch.payload.get("rework_feedback") or []
    )


def _admitted_dispatch(
    result: ZfEvent, stage: str, admissions: Mapping[str, ZfEvent], dispatches: Mapping[str, ZfEvent],
) -> ZfEvent | None:
    p = result.payload
    admission = admissions.get(p.get("semantic_submit_admission_event_id"))
    dispatch = dispatches.get(p.get("dispatch_id"))
    if not admission or not dispatch:
        return None
    a, d = admission.payload, dispatch.payload
    identity = ("workflow_run_id", "operation_id", "request_hash", "attempt_id", "dispatch_id")
    schema = "implementation-result.v1" if stage == "impl" else "verification-result.v1"
    if (a.get("source_event_id") == result.id
            and a.get("admission_status") == "admitted" and a.get("semantic_verdict") == "passed"
            and a.get("mode") == "blocking" and a.get("control_result_schema") == schema
            and _same(p, a, *identity, "control_result_ref")
            and p.get("task_pipeline_stage") == stage and result.actor == d.get("role_instance")
            and _same(p, d, *identity, "task_id", "task_pipeline_stage", "operation_generation",
                      "pipeline_key", "task_map_generation", "contract_snapshot_digest", "task_ref")):
        return dispatch
    return None


def _verified_freeze(
    by_id: Mapping[str, ZfEvent], publication: ZfEvent, head: str, handoff: Mapping,
) -> ZfEvent | None:
    aggregate = by_id.get(publication.causation_id)
    p = publication.payload
    if (publication.type not in {"test.passed", "verify.passed"} or publication.origin != "kernel"
            or not aggregate or aggregate.type != "fanout.aggregate.completed" or aggregate.origin != "kernel"
            or aggregate.payload.get("status") != "completed" or p.get("status") != "completed"
            or aggregate.payload.get("success_event") != publication.type
            or not _same(p, aggregate.payload, "fanout_id", "workflow_run_id", "task_map_generation")
            or not _same(p, handoff, "workflow_run_id", "task_map_generation")):
        return None
    start = next((e for e in reversed(by_id.values()) if e.type == "fanout.started" and e.origin == "kernel"
                  and _same(p, e.payload, "fanout_id", "workflow_run_id", "target_ref")), None)
    freeze = by_id.get(start.causation_id) if start else None
    if (not freeze or freeze.type != "candidate.ready" or freeze.origin != "kernel"
            or start.payload.get("trigger_event_id") != freeze.id
            or freeze.payload.get("candidate_head_commit") != head
            or freeze.payload.get("candidate_ref") != p["target_ref"]
            or not _same(p, freeze.payload, "workflow_run_id", "task_map_generation")
            or handoff["task_id"] not in (freeze.payload.get("completed_task_ids") or [])):
        return None
    latest = next(e for e in reversed(by_id.values()) if e.type == "candidate.ready" and e.origin == "kernel"
                  and _same(e.payload, freeze.payload, "workflow_run_id", "candidate_ref")
                  and e.payload.get("candidate_head_commit"))
    if not _same(latest.payload, freeze.payload, "candidate_head_commit", "task_map_generation"):
        return None
    if latest.payload.get("freeze_id") != freeze.payload.get("freeze_id"):
        return None
    return freeze


def verified_task_ref_correction(
    by_id: dict[str, ZfEvent], handoff: Mapping[str, Any], publication: ZfEvent,
    candidate_head: str, integrated: set[str],
) -> dict[str, str] | None:
    """Require both typed feedback hops, both task refs and an independent admitted Verify."""
    original = by_id.get(handoff["last_event_id"])
    request = by_id.get(handoff["request_event_id"])
    if (not integrated or not request or request.type != "task.rework.requested" or request.origin != "kernel"
            or request.payload.get("source") != "task_pipeline_operator_retry"
            or not original or original.type != "dev.build.done" or not request.payload.get("source_event_id")
            or not _same(original.payload, handoff, "task_id", "dispatch_id", "workflow_run_id", "task_map_generation")
            or original.payload.get("source_commit") != handoff["target_commit"]):
        return None
    freeze = _verified_freeze(by_id, publication, candidate_head, handoff)
    if not freeze:
        return None
    dispatches: dict[str, ZfEvent] = {}
    admissions: dict[str, ZfEvent] = {}
    rejected = corrected = updated = verified = None
    feedback_ref = None
    stable = ("workflow_run_id", "task_map_generation", "pipeline_key", "task_ref",
              "source_branch", "contract_snapshot_digest", "contract_revision")
    for event in by_id.values():
        if event.id == freeze.id:
            break
        if event.task_id != handoff["task_id"] or event.correlation_id != handoff["workflow_run_id"]:
            continue
        p = event.payload
        if event.type == "workflow.call.result.admitted" and event.origin == "kernel":
            admissions[event.id] = event
        elif event.type == "task.dispatched" and event.origin == "kernel":
            dispatches[p.get("dispatch_id")] = event
        elif event.id == original.id:
            dispatch = _admitted_dispatch(original, "impl", admissions, dispatches)
            if not dispatch or not _dispatched_feedback(dispatch, request, request.payload["source_event_id"]):
                return None
            feedback_ref = dispatch.payload.get("candidate_repair_feedback_ref")
            if (not isinstance(feedback_ref, dict) or not feedback_ref.get("sha256")
                    or not feedback_ref.get("ref")):
                return None
        elif event.type == "task.ref.rejected" and feedback_ref and event.origin == "kernel":
            if (event.causation_id == original.id and p.get("trigger_event_id") == original.id
                    and _same(p, original.payload, "source_commit", "source_branch", "contract_snapshot_digest")):
                rejected = event
        elif event.type == "dev.build.done" and rejected:
            dispatch = _admitted_dispatch(event, "impl", admissions, dispatches)
            if (dispatch and _dispatched_feedback(dispatch, rejected, original.id)
                    and _same(p, original.payload, *stable)
                    and dispatch.payload.get("candidate_repair_feedback_ref") == feedback_ref
                    and p.get("source_commit") in integrated and p["source_commit"] != handoff["target_commit"]):
                corrected, updated, verified = event, None, None
        elif event.type == "task.ref.updated" and corrected and event.origin == "kernel":
            updated = verified = None
            if (event.causation_id == corrected.id and p.get("trigger_event_id") == corrected.id
                    and _same(p, corrected.payload, "source_commit", "source_branch", "task_ref")):
                updated = event
        elif event.type == "task.pipeline.verify.completed" and updated:
            dispatch = _admitted_dispatch(event, "verify", admissions, dispatches)
            if (dispatch and event.actor != corrected.actor and event.actor != original.actor
                    and p.get("target_commit") == corrected.payload["source_commit"]
                    and _same(p, corrected.payload, *stable, "operation_generation")
                    and dispatch.payload.get("candidate_repair_feedback_ref") == feedback_ref):
                verified = event
        elif event.type == "task.pipeline.verify.failed" and corrected and _same(p, corrected.payload, *stable):
            verified = None
    if not verified:
        return None
    return {
        "reported_target_commit": handoff["target_commit"], "original_result_event_id": original.id,
        "rejection_event_id": rejected.id, "corrected_result_event_id": corrected.id,
        "updated_ref_event_id": updated.id, "task_verify_event_id": verified.id,
        "target_commit": verified.payload["target_commit"],
    }
