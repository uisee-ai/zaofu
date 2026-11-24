"""One failed admission for a batch whose Readers share an invalid native freeze."""
from dataclasses import replace
from pathlib import Path
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.call_result_adapters import call_result_profile_identity
from zf.runtime.event_problem_registry import spec_for_event
from zf.runtime.scoped_candidate_verify import uses_scoped_candidate_verify


FAILURE_CLASS = "candidate_authority_invalid"
DIAGNOSIS = replace(
    spec_for_event("candidate.freeze.blocked"), event_type="fanout.cancelled", source="candidate_reader_registration",
    title="Candidate verification could not bind its frozen evidence",
)


def is_freeze_failure(event: ZfEvent) -> bool:
    p = event.payload
    return (event.type == "candidate.freeze.blocked" and event.origin == "kernel"
            and p.get("source") == "task_pipeline_freeze" and p.get("diagnosis_owner") == "run_manager"
            and p.get("failure_class") == FAILURE_CLASS and p.get("failure_scope") == "candidate"
            and p.get("failure_kind") == "infra" and bool(event.causation_id)
            and event.causation_id == p.get("generation_admitted_event_id")
            and all(p.get(k) for k in ("workflow_run_id", "generation_id", "task_map_generation",
                                      "plan_artifact_package_digest", "candidate_head_commit")))


def is_registration_failure(event: ZfEvent) -> bool:
    body = event.payload
    return (event.type == "fanout.cancelled" and event.origin == "kernel"
            and body.get("failure_class") == FAILURE_CLASS and body.get("failure_kind") == "infra"
            and body.get("failure_scope") == "candidate" and bool(body.get("fanout_id"))
            and bool(body.get("workflow_run_id")) and bool(body.get("candidate_snapshot_event_id")))


def authority_incident_resolved(events, source: ZfEvent) -> bool | None:
    """Retire a freeze on its matching native receipt; registration requires a downstream dispatch."""
    freezing = is_freeze_failure(source)
    if not freezing and not is_registration_failure(source):
        return None
    p = source.payload
    candidates: set[str] = {p["candidate_snapshot_event_id"]} if not freezing else set()
    fanouts: set[str] = set()
    for e in events[events.index(source) + 1:]:
        row = e.payload
        if e.origin != "kernel" or (row.get("workflow_run_id") or e.correlation_id) != p["workflow_run_id"]:
            continue
        if (e.type == "candidate.ready" and row.get("schema_version") == "candidate-freeze-receipt.v1"
                and not row.get("repaired_failure_event_id") and row.get("freeze_receipt_digest")
                and all(row.get(k) == p.get(k) for k in ("candidate_head_commit", "task_map_generation",
                                                         "plan_artifact_package_digest", "generation_id"))):
            if freezing:
                return True
            candidates.add(e.id)
        if not freezing and e.type == "candidate.ready" and row.get("candidate_snapshot_event_id") in candidates:
            candidates.add(e.id)
        if (e.type == "fanout.started" and row.get("stage_id") == p.get("stage_id")
                and row.get("trigger_event_id") in candidates):
            fanouts.add(row["fanout_id"])
        if e.type == "fanout.child.dispatched" and row.get("fanout_id") in fanouts:
            return True
    return False


def cancel_shared_registration(runtime: Any, context, started_id: str, exc: Exception) -> bool:
    """An input failure seen before any Reader registration can close the shared batch."""
    from zf.runtime.candidate_verification_authority import CandidateVerificationAuthorityError

    if not isinstance(exc, CandidateVerificationAuthorityError):
        return False
    events = runtime.event_log.read_all()
    by_id = {event.id: event for event in events}
    started = by_id.get(started_id)
    candidate = by_id.get(context.trigger_event_id)
    if (not started or started.type != "fanout.started" or started.origin != "kernel"
            or started.payload.get("fanout_id") != context.fanout_id
            or started.payload.get("trigger_event_id") != context.trigger_event_id
            or not candidate or candidate.type != "candidate.ready" or candidate.origin != "kernel"):
        return False
    body = candidate.payload
    run = body.get("workflow_run_id")
    if (body.get("schema_version") != "candidate-freeze-receipt.v1" or not run
            or not body.get("freeze_id") or not body.get("freeze_receipt_digest")
            or (started.payload.get("workflow_run_id") or started.correlation_id) != run
            or not context.expected_children or not started.payload.get("expected_children")):
        return False
    ids = {child.child_id for child in context.expected_children}
    declared = started.payload["expected_children"]
    if not isinstance(declared, list) or not all(isinstance(row, dict) for row in declared) or ids != {row.get("child_id") for row in declared}:
        return False
    kwargs = {"operation_type": "fanout_reader_child", "stage_id": context.stage_id}
    task = started.payload.get("task_id") or started.payload.get("parent_task_id") or context.expected_children[0].payload.get("task_id")
    if not task:
        return False
    for child in context.expected_children:
        payload = child.payload
        nested = payload.get("trigger_payload") or {}
        try:
            profile, _ = call_result_profile_identity(payload=payload, **kwargs)
        except ValueError:
            return False  # A malformed sibling must retain its own preparation failure.
        if (not isinstance(nested, dict) or payload.get("workflow_run_id") != run or payload.get("task_id") != task
                or nested.get("freeze_id") != body["freeze_id"]
                or child.target_ref and child.target_ref != body.get("candidate_ref")
                or any(value != expected for key, expected in (
                    ("candidate_event_id", candidate.id), ("candidate_snapshot_event_id", candidate.id),
                    ("freeze_id", body["freeze_id"]), ("candidate_head_commit", body.get("candidate_head_commit")),
                    ("target_commit", body.get("candidate_head_commit")))
                    for value in (payload.get(key), nested.get(key)) if value)
                or uses_scoped_candidate_verify(runtime.config, payload=payload, **kwargs)
                or profile != "candidate-verify"):
            return False
    from zf.core.state.locks import locked_path

    with locked_path(Path(runtime.state_dir) / "locks" / "candidate-registration-failure.lock"):
        events = runtime.event_log.read_all()
        for event in events[events.index(started) + 1:]:
            p = event.payload
            if p.get("fanout_id") != context.fanout_id and p.get("parent_fanout_id") != context.fanout_id:
                continue
            if is_registration_failure(event):
                return True
            if event.type in {"workflow.operation.requested", "fanout.child.dispatched", "fanout.child.completed",
                              "fanout.child.failed", "fanout.aggregate.completed", "fanout.cancelled"}:
                return False
        runtime.event_writer.append(ZfEvent(type="fanout.cancelled", actor="zf-cli", origin="kernel",
            causation_id=started.id, correlation_id=context.trace_id, payload={
                "workflow_run_id": run, "task_id": task, "fanout_id": context.fanout_id, "stage_id": context.stage_id,
                "trace_id": context.trace_id, "trigger_event_id": candidate.id, "candidate_snapshot_event_id": candidate.id,
                "candidate_head_commit": body.get("candidate_head_commit"), "freeze_id": body["freeze_id"],
                "freeze_receipt_digest": body["freeze_receipt_digest"], "task_map_generation": body.get("task_map_generation"),
                "failure_kind": "infra", "failure_scope": "candidate", "failure_class": FAILURE_CLASS,
                "plan_artifact_package_digest": body.get("plan_artifact_package_digest"), "generation_id": body.get("generation_id"),
                "recovery_owner": "run_manager", "reason": str(exc),
            }))
    return True


def obsolete_roster_failure(state_dir, events, aggregate, candidate) -> bool:
    """Bind the old preregistration signature to its digest-verified Plan and native freeze."""
    p = aggregate.payload
    if (aggregate.origin != "kernel" or p.get("failure_kind") != "infra" or not p.get("fanout_id")
            or candidate.origin != "kernel" or candidate.payload.get("schema_version") != "candidate-freeze-receipt.v1"):
        return False
    run = candidate.payload.get("workflow_run_id")
    if not run or (p.get("workflow_run_id") or aggregate.correlation_id) != run:
        return False
    starts = [e for e in events if e.type == "fanout.started" and e.origin == "kernel"
              and e.payload.get("fanout_id") == p["fanout_id"] and e.payload.get("trigger_event_id") == candidate.id
              and e.payload.get("workflow_run_id") == run and e.payload.get("stage_id") == p.get("stage_id")]
    if len(starts) != 1:
        return False
    start, = starts
    children = start.payload.get("expected_children")
    failed = p.get("failed_children")
    if (not isinstance(children, list) or not isinstance(failed, list)
            or not all(isinstance(row, dict) and isinstance(row.get("child_id"), str) for row in children)
            or not all(isinstance(value, str) for value in failed)):
        return False
    expected = {row["child_id"] for row in children}
    if not expected or "" in expected or len(expected) != len(children) or expected != set(failed):
        return False
    failures = [e for e in events if e.type == "fanout.child.failed" and e.payload.get("fanout_id") == p["fanout_id"]]
    unanimous = (len(failures) == len(expected) and {e.payload.get("child_id") for e in failures} == expected
            and len({e.payload.get("reason") for e in failures}) == 1
            and all(e.origin == "kernel" and e.causation_id == start.id and e.payload.get("reason")
                    and e.payload.get("failure_kind") == "infra" and e.payload.get("failure_scope") == "operation_preregistration"
                    for e in failures))
    if not unanimous:
        return False
    from zf.runtime.sidecar_refs import SidecarRefError, hydrate_sidecar_ref
    from zf.runtime.task_map_successor import task_map_supersedes_task_ids

    body = candidate.payload
    freeze_ref = body.get("freeze_receipt_ref") or {}
    if not isinstance(freeze_ref, dict) or not freeze_ref.get("sha256") or body.get("freeze_receipt_digest") != freeze_ref["sha256"]:
        return False
    try:
        frozen = hydrate_sidecar_ref(state_dir, freeze_ref)
        task_map = hydrate_sidecar_ref(state_dir, {"ref": body.get("task_map_ref"), "sha256": body.get("task_map_digest")})
    except (OSError, SidecarRefError):
        return False
    if not frozen.ok or not isinstance(frozen.payload, dict) or any(
        frozen.payload.get(k) != body.get(k) for k in ("completed_task_ids", "workflow_run_id",
            "task_map_generation", "freeze_id", "task_map_ref", "task_map_digest")
    ):
        return False
    if not task_map.ok or not isinstance(task_map.payload, dict):
        return False
    rows = task_map.payload.get("tasks") or []
    completed_ids = body.get("completed_task_ids")
    amend = task_map.payload.get("amend")
    if (not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get("task_id"), str) for row in rows)
            or not isinstance(completed_ids, list) or not all(isinstance(value, str) for value in completed_ids)
            or not isinstance(amend, dict) or not isinstance(amend.get("superseded_task_ids"), list)
            or not all(isinstance(value, str) for value in amend["superseded_task_ids"])):
        return False
    completed = set(completed_ids)
    replaced = {old for row in rows if row.get("task_id") in completed for old in task_map_supersedes_task_ids(row)}
    absent = completed.difference(row.get("task_id") for row in rows)
    return any(failures[0].payload["reason"] == "durable operation preregistration failed: "
               "retained Task missing or duplicated in Plan: " + old
               for old in absent.intersection(replaced, amend["superseded_task_ids"]))
