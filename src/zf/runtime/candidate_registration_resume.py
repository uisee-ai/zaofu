"""Owner-controlled retry of one untouched Candidate Reader roster on its original Run."""

from dataclasses import replace
from types import SimpleNamespace

from zf.core.state.locks import locked_path
from zf.core.task.store import TaskStore
from zf.runtime.call_result_adapters import call_result_profile_identity
from zf.runtime.candidate_continuation import _git_commit
from zf.runtime.candidate_registration_failure import is_registration_failure
from zf.runtime.candidate_verification_authority import (
    frozen_candidate_for_payload,
    prepare_candidate_verification_authority,
)
from zf.runtime.pause_lifecycle import is_run_dispatch_paused
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.scoped_candidate_verify import uses_scoped_candidate_verify


def _native_and_start(events, failure_id):
    by_id = {event.id: event for event in events}
    failed = by_id.get(failure_id)
    if (
        failed is None
        or not is_registration_failure(failed)
        or failed.actor != "zf-cli"
        or failed.payload.get("recovery_owner") != "run_manager"
    ):
        raise ValueError("not a kernel Candidate preregistration cancellation")
    p = failed.payload
    run = p["workflow_run_id"]
    admission = build_run_admission_projection(events).runs.get(run)
    admitted = [
        e
        for e in events
        if e.type == "run.admission.admitted"
        and e.origin == "kernel"
        and e.actor == "orchestrator"
        and e.payload.get("workflow_run_id") == run
    ]
    if len(admitted) != 1 or not admission or admission.status != "running" or is_run_dispatch_paused(events, run):
        raise ValueError("registration retry requires the original running, unpaused Run")
    native = frozen_candidate_for_payload(events, run, payload={})
    start = by_id.get(failed.causation_id)
    if (
        native is None
        or native.id != p["candidate_snapshot_event_id"]
        or native.origin != "kernel"
        or native.payload.get("schema_version") != "candidate-freeze-receipt.v1"
        or native.payload.get("status") != "frozen"
        or start is None
        or start.type != "fanout.started"
        or start.origin != "kernel"
        or start.causation_id != native.id
        or native.id != p.get("trigger_event_id")
        or start.payload.get("trigger_event_id") != native.id
        or not events.index(native) < events.index(start) < events.index(failed)
    ):
        raise ValueError("registration cancellation no longer binds its native freeze and start")
    body = native.payload
    if not all(
        p.get(k) and p[k] == body.get(k)
        for k in (
            "freeze_receipt_digest",
            "freeze_id",
            "candidate_head_commit",
            "generation_id",
            "task_map_generation",
            "plan_artifact_package_digest",
        )
    ) or not all(
        p.get(k) and start.payload.get(k) == p[k] for k in ("fanout_id", "stage_id", "workflow_run_id", "task_id")
    ):
        raise ValueError("registration cancellation has different freeze or roster authority")
    return failed, native, start


def _original_readers(service, start, failed, native):
    p, body = failed.payload, native.payload
    run = p["workflow_run_id"]
    roster = start.payload.get("expected_children")
    if (
        not isinstance(roster, list)
        or not roster
        or not all(isinstance(row, dict) and row.get("child_id") and row.get("role_instance") for row in roster)
        or len({row["child_id"] for row in roster}) != len(roster)
        or len({row["role_instance"] for row in roster}) != len(roster)
    ):
        raise ValueError("registration retry requires the entire unique Reader roster")
    for row in roster:
        child = row.get("payload")
        spec = {"operation_type": "fanout_reader_child", "stage_id": p["stage_id"]}
        nested = child.get("trigger_payload") if isinstance(child, dict) else None
        if (
            not isinstance(nested, dict)
            or any(nested.get(k) != v for k, v in body.items())
            or any(
                v != p["task_id"] or k not in {"task_id", "parent_task_id"}
                for k, v in nested.items()
                if k not in body
            )
            or child.get("workflow_run_id") != run
            or child.get("task_id") != p["task_id"]
            or row.get("target_ref") not in ("", None, body["candidate_ref"])
            or call_result_profile_identity(payload=child, **spec)[0] != "candidate-verify"
            or uses_scoped_candidate_verify(service.config, payload=child, **spec)
        ):
            raise ValueError("registration retry requires all original frozen Candidate Readers")
    return roster


def _check_later_events(events, failed, native, start):
    p, run = failed.payload, failed.payload["workflow_run_id"]
    later = events[events.index(native) + 1 :]
    after = [e for e in later if (e.payload.get("workflow_run_id") or e.correlation_id) == run]
    if any(
        e.type == "candidate.ready"
        or (e.type == "fanout.started" and e.id != start.id and e.payload.get("stage_id") == p["stage_id"])
        for e in after
    ):
        raise ValueError("freeze has a later Candidate attempt or has already been retried")
    if any(e.type == "workflow.operation.requested" and e.causation_id == start.id for e in later):
        raise ValueError("an original Reader already has a durable operation")
    if any(
        e.type
        in {
            "workflow.operation.requested",
            "worker.dispatched",
            "fanout.child.dispatched",
            "fanout.child.completed",
            "fanout.child.failed",
            "fanout.aggregate.completed",
            "fanout.cancelled",
        }
        and e.id != failed.id
        and p["fanout_id"] in (e.payload.get("fanout_id"), e.payload.get("parent_fanout_id"))
        for e in later
    ):
        raise ValueError("an original Reader has already started or has an outcome")
    if any(
        e.type
        in {
            "test.failed",
            "verify.failed",
            "review.rejected",
            "judge.failed",
            "candidate.rework.requested",
            "task.pipeline.generation.admitted",
            "plan.artifact_package.admitted",
            "run.goal.completed",
            "run.ship.executed",
        }
        for e in after
    ):
        raise ValueError("later product verdict, generation or delivery prevents registration retry")
    diagnosed = [
        e
        for e in after
        if e.origin == "kernel"
        and e.actor == "run-manager"
        and e.type
        in {
            "run.manager.reflect.completed",
            "run.manager.autoresearch.requested",
            "run.manager.resident.prompted",
        }
        and failed.id in (e.payload.get("source_event_ids") or [])
    ]
    return [e.id for e in diagnosed]


def _check_current_inputs(service, events, native, roles):
    from zf.runtime.candidate_reader_assignments import (
        approved_roster_ref,
        assigned_spec,
    )
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_config_consumer import approved_config_consumer_error

    body = native.payload
    scope = [
        e for e in events if e.origin == "kernel" and e.payload.get("workflow_run_id") == body["workflow_run_id"]
    ]

    def latest(kind):
        return next((e for e in reversed(scope) if e.type == kind), None)

    descriptor = body.get("freeze_receipt_ref") or {}
    if descriptor.get("sha256") != body["freeze_receipt_digest"]:
        raise ValueError("native freeze receipt digest differs")
    receipt = hydrate_sidecar_ref(service.state_dir, descriptor)
    if (
        not receipt.ok
        or not isinstance(receipt.payload, dict)
        or any(body.get(k) != v for k, v in receipt.payload.items())
        or not all(
            k in receipt.payload
            for k in (
                "freeze_id",
                "candidate_ref",
                "candidate_head_commit",
                "workflow_run_id",
                "plan_artifact_package_digest",
                "task_map_generation",
                "completed_task_ids",
            )
        )
    ):
        raise ValueError("native freeze receipt no longer matches its immutable inputs")
    plan = latest("plan.artifact_package.admitted")
    generation = latest("task.pipeline.generation.admitted")
    if (
        not plan
        or plan.payload.get("package_digest") != body["plan_artifact_package_digest"]
        or plan.payload.get("package_ref") != body["plan_artifact_package_ref"]
        or not generation
        or generation.payload.get("generation_id") != body["generation_id"]
        or generation.payload.get("task_map_generation") != body["task_map_generation"]
    ):
        raise ValueError("Candidate Plan package or admitted generation changed")
    from zf.core.workflow.flow_metadata import flow_metadata_for

    spec = assigned_spec(service.state_dir, body)
    teams = flow_metadata_for(service.config, body.get("flow_kind", "")).get("stage_teams") or {}
    if teams.get("candidate_verify") or body.get("candidate_reader_approval"):
        approval = approved_roster_ref(service.state_dir, events, body, spec)
        if approval != body.get("candidate_reader_approval"):
            raise ValueError("original Candidate lacks its current OA authorization")
    if spec and set(roles) != {row["role_instance"] for row in spec["candidate_reader_assignments"]}:
        raise ValueError("original Candidate roster differs from its declared Reader assignments")
    invokes = [
        e
        for e in events
        if e.type == "workflow.invoke.requested" and e.payload.get("workflow_run_id") == body["workflow_run_id"]
    ]
    if len(invokes) != 1 or approved_config_consumer_error(service.state_dir, service.config, invokes[0].payload):
        raise ValueError("running workflow has no matching approved consumer configuration")


def _bound_checkpoint(service, events, failure_id):
    from zf.runtime.run_continuation import (
        bind_action_dependencies,
        blocking_decisions_for_action,
    )
    from zf.runtime.run_manager import _pending_human_decisions
    from zf.runtime.workflow_resume import _batch_checkpoint_from_event

    failed, native, start = _native_and_start(events, failure_id)
    roster = _original_readers(service, start, failed, native)
    diagnosis_refs = _check_later_events(events, failed, native, start)
    _check_current_inputs(service, events, native, [row["role_instance"] for row in roster])
    p, body, run = failed.payload, native.payload, failed.payload["workflow_run_id"]
    deps = bind_action_dependencies(
        [{"task_id": p["task_id"], "workflow_run_id": run}],
        TaskStore(service.state_dir / "kanban.json").list_all(),
    )
    if blocking_decisions_for_action(_pending_human_decisions(events), deps[0], events=events):
        raise ValueError("registration retry requires resolution of the outstanding owner decision")
    if _git_commit(service.project_root, body["candidate_ref"]) != body["candidate_head_commit"]:
        raise ValueError("Candidate ref no longer points to the native frozen commit")
    checkpoint = _batch_checkpoint_from_event(
        failed,
        source_event_type=failed.type,
        safe_resume_action="reemit_candidate_ready",
        evidence_event_ids=[native.id, start.id, failed.id, *diagnosis_refs],
        anchor=body,
    )
    return replace(checkpoint, upstream_fanout_id=body["fanout_id"]), native, roster


def registration_preview(service, failure_id):
    checkpoint, native, roster = _bound_checkpoint(service, service.writer.event_log.read_all(), failure_id)
    return {
        "action": "workflow-batch-resume",
        "checkpoint_id": checkpoint.checkpoint_id,
        "safe_resume_action": checkpoint.safe_resume_action,
        "registration_failure_event_id": failure_id,
        "workflow_run_id": checkpoint.workflow_run_id,
        "freeze_receipt_digest": native.payload["freeze_receipt_digest"],
        "reader_roles": [row["role_instance"] for row in roster],
    }


def apply_registration_resume(service, requested, action, requested_action, payload):
    from zf.runtime.workflow_resume_apply import _apply_batch_candidate_ready

    try:
        with locked_path(service.state_dir / "locks" / "candidate-registration-failure.lock"):
            events = service.writer.event_log.read_all()
            checkpoint, native, roster = _bound_checkpoint(
                service, events, payload["registration_failure_event_id"]
            )
            body = native.payload
            required = {
                "action": action,
                "checkpoint_id": checkpoint.checkpoint_id,
                "safe_resume_action": checkpoint.safe_resume_action,
                "registration_failure_event_id": checkpoint.source_event_id,
                "workflow_run_id": checkpoint.workflow_run_id,
                "freeze_receipt_digest": body["freeze_receipt_digest"],
                "approval_ref": str(payload.get("approval_ref") or "").strip(),
            }
            source_position = next(i for i, e in enumerate(events) if e.id == checkpoint.source_event_id)
            if (
                service.actor != "operator"
                or requested.actor != "operator"
                or requested.origin != "kernel"
                or requested.type != "control.action.requested"
                or not required["approval_ref"]
                or requested.causation_id != checkpoint.source_event_id
                or requested.correlation_id != checkpoint.workflow_run_id
                or not any(e == requested for e in events[source_position + 1 :])
                or any(payload.get(k) != v or requested.payload.get(k) != v for k, v in required.items())
            ):
                raise ValueError("registration retry requires an exact owner-authorized controlled request")
            runtime = SimpleNamespace(
                state_dir=service.state_dir,
                event_log=service.writer.event_log,
                config=service.config,
                project_root=service.project_root,
                task_store=TaskStore(service.state_dir / "kanban.json"),
            )
            for row in roster:
                authority = prepare_candidate_verification_authority(
                    runtime,
                    payload={
                        **row["payload"],
                        "role_instance": row["role_instance"],
                        "candidate_event_id": native.id,
                    },
                    workflow_run_id=checkpoint.workflow_run_id,
                    task_id=body["pdd_id"],
                    source_event_id=checkpoint.source_event_id,
                )
                if (
                    authority.get("target_commit") != body["candidate_head_commit"]
                    or authority.get("candidate_snapshot_event_id") != native.id
                ):
                    raise ValueError("Reader authority preflight did not bind the exact native target")
            binding = {
                k: body[k]
                for k in (
                    "plan_artifact_package_id",
                    "plan_artifact_package_ref",
                    "plan_artifact_package_digest",
                    "task_map_generation",
                    "generation_id",
                    "freeze_receipt_digest",
                    "candidate_reader_approval",
                )
                if k in body
            }
            binding.update(
                registration_failure_event_id=checkpoint.source_event_id,
                registration_authorization_event_id=requested.id,
            )
            current = service.writer.event_log.read_all()
            if current != events:
                _bound_checkpoint(service, current, checkpoint.source_event_id)
            result = _apply_batch_candidate_ready(service.writer, checkpoint, [], registration_binding=binding)
            if not result.applied:
                raise ValueError(result.reason)
            event = next(
                e
                for e in service.writer.event_log.read_all()
                if e.id in result.emitted_event_ids and e.type == "candidate.ready"
            )
            return service._recovery_ok(requested, event, action, requested_action, required)
    except (OSError, ValueError, KeyError) as exc:
        return service._recovery_failed(requested, action, requested_action, str(exc), 409)
