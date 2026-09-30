"""Live old-map lease protection and new-map handover; upstream admission is Given."""

from types import SimpleNamespace

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _submit_generation_stage,
    blocking_runtime,  # noqa: F401 -- imported fixture
)
from tests.test_plan_artifact_package import _package
from tests.test_task_pipeline_generation import _git
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_artifact_package import package_event_payload, write_plan_artifact_package
from zf.runtime.result_submit import ResultSubmitError, provision_role_submit_credential
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.task_pipeline_runtime import admit_task_pipeline_generation
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


def _approve_inert_handover(runtime, monkeypatch):
    """No OS worker here; keep RM decision/consumer/redrive real after expiry."""
    assert runtime._dispatch_ready() == []
    monkeypatch.setattr(Orchestrator, "_respawn_instance", lambda _runtime, role:
                        WorkflowRuntimeDecision(action="respawn", role=role.instance_id,
                                                reason="explicit inert physical respawn"))
    assert run_manager_tick(
        state_dir=runtime.state_dir, writer=runtime.event_writer, config=runtime.config,
        project_root=runtime.project_root, event_log=runtime.event_log,
        action_filter={"worker-lifecycle-recover"}, spawn_repairs=False,
    ).actions_applied == 1
    actions = [decision.action for decision in runtime.run_once()]
    assert actions == ["respawn", "task_pipeline_operation_redrive_admitted", "task_pipeline_dispatch"]


def test_new_map_waits_for_old_lease_and_isolates_late_result(
    blocking_runtime, monkeypatch,
) -> None:
    """Admitted maps wait for a live lane; expired old results cannot finish the new map."""
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
    old = transport.contexts[-1]
    old_attempts = TaskAttemptStore(state / "task_attempts.json")
    assert old_attempts.get(old.attempt_id)["status"] == "sent"
    old_operations = reduce_workflow_operations(runtime.event_log.read_all())
    assert old_operations[old.operation_id]["status"] == "running"

    package = _package(state, revision="live-r2", generation="map-live-new")
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
        task_map_generation="map-live-new", source_refs=identity,
    )
    runtime.task_store.update(task.id, contract=task.contract)
    trigger = runtime.event_writer.append(ZfEvent(
        type="task_map.ready", correlation_id="run-1",
        payload={"task_map_generation": "map-live-new"},
    ))
    admitted_map = runtime.event_writer.append(ZfEvent(
        type="task_map.admitted", correlation_id="run-1", causation_id=trigger.id,
        payload={"task_map_generation": "map-live-new", "task_ids": [task.id]},
    ))
    inputs = dict(
        trigger_event=trigger,
        task_map_admitted_event=admitted_map,
        stage_id="impl",
        trace_id="run-1",
        task_items=[{"task_id": task.id}],
        loaded=SimpleNamespace(
            workflow_run_id="run-1",
            task_map_generation="map-live-new",
            dispatch_base_commit=old_admission.payload["dispatch_base_commit"],
            **identity,
        ),
    )
    new_admission = admit_task_pipeline_generation(runtime, **inputs)
    assert new_admission.payload["generation_id"] != old_admission.payload["generation_id"]

    runtime = Orchestrator(state, config, transport, project_root=project)
    decisions = runtime._dispatch_ready()
    # A live old worker owns the only impl lane: the new map must hold rather
    # than concurrently dispatching a second writer into the same role.
    assert decisions == []
    assert len(transport.contexts) == 1
    assert old_attempts.get(old.attempt_id)["status"] == "sent"
    assert reduce_workflow_operations(runtime.event_log.read_all())[old.operation_id]["status"] == "running"
    # Before expiry the old operation is still running, so the generation /
    # package admission guard (not the suspended-operation guard) must reject it.
    before_live_task = runtime.task_store.get(task.id)
    before_live_operations = reduce_workflow_operations(runtime.event_log.read_all())
    with pytest.raises(ResultSubmitError) as stale:
        _submit_generation_stage(runtime, old, tokens[old.instance_id])
    assert stale.value.code == "result_not_admitted"
    assert stale.value.details["status"] == "superseded"
    assert "stale_plan_artifact_package" in {
        issue["code"] for issue in stale.value.details["issues"]
    }
    assert runtime.task_store.get(task.id) == before_live_task
    assert old_attempts.get(old.attempt_id)["status"] == "sent"
    assert reduce_workflow_operations(runtime.event_log.read_all()) == before_live_operations
    assert not any(e.type in {"task.ref.updated", "dev.build.done", "workflow.call.result.admitted"}
                   for e in runtime.event_log.read_all())
    with monkeypatch.context() as clock:
        clock.setattr("zf.runtime.task_attempt_runtime._expired", lambda _value: True)
        reopened = Orchestrator(state, config, transport, project_root=project)
        recovered = reopened.run_once()
    assert recovered == []
    assert old_attempts.get(old.attempt_id)["status"] == "expired"
    assert reduce_workflow_operations(reopened.event_log.read_all())[old.operation_id]["status"] == "suspended"
    runtime = Orchestrator(state, config, transport, project_root=project)
    _approve_inert_handover(runtime, monkeypatch)
    new = transport.contexts[-1]
    assert new.operation_id != old.operation_id
    assert new.attempt_id != old.attempt_id
    assert new.dispatch_id != old.dispatch_id
    assert new.task_pipeline_stage == "impl"
    attempts = TaskAttemptStore(state / "task_attempts.json")
    assert attempts.get(old.attempt_id)["status"] == "expired"
    operations = reduce_workflow_operations(runtime.event_log.read_all())
    assert operations[old.operation_id]["status"] == "requested"
    assert operations[new.operation_id]["status"] == "running"
    assert operations[new.operation_id]["task_map_generation"] == "map-live-new"
    assert runtime.task_store.get(task.id).status != "done"
    assert not any(e.type in {"candidate.ready", "task.done"}
                   for e in runtime.event_log.read_all())

    for _ in range(2):
        runtime = Orchestrator(state, config, transport, project_root=project)
        assert runtime._dispatch_ready() == []
    assert len(transport.contexts) == 2

    # A real late semantic submit must not settle or update the replacement task.
    before_task = runtime.task_store.get(task.id)
    before_attempts = attempts.current_rows()
    before_operations = reduce_workflow_operations(runtime.event_log.read_all())
    before_events = runtime.event_log.read_all()
    with pytest.raises(ResultSubmitError) as rejected:
        _submit_generation_stage(runtime, old, tokens[old.instance_id])
    # Suspended old operations fail closed before result admission; neither
    # the old operation nor the replacement operation may be settled by a late
    # worker payload.
    assert rejected.value.code == "operation_not_running"
    assert runtime.task_store.get(task.id) == before_task
    assert attempts.current_rows() == before_attempts
    assert reduce_workflow_operations(runtime.event_log.read_all()) == before_operations
    assert not any(e.type in {
        "task.ref.updated", "dev.build.done", "verify.passed", "task.done", "candidate.ready",
        "workflow.call.result.admitted", "workflow.operation.settled",
    } for e in runtime.event_log.read_all()[len(before_events):])

    for stage in ("impl", "verify"):
        context = transport.contexts[-1]
        assert context.task_pipeline_stage == stage
        assert context.attempt_id != old.attempt_id
        _submit_generation_stage(runtime, context, tokens[context.instance_id], repair=stage == "impl")
        runtime._react_to_events()
        decisions = [d.action for d in runtime._dispatch_ready()]
        if stage == "impl":
            assert decisions == ["task_pipeline_dispatch"]
        else:
            assert "task_pipeline_integrated" in decisions
            assert "task_pipeline_task_done" in decisions
            assert decisions[-1] == "task_pipeline_candidate_frozen"
    assert runtime.task_store.get(task.id).status == "done"
    for _ in range(2):
        runtime = Orchestrator(state, config, transport, project_root=project)
        assert runtime._dispatch_ready() == []
    events = runtime.event_log.read_all()
    [ready] = [e for e in events if e.type == "candidate.ready"]
    assert ready.payload["task_map_generation"] == "map-live-new"
    assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
    assert len(transport.contexts) == 3
    assert sum(e.type == "task.done" for e in events) == 1
    assert sum(e.type == "workflow.call.result.invalid" for e in events) == 1
    assert sum(e.type == "workflow.operation.redrive_admitted" for e in events) == 1
    assert attempts.get(old.attempt_id)["status"] == "expired"
    assert reduce_workflow_operations(events)[old.operation_id] == before_operations[old.operation_id]
    assert not any(e.type in {"kernel.housekeeping.failed", "task.pipeline.stage.dispatch_deferred"}
                   for e in events)
