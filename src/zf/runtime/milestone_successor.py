"""Build the successor's own checkpoint from an authorized candidate frontier."""

from __future__ import annotations

from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.milestone_handoff import milestone_readiness
from zf.runtime.orchestrator_agent_role import orchestrator_instance_id
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations


def current_candidate(events, run):
    return next(
        (
            e
            for e in reversed(events)
            if e.type == "candidate.ready"
            and e.origin == "kernel"
            and e.payload.get("workflow_run_id") == run
            and e.payload.get("freeze_receipt_ref")
        ),
        None,
    )


def successor_context(
    runtime, *, checkpoint, run, operation, attempt, source_id, payload
):
    metadata = flow_metadata_for(runtime.config, payload=payload)
    if (
        checkpoint not in {"stage_barrier", "pre_closeout"}
        or metadata.get("milestone_handoff") is not True
    ):
        return {}
    events = runtime.event_log.read_all()
    end = next((i for i, e in enumerate(events) if e.id == source_id), None)
    if end is None:
        return {}
    history = events[: end + 1]
    candidate = current_candidate(history, run)
    if not candidate or candidate.payload.get("task_map_generation") != payload.get(
        "task_map_generation"
    ):
        return {}
    target = candidate.payload["target_commit"]
    if payload.get("target_commit") and payload["target_commit"] != target:
        return {}
    reviews = []
    for op in reduce_workflow_operations(history).values():
        if (
            op.get("workflow_run_id") != run
            or not op.get("request_ref")
            or op.get("operation_type") != "fanout_reader_child"
        ):
            continue
        request = hydrate_sidecar_ref(runtime.state_dir, op["request_ref"]).payload[
            "request"
        ]
        ref = request.get("milestone_handoff_ref")
        if not ref:
            continue
        handoff = hydrate_sidecar_ref(runtime.state_dir, ref).payload
        if (
            handoff["candidate_event_id"] != candidate.id
            or handoff["binding"]["target_commit"] != target
        ):
            continue
        reviews.append(
            {
                "source": ref,
                "consumer": handoff["consumer"],
                "contract": handoff["binding"]["contract_snapshot_digest"],
                "members": handoff["members"],
                "member_checkpoints": handoff["member_checkpoints"],
                "readiness": milestone_readiness(runtime.state_dir, history, handoff),
            }
        )
    if not reviews:
        return {}
    body = {
        "schema_version": "milestone-successor.v1",
        "workflow_run_id": run,
        "operation_id": operation,
        "attempt_id": attempt,
        "source_event_id": source_id,
        "candidate_event_id": candidate.id,
        "target_commit": target,
        "freeze_receipt_ref": candidate.payload["freeze_receipt_ref"],
        "task_map_generation": candidate.payload["task_map_generation"],
        "session_mode": "fresh",
        "reviews": reviews,
        "next_owner": orchestrator_instance_id(runtime),
        "authorizes_execution": False,
    }
    return write_immutable_json_sidecar(
        runtime.state_dir,
        body,
        root="milestone-successors",
        kind="milestone_successor",
        schema_version=body["schema_version"],
        created_by="kernel",
        source_event_id=source_id,
    )


def successor_is_current(runtime, ref, run):
    handoff = hydrate_sidecar_ref(runtime.state_dir, ref).payload
    current = current_candidate(runtime.event_log.read_all(), run)
    return bool(
        current
        and handoff["workflow_run_id"] == run
        and current.id == handoff["candidate_event_id"]
        and current.payload.get("target_commit") == handoff["target_commit"]
    )
