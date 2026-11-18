"""Resolve candidate Verify recovery against the frozen acceptance authority."""

from pathlib import Path

from zf.core.events import ZfEvent
from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_contract_snapshot import (
    descriptor_from_payload,
    hydrate_task_contract_snapshot,
    hydrate_target_snapshot,
    target_descriptor_from_payload,
)
from zf.runtime.verification_result import failed_acceptance_ids
from zf.runtime.workflow_operation import reduce_workflow_operations


def candidate_plan_scope(state_dir: Path, events: list[ZfEvent], result: dict) -> dict:
    """Return only implicated child Tasks; an unmapped AC requires planner scoping."""
    run_id = result.get("workflow_run_id")
    generation = result.get("task_map_generation")
    required = (
        "target_commit",
        "task_id",
        "contract_revision",
        "plan_artifact_package_digest",
        "contract_snapshot_digest",
        "target_snapshot_digest",
    )
    if not run_id or not generation or not all(result.get(key) for key in required):
        raise ValueError("candidate recovery identity incomplete")
    run_events = [
        event
        for event in events
        if (event.payload.get("workflow_run_id") or event.correlation_id) == run_id
        and event.origin == "kernel"
    ]
    current = next((e for e in reversed(run_events) if e.type == "candidate.ready"), None)
    if current is None or any(
        e.type
        in {
            "run.goal.completed",
            "run.goal.cancelled",
            "plan.artifact_package.admitted",
            "task_map.ready",
        }
        for e in run_events[run_events.index(current) + 1 :]
    ):
        raise ValueError("candidate recovery target superseded")
    trigger = current
    current = frozen_candidate_for_payload(run_events, run_id, payload={"candidate_event_id": trigger.id})
    if current is None:
        raise ValueError("candidate freeze receipt missing")
    if (
        current.payload.get("task_map_generation") != generation
        or current.payload.get("candidate_head_commit") != result["target_commit"]
        or current.payload.get("plan_artifact_package_digest") != result.get("plan_artifact_package_digest")
    ):
        raise ValueError("candidate recovery generation or target mismatch")
    if not current.payload.get("candidate_ref") or not current.payload.get("pdd_id"):
        raise ValueError("candidate recovery PDD or reference missing")
    expected = {
        "workflow_run_id": run_id,
        "task_map_generation": generation,
        "authority_scope": "candidate",
        "candidate_event_id": current.id,
        "task_id": result["task_id"],
        "contract_revision": result["contract_revision"],
    }
    contract = hydrate_task_contract_snapshot(
        state_dir,
        descriptor_from_payload(result),
        expected={
            **expected,
            "plan_artifact_package_digest": result["plan_artifact_package_digest"],
        },
    )
    hydrate_target_snapshot(
        state_dir,
        target_descriptor_from_payload(result),
        expected={
            **expected,
            "target_commit": result["target_commit"],
            "contract_snapshot_digest": result["contract_snapshot_digest"],
        },
    )
    sources = {
        row["acceptance_id"]: row.get("source_task_id")
        for row in contract["acceptance_criteria"]
        if row.get("source_acceptance_id")
    }
    affected = {sources.get(ac) for ac in failed_acceptance_ids(result)}
    known = set(contract.get("completed_task_ids") or [])
    return {
        "candidate_event_id": current.id,
        "trigger_event_id": trigger.id,
        "source_commit": result["target_commit"],
        "task_map_generation": generation,
        "plan_artifact_package_digest": result["plan_artifact_package_digest"],
        "candidate_ref": current.payload["candidate_ref"],
        "pdd_id": current.payload["pdd_id"],
        "anchor_task_id": result["task_id"],
        "failed_task_ids": sorted(affected) if affected <= known else [],
    }


def candidate_checkpoint_binding(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    failure_id: str,
    operation_id: str,
    run_id: str,
    task_id: str = "",
) -> dict:
    """Bind an operator's recovery request to a settled reader and its aggregate failure."""
    failure = next((e for e in events if e.id == failure_id), None)
    if not failure or failure.origin != "kernel" or failure.type not in {"test.failed", "verify.failed"}:
        raise ValueError("candidate aggregate failure missing")
    payload = failure.payload
    if payload.get("workflow_run_id") != run_id or not payload.get("fanout_id"):
        raise ValueError("candidate aggregate scope mismatch")
    earlier = events[: events.index(failure)]
    aggregate = next((e for e in reversed(earlier) if e.id == failure.causation_id), None)
    if (
        not aggregate
        or aggregate.type != "fanout.aggregate.completed"
        or aggregate.origin != "kernel"
        or aggregate.payload.get("fanout_id") != payload["fanout_id"]
        or aggregate.payload.get("status") != "failed"
    ):
        raise ValueError("candidate aggregate lineage mismatch")
    scope, _ = candidate_reader_binding(
        state_dir, events, fanout_id=payload["fanout_id"], operation_id=operation_id
    )
    if (
        (task_id and scope["anchor_task_id"] != task_id)
        or scope["workflow_run_id"] != run_id
        or scope["pdd_id"] != payload.get("pdd_id")
        or scope["task_map_generation"] != payload.get("task_map_generation")
        or scope["child_id"] not in (aggregate.payload.get("failed_children") or [])
        or not any(e.id == scope["child_event_id"] for e in earlier)
    ):
        raise ValueError("candidate failure and reader scope differ")
    if any(
        e.type == "orchestrator.replan_requested"
        and (e.payload.get("rework_of") == failure_id or e.payload.get("candidate_failure_id") == failure_id)
        for e in events
    ):
        raise ValueError("candidate failure already replanned")
    return scope


def candidate_reader_binding(
    state_dir: Path, events: list[ZfEvent], *, fanout_id: str, operation_id: str
) -> tuple[dict, dict]:
    """Locate the admitted control body and its exact trigger for either recovery entry."""
    op = reduce_workflow_operations(events).get(operation_id, {})
    if (
        op.get("operation_type") != "fanout_reader_child"
        or op.get("status") != "settled"
        or op.get("semantic_verdict") not in {"rejected", "blocked"}
    ):
        raise ValueError("candidate Verify operation stale")
    child = next(
        (
            e
            for e in reversed(events)
            if e.type == "fanout.child.failed"
            and e.origin == "kernel"
            and e.payload.get("fanout_id") == fanout_id
            and e.payload.get("operation_id") == operation_id
        ),
        None,
    )
    descriptor = op.get("admitted_control_result_ref")
    if (
        not child
        or not descriptor
        or child.payload.get("control_result_ref") != descriptor
        or child.payload.get("request_hash") != op.get("request_hash")
    ):
        raise ValueError("candidate failure lacks admitted reader result")
    result = hydrate_sidecar_ref(state_dir, descriptor).payload
    expected = {
        "operation_id": operation_id,
        "workflow_run_id": op.get("workflow_run_id"),
        "task_id": op.get("task_id"),
        "role_instance": op.get("role_instance"),
        "attempt_id": op.get("active_attempt_id"),
        "stage_id": child.payload.get("stage_id"),
        "request_hash": op.get("request_hash"),
        "verdict": op.get("semantic_verdict"),
        "execution_status": "completed",
        "output_profile_id": "candidate-verify",
    }
    if not isinstance(result, dict) or any(
        not val or result.get(key) != val for key, val in expected.items()
    ):
        raise ValueError("candidate Verify result binding mismatch")
    scope = candidate_plan_scope(state_dir, events, result)
    started = next(
        (
            e
            for e in reversed(events)
            if e.type == "fanout.started" and e.origin == "kernel" and e.payload.get("fanout_id") == fanout_id
        ),
        None,
    )
    if (
        not started
        or started.payload.get("trigger_event_id") != scope["trigger_event_id"]
        or started.payload.get("workflow_run_id") != op["workflow_run_id"]
    ):
        raise ValueError("candidate reader fanout target superseded")
    return {
        **scope,
        "workflow_run_id": op["workflow_run_id"],
        "role_instance": op["role_instance"],
        "attempt_id": op["active_attempt_id"],
        "stage_id": result["stage_id"],
        "child_id": child.payload["child_id"],
        "child_event_id": child.id,
        "control_result_ref": descriptor,
    }, result


def candidate_action_scope(state_dir: Path, events: list[ZfEvent], failure: ZfEvent, request: dict) -> dict:
    """Recheck a queued Plan action after the candidate may have advanced."""
    source = failure.payload
    for key in ("workflow_run_id", "pdd_id", "task_map_generation", "plan_artifact_package_digest"):
        if request.get(key) and request[key] != source.get(key):
            raise ValueError(f"candidate Plan request scope mismatch: {key}")
    run_id = source["workflow_run_id"]
    if request.get("trace_id") and request["trace_id"] != run_id:
        raise ValueError("candidate Plan request trace mismatch")
    operations = source.get("candidate_verify_operation_ids")
    if not isinstance(operations, list) or not operations:
        raise ValueError("candidate Plan request lacks reader operation")
    for operation_id in operations:
        scope = candidate_checkpoint_binding(
            state_dir,
            events,
            failure_id=failure.id,
            operation_id=operation_id,
            run_id=run_id,
        )
        if (
            scope["candidate_event_id"] != source["candidate_event_id"]
            or scope["source_commit"] != source["source_commit"]
        ):
            raise ValueError("candidate Plan request no longer current")
    return {
        **{
            key: source[key]
            for key in (
                "candidate_event_id",
                "source_commit",
                "failed_task_ids",
                "task_map_generation",
                "plan_artifact_package_digest",
                "workflow_run_id",
            )
        },
        "trace_id": run_id,
        "target_ref": scope["candidate_ref"],
    }
