"""Recover the exact admitted producer when its independent diagnosis review was never issued."""

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.plan_candidate_owner_review import bound_candidate_diagnosis
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash, reduce_workflow_operations


def synth_call_issued(state: Path, events, fanout_id: str, run: str, stage: str) -> bool:
    """A registered but not-yet-dispatched call belongs to its original Critic."""
    for event in events:
        p = event.payload
        if event.type == "fanout.synth.dispatched" and p.get("fanout_id") == fanout_id:
            return True
        if (
            event.type == "workflow.operation.requested"
            and p.get("operation_type") == "fanout_synth"
            and p.get("workflow_run_id") == run
            and p.get("parent_stage_id") == stage
        ):
            request = hydrate_sidecar_ref(state, p["request_ref"]).payload["request"]
            if not request.get("fanout_id") or request["fanout_id"] == fanout_id:
                return True
    return False


def admitted_unissued_planner(
    state: Path,
    events: list[ZfEvent],
    *,
    fanout_id: str,
    trigger_event_id: str,
) -> dict[str, Any] | None:
    """Only a signed, settled diagnosis for this frozen fanout may wait for its first Critic."""
    by_id = {e.id: e for e in events}
    trigger = by_id.get(trigger_event_id)
    if not fanout_id or trigger is None or not bound_candidate_diagnosis(trigger.payload):
        return None
    run = trigger.payload["workflow_run_id"]
    frozen = frozen_candidate_for_payload(events, run, payload={})
    if (
        not frozen
        or frozen.id != trigger.payload["candidate_event_id"]
        or frozen.payload.get("candidate_head_commit") != trigger.payload["source_commit"]
    ):
        return None
    starts = [e for e in events if e.type == "fanout.started" and e.payload.get("fanout_id") == fanout_id]
    if (
        len(starts) != 1
        or starts[0].origin != "kernel"
        or starts[0].actor != "zf-cli"
        or starts[0].causation_id != trigger.id
        or (starts[0].payload.get("workflow_run_id") or starts[0].correlation_id) != run
    ):
        return None
    start = starts[0]
    history = events[events.index(start) + 1 :]
    children = [
        e
        for e in history
        if e.type in {"fanout.child.completed", "fanout.child.failed"}
        and e.payload.get("fanout_id") == fanout_id
    ]
    if len(children) != 1 or children[0].type != "fanout.child.completed" or children[0].origin != "kernel":
        return None
    child = children[0]
    ops = reduce_workflow_operations(history, workflow_run_id=run)
    planner = ops.get(child.payload.get("operation_id"), {})
    if (
        planner.get("status") != "settled"
        or planner.get("operation_type") != "fanout_reader_child"
        or planner.get("attempt_domain") != "plan"
    ):
        return None
    admitted = by_id.get(planner.get("call_result_admitted_event_id"))
    if (
        admitted is None
        or admitted.type != "workflow.call.result.admitted"
        or admitted.origin != "kernel"
        or admitted.payload.get("mode") != "blocking"
        or admitted.payload.get("admission_status") != "admitted"
        or admitted.payload.get("source_event_id") != child.causation_id
        or any(
            not child.payload.get(k) or child.payload[k] != admitted.payload.get(k)
            for k in ("workflow_run_id", "operation_id", "request_hash", "attempt_id", "control_result_ref")
        )
        or child.payload["request_hash"] != planner.get("request_hash")
    ):
        return None
    if synth_call_issued(state, history, fanout_id, run, start.payload.get("stage_id")):
        return None
    signed = hydrate_sidecar_ref(state, planner["request_ref"]).payload
    req = signed["request"]
    pinned = (req.get("plan_candidate_validation") or {}).get("manifest") or {}
    if (
        operation_request_hash(signed) != planner["request_hash"]
        or req.get("fanout_id") != fanout_id
        or req.get("role_instance") != child.payload.get("role_instance")
        or req.get("child_id") != child.payload.get("child_id")
        or req.get("stage_id") != start.payload.get("stage_id")
        or req.get("active_attempt_id") != child.payload["attempt_id"]
        or (pinned.get("trigger_payload") or {}).get("candidate_event_id") != frozen.id
    ):
        return None
    ref = admitted.payload["control_result_ref"]
    body = hydrate_sidecar_ref(state, ref).payload
    if (
        body.get("candidate_kind") != "diagnosis_review"
        or body.get("execution_status") != "completed"
        or body.get("result_semantics") != "artifact_production"
        or body.get("target_commit") != trigger.payload["source_commit"]
        or any(
            body.get(k) != child.payload.get(k)
            for k in ("operation_id", "request_hash", "attempt_id", "fanout_id", "role_instance")
        )
        or any(
            body.get(k) != trigger.payload.get(k)
            for k in ("plan_artifact_package_digest", "task_map_generation", "operator_feedback")
        )
    ):
        return None
    return {
        "planner_admitted_event_id": admitted.id,
        "planner_result_ref": ref,
        "planner_request_hash": planner["request_hash"],
    }


def handoff_failure_details(runtime, manifest: dict) -> dict:
    """A local prerequisite error has no semantic verdict and grants no Writer or retry."""
    if not bound_candidate_diagnosis(manifest.get("trigger_payload")):
        return {}
    try:
        binding = admitted_unissued_planner(
            runtime.state_dir,
            runtime.event_log.read_all(),
            fanout_id=manifest["fanout_id"],
            trigger_event_id=manifest["trigger_event_id"],
        )
    except (ValueError, OSError, KeyError, TypeError):
        return {}
    if not binding:
        return {}
    return {
        **binding,
        "failure_scope": "diagnosis_critic_handoff",
        "failure_class": "reader_execution_failure",
        "failure_kind": "infra",
        "attempt_domain": "protocol_repair",
        "semantic_attempt_consumed": False,
        "semantic_rework_cost": 0,
    }


def is_handoff_failure(event: ZfEvent) -> bool:
    p = event.payload
    return (
        event.origin == "kernel"
        and event.actor == "zf-cli"
        and p.get("failure_scope") == "diagnosis_critic_handoff"
        and not p.get("operation_id")
        and p.get("failure_class") == "reader_execution_failure"
        and p.get("failure_kind") == "infra"
        and p.get("semantic_attempt_consumed") is False
        and bool(
            p.get("planner_admitted_event_id")
            and p.get("planner_request_hash")
            and p.get("planner_result_ref")
        )
    )


def prepare_critic_handoff(runtime, manifest: dict, reports: list, *, run_id: str, role: str) -> dict | None:
    """Build the original review inputs or record a proven pre-WRC infrastructure failure."""
    from zf.runtime.plan_critic_reassessment import validate_reassessment_inputs, review_checkpoint_id
    from zf.runtime.plan_review_input_budget import PlanReviewInputBudgetExceeded
    from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload

    try:
        payload = build_plan_synth_call_payload(
            state_dir=runtime.state_dir,
            project_root=runtime.project_root,
            event_log=runtime.event_log,
            manifest=manifest,
            reports=reports,
            run_id=run_id,
            role_instance=role,
        )
        validate_reassessment_inputs(runtime.state_dir, manifest, payload)
        return payload
    except PlanReviewInputBudgetExceeded:
        raise
    except (ValueError, OSError) as exc:
        details = handoff_failure_details(runtime, manifest)
        if not details:
            raise
        trace_id = manifest.get("trace_id") or manifest.get("workflow_run_id") or manifest.get("fanout_id")
        failed = runtime.event_writer.append(
            ZfEvent(
                type="fanout.synth.completed",
                actor="zf-cli",
                origin="kernel",
                causation_id=manifest["trigger_event_id"],
                correlation_id=trace_id,
                payload={
                    "fanout_id": manifest["fanout_id"],
                    "trace_id": trace_id,
                    "stage_id": manifest["stage_id"],
                    "run_id": run_id,
                    "role_instance": role,
                    "status": "failed",
                    "recommendation": "abstain",
                    "summary": str(exc),
                    "review_request_event_id": review_checkpoint_id(manifest),
                    **details,
                },
            )
        )
        runtime._finalize_fanout_synth(failed)
        return None
