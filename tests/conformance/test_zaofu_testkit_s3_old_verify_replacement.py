"""Old verify attempt handover when a newer Task Map is admitted."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _submit_generation_stage,
    blocking_runtime,  # noqa: F401 -- imported fixture
)
from tests.conformance.test_zaofu_testkit_s3_live_map_replacement import _approve_inert_handover
from tests.test_plan_artifact_package import _package
from tests.test_task_pipeline_generation import _git
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_artifact_package import package_event_payload, write_plan_artifact_package
from zf.runtime.result_submit import ResultSubmitError, provision_role_submit_credential
from zf.runtime.task_pipeline_runtime import admit_task_pipeline_generation
from zf.runtime.workflow_operation import reduce_workflow_operations


def _expired_old_verify_handover(
    blocking_runtime,
):
    runtime, transport, old_admission = blocking_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    tokens = {role: provision_role_submit_credential(state, role)
              for role in ("impl-1", "verify-1")}
    task = runtime.task_store.get("TASK-BLOCKING")
    assert task is not None
    task.contract.scope = ["README.md"]
    task.contract.acceptance_criteria = ["README exists at the pinned commit"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    task.contract.validation = {"commands": [{
        "id": "readme", "command": task.contract.verification,
        "rolling_smoke": True, "tier": "runtime", "deterministic": True, "reusable": True,
    }]}
    runtime.task_store.update(task.id, contract=task.contract)
    assert [d.action for d in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
    old_impl = transport.contexts[-1]
    _submit_generation_stage(runtime, old_impl, tokens["impl-1"], repair=True)
    runtime._react_to_events()
    assert [d.action for d in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
    old_verify = transport.contexts[-1]
    assert old_verify.task_pipeline_stage == "verify"
    old_attempts = TaskAttemptStore(state / "task_attempts.json")
    assert old_attempts.get(old_verify.attempt_id)["status"] == "sent"

    package = _package(state, revision="live-verify-r2", generation="map-live-verify-new")
    descriptor = write_plan_artifact_package(state, package)
    identity = {
        "plan_artifact_package_id": descriptor["package_id"],
        "plan_artifact_package_ref": descriptor["ref"],
        "plan_artifact_package_digest": descriptor["sha256"],
    }
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted", correlation_id="run-1",
        payload=package_event_payload(package, descriptor, status="admitted"),
    ))
    task.contract.evidence_contract.update(
        task_map_generation="map-live-verify-new", source_refs=identity,
    )
    runtime.task_store.update(task.id, contract=task.contract)
    trigger = runtime.event_writer.append(ZfEvent(
        type="task_map.ready", correlation_id="run-1",
        payload={"task_map_generation": "map-live-verify-new"},
    ))
    admitted_map = runtime.event_writer.append(ZfEvent(
        type="task_map.admitted", correlation_id="run-1", causation_id=trigger.id,
        payload={"task_map_generation": "map-live-verify-new", "task_ids": [task.id]},
    ))
    new_admission = admit_task_pipeline_generation(
        runtime,
        trigger_event=trigger,
        task_map_admitted_event=admitted_map,
        stage_id="impl",
        trace_id="run-1",
        task_items=[{"task_id": task.id}],
        loaded=SimpleNamespace(
            workflow_run_id="run-1", task_map_generation="map-live-verify-new",
            dispatch_base_commit=old_admission.payload["dispatch_base_commit"], **identity,
        ),
    )
    assert new_admission.payload["generation_id"] != old_admission.payload["generation_id"]

    reopened = Orchestrator(state, config, transport, project_root=project)
    # Pools are stage-specific: the free writer can run the new map while the
    # old reader remains live. Only the occupied reader slot must be held.
    assert [d.action for d in reopened._dispatch_ready()] == ["task_pipeline_dispatch"]
    assert len(transport.contexts) == 3
    new_impl = transport.contexts[-1]
    assert new_impl.task_pipeline_stage == "impl"
    assert new_impl.operation_id not in {old_impl.operation_id, old_verify.operation_id}
    assert new_impl.attempt_id not in {old_impl.attempt_id, old_verify.attempt_id}
    assert old_attempts.get(old_verify.attempt_id)["status"] == "sent"
    events = reopened.event_log.read_all()
    dispatches = {e.payload["operation_id"]: e.payload for e in events
                  if e.type == "task.pipeline.stage.dispatched"}
    old_workdir = dispatches[old_verify.operation_id]["workdir"]
    new_workdir = dispatches[new_impl.operation_id]["workdir"]
    assert old_workdir != new_workdir
    old_head = _git(Path(old_workdir), "rev-parse", "HEAD")
    assert old_head == dispatches[old_verify.operation_id]["target_commit"]
    assert reopened._dispatch_ready() == []

    # The old verify is now stale by map/package identity, even though its lease is live.
    before_task = reopened.task_store.get(task.id)
    before_operations = reduce_workflow_operations(reopened.event_log.read_all())
    before_attempts = old_attempts.current_rows()
    before_events = reopened.event_log.read_all()
    with pytest.raises(ResultSubmitError) as stale:
        _submit_generation_stage(reopened, old_verify, tokens["verify-1"])
    assert stale.value.code == "result_not_admitted"
    assert "stale_plan_artifact_package" in {
        issue["code"] for issue in stale.value.details["issues"]
    }
    assert reopened.task_store.get(task.id) == before_task
    assert reduce_workflow_operations(reopened.event_log.read_all()) == before_operations
    assert old_attempts.current_rows() == before_attempts
    assert old_attempts.get(old_verify.attempt_id)["status"] == "sent"
    assert not any(e.type in {
        "task.pipeline.verify.completed", "workflow.call.result.admitted",
        "workflow.operation.settled", "task.ref.updated", "task.done", "candidate.ready",
    } for e in reopened.event_log.read_all()[len(before_events):])

    _submit_generation_stage(reopened, new_impl, tokens["impl-1"], repair=True)
    reopened._react_to_events()
    assert reopened._dispatch_ready() == []
    assert len(transport.contexts) == 3
    assert old_attempts.get(new_impl.attempt_id)["status"] == "succeeded"
    assert _git(Path(old_workdir), "rev-parse", "HEAD") == old_head
    # Fault input targets only the old lease, through the canonical Store.
    # The real expiry predicate and full run_once recovery remain enabled.
    old_attempts.renew_lease(
        old_verify.attempt_id, updated_at="2000-01-01T00:00:00+00:00",
        lease_expires_at="2000-01-01T00:00:00+00:00",
    )
    reopened = Orchestrator(state, config, transport, project_root=project)
    assert reopened.run_once() == []
    assert old_attempts.get(old_verify.attempt_id)["status"] == "expired"
    assert reduce_workflow_operations(reopened.event_log.read_all())[old_verify.operation_id]["status"] == "suspended"
    return reopened, old_verify, tokens


def test_new_map_isolates_live_old_verify_and_waits_for_reader_slot(
    blocking_runtime, monkeypatch,
) -> None:
    reopened, old_verify, tokens = _expired_old_verify_handover(blocking_runtime)
    state, config, project = reopened.state_dir, reopened.config, reopened.project_root
    transport = reopened.transport
    task = reopened.task_store.get("TASK-BLOCKING")
    old_attempts = TaskAttemptStore(state / "task_attempts.json")
    _approve_inert_handover(reopened, monkeypatch)
    new_verify = transport.contexts[-1]
    assert new_verify.task_pipeline_stage == "verify"
    assert new_verify.operation_id != old_verify.operation_id
    assert new_verify.attempt_id != old_verify.attempt_id
    assert new_verify.dispatch_id != old_verify.dispatch_id
    assert reduce_workflow_operations(reopened.event_log.read_all())[new_verify.operation_id]["task_map_generation"] == "map-live-verify-new"
    before_task = reopened.task_store.get(task.id)
    before_attempts = old_attempts.current_rows()
    before_operations = reduce_workflow_operations(reopened.event_log.read_all())
    before_events = reopened.event_log.read_all()
    with pytest.raises(ResultSubmitError) as late:
        _submit_generation_stage(reopened, old_verify, tokens["verify-1"])
    assert late.value.code == "operation_not_running"
    assert reopened.task_store.get(task.id) == before_task
    assert old_attempts.current_rows() == before_attempts
    assert reduce_workflow_operations(reopened.event_log.read_all()) == before_operations
    assert not any(e.type in {
        "task.pipeline.verify.completed", "workflow.call.result.admitted",
        "workflow.operation.settled", "task.ref.updated", "task.done", "candidate.ready",
    } for e in reopened.event_log.read_all()[len(before_events):])
    _submit_generation_stage(reopened, new_verify, tokens["verify-1"])
    reopened._react_to_events()
    decisions = reopened._dispatch_ready()
    assert "task_pipeline_candidate_frozen" in [d.action for d in decisions]
    events = reopened.event_log.read_all()
    assert reopened.task_store.get(task.id).status == "done"
    [ready] = [event for event in events if event.type == "candidate.ready"]
    assert ready.payload["task_map_generation"] == "map-live-verify-new"
    assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
    assert len(transport.contexts) == 4
    assert sum(e.type == "task.done" for e in events) == 1
    assert sum(e.type == "task.pipeline.verify.completed" for e in events) == 1
    assert old_attempts.get(old_verify.attempt_id)["status"] == "expired"
    assert old_attempts.get(new_verify.attempt_id)["status"] == "succeeded"
    assert sum(e.type == "workflow.operation.redrive_admitted" for e in events) == 1
    assert not any(e.type in {"kernel.housekeeping.failed", "task.pipeline.stage.dispatch_deferred"}
                   for e in events)
    for _ in range(2):
        reopened = Orchestrator(state, config, transport, project_root=project)
        assert reopened._dispatch_ready() == []
    assert len(transport.contexts) == 4
    assert sum(e.type == "candidate.ready" for e in reopened.event_log.read_all()) == 1
