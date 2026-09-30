"""Run Manager -> worker-action receipt -> blocking redrive, without a provider.

Only physical respawn/activation and transport are substitutes. No recovery
request, completion, redrive, dispatch or settlement event is written by tests.
"""

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _submit_generation_stage,
    blocking_runtime,  # noqa: F401 -- imported fixture
)
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.events import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_attempt_runtime import reconcile_task_attempts
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision
from tests.test_task_pipeline_generation import _git


@pytest.mark.parametrize("reopen", [False, True], ids=["same-process", "reconstructed"])
def test_run_manager_recovery_reaches_new_attempt(blocking_runtime, monkeypatch, reopen):
    runtime, transport, _admission = blocking_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    task = runtime.task_store.get("TASK-BLOCKING")
    assert task is not None
    task.contract.scope = ["README.md"]
    task.contract.acceptance_criteria = ["README exists at the pinned commit"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    runtime.task_store.update(task.id, contract=task.contract)
    tokens = {role: provision_role_submit_credential(state, role)
              for role in ("impl-1", "verify-1")}
    store = TaskAttemptStore(state / "task_attempts.json")
    respawn_calls = []

    def physical_respawn(_runtime, role):
        respawn_calls.append(role.instance_id)
        return WorkflowRuntimeDecision(action="respawn", role=role.instance_id,
                                       reason="inert physical respawn fixture")

    monkeypatch.setattr(Orchestrator, "_respawn_instance", physical_respawn)

    def tick():
        return run_manager_tick(
            state_dir=state, writer=runtime.event_writer, config=config,
            project_root=project, event_log=runtime.event_log,
            action_filter={"worker-lifecycle-recover"}, spawn_repairs=False,
        )

    assert [d.action for d in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
    first = transport.contexts[0]
    assert store.get(first.attempt_id)["identity_version"] == "operation-v2"
    # Fault injection is confined to the lease predicate, then immediately removed.
    with monkeypatch.context() as clock:
        clock.setattr("zf.runtime.task_attempt_runtime._expired", lambda _value: True)
        assert reconcile_task_attempts(runtime) >= 1
    assert store.get(first.attempt_id)["status"] == "expired"
    assert store.get(first.attempt_id)["recovery_owner"] == "run_manager"
    assert reduce_workflow_operations(runtime.event_log.read_all())[first.operation_id]["status"] == "suspended"
    assert runtime._dispatch_ready() == []

    assert tick().actions_applied == 1
    [request] = [e for e in runtime.event_log.read_all() if e.type == "worker.respawn.requested"]
    assert request.actor == "run-manager"
    assert request.payload["operation_id"] == first.operation_id
    assert request.payload["attempt_id"] == first.attempt_id
    assert request.payload["recovery_decision_owner"] == "run_manager"
    assert request.payload["recovery_effect_owner"] == "workflow_runtime_coordinator"
    assert tick().actions_applied == 0
    # Receipt-only success from Run Manager cannot authorize dispatch.
    assert runtime._dispatch_ready() == []
    assert len(transport.contexts) == 1
    assert not any(e.type == "workflow.operation.redrive_admitted"
                   for e in runtime.event_log.read_all())
    if reopen:
        runtime = Orchestrator(state, config, transport, project_root=project)

    # Real durable request consumer produces started/completed, not this test.
    assert [d.action for d in runtime._handle_worker_action_requests()] == ["respawn"]
    [completed] = [e for e in runtime.event_log.read_all() if e.type == "worker.respawn.completed"]
    assert completed.causation_id == request.id
    assert respawn_calls == ["impl-1"]
    if reopen:
        runtime = Orchestrator(state, config, transport, project_root=project)
    assert runtime._handle_worker_action_requests() == []
    assert [d.action for d in runtime._dispatch_ready()] == [
        "task_pipeline_operation_redrive_admitted", "task_pipeline_dispatch",
    ]
    first_after, second = store.get(first.attempt_id), transport.contexts[-1]
    assert len(transport.contexts) == 2
    assert second.operation_id == first.operation_id
    assert second.attempt_id != first.attempt_id
    assert second.dispatch_id != first.dispatch_id
    assert second.lease_id != first.lease_id
    assert first_after["status"] == "superseded"
    assert first_after["superseded_from_status"] == "expired"
    assert first_after["superseded_by"] == second.attempt_id
    assert store.get(second.attempt_id)["status"] == "sent"
    assert store.get(second.attempt_id)["placement_epoch"] == 2
    assert store.get(second.attempt_id)["ordinal"] == 2
    [redrive] = [e for e in runtime.event_log.read_all() if e.type == "workflow.operation.redrive_admitted"]
    assert redrive.payload["source_attempt_id"] == first.attempt_id
    assert redrive.payload["recovery_decision_event_id"] == request.id
    operation = reduce_workflow_operations(runtime.event_log.read_all())[first.operation_id]
    assert operation["status"] == "running"
    assert operation["redrive_count"] == 1
    assert operation["operation_generation"] == 1
    assert operation["task_map_generation"] == "map-current"

    for _ in range(2):
        runtime = Orchestrator(state, config, transport, project_root=project)
        assert tick().actions_applied == 0
        assert runtime._handle_worker_action_requests() == []
        assert runtime._dispatch_ready() == []
    assert len(transport.contexts) == 2
    assert respawn_calls == ["impl-1"]
    impl_event, _ = _submit_generation_stage(runtime, second, tokens["impl-1"])
    assert impl_event.payload["attempt_id"] == second.attempt_id
    runtime._react_to_events()
    assert store.get(second.attempt_id)["status"] == "succeeded"
    assert [d.action for d in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
    verify = transport.contexts[-1]
    assert verify.task_pipeline_stage == "verify"
    verify_event, body = _submit_generation_stage(runtime, verify, tokens["verify-1"])
    assert body["verdict"] == "passed"
    runtime._react_to_events()
    assert store.get(verify.attempt_id)["status"] == "succeeded"
    assert verify_event.payload["attempt_id"] == verify.attempt_id
    assert store.get(first.attempt_id)["status"] == "superseded"
    events = runtime.event_log.read_all()
    for event_type in ("worker.respawn.requested", "worker.respawn.completed",
                       "workflow.operation.redrive_admitted"):
        assert sum(e.type == event_type for e in events) == 1
    assert not any(e.type in {"kernel.housekeeping.failed", "task.pipeline.stage.dispatch_deferred"}
                   for e in events)


class IncompleteRespawnPromoted(AssertionError):
    """A non-successful physical respawn was promoted to a successful recovery."""


@pytest.mark.parametrize("reopen", [False, True], ids=["same-process", "reconstructed"])
@pytest.mark.parametrize("respawn_action", [
    "respawn", "respawn_failed", "respawn_cooldown",
])
def test_full_orchestrator_run_once_consumes_recovery_and_fails_closed(
    blocking_runtime, monkeypatch, respawn_action, reopen,
):
    """The idle Orchestrator loop must consume, or reject, RM recovery."""
    runtime, transport, _admission = blocking_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    task = runtime.task_store.get("TASK-BLOCKING")
    assert task is not None
    task.contract.scope = ["README.md"]
    task.contract.acceptance_criteria = ["README exists at the pinned commit"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    task.contract.validation = {"commands": [{
        "id": "contract-verification", "command": task.contract.verification,
        "tier": "runtime", "rolling_smoke": True, "deterministic": True, "reusable": True,
    }]}
    runtime.task_store.update(task.id, contract=task.contract)
    tokens = {role: provision_role_submit_credential(state, role)
              for role in ("impl-1", "verify-1")}
    store = TaskAttemptStore(state / "task_attempts.json")
    respawn_calls: list[str] = []
    original_respawn = Orchestrator._respawn_instance
    if respawn_action == "respawn_cooldown":
        monkeypatch.setattr(Orchestrator, "_respawn_recent_failure_cooldown_active",
                            lambda _runtime, _instance: True)

    def physical_respawn(_runtime, role):
        respawn_calls.append(role.instance_id)
        if respawn_action == "respawn_cooldown":
            # Real lease + lifecycle cooldown branch; no provider process starts.
            return original_respawn(_runtime, role)
        return WorkflowRuntimeDecision(
            action=respawn_action, role=role.instance_id,
            reason=f"deterministic {respawn_action} fixture",
        )

    monkeypatch.setattr(Orchestrator, "_respawn_instance", physical_respawn)
    assert any(decision.action == "task_pipeline_dispatch"
               for decision in runtime.run_once())
    first = transport.contexts[0]

    with monkeypatch.context() as clock:
        clock.setattr("zf.runtime.task_attempt_runtime._expired", lambda _value: True)
        # The periodic run_once sweep owns expiry observation; it must not
        # dispatch a replacement before Run Manager has made a decision.
        runtime.run_once()
    assert not any(e.type == "workflow.operation.redrive_admitted"
                   for e in runtime.event_log.read_all())
    expired = store.get(first.attempt_id)
    assert expired is not None
    assert expired["status"] == "expired"
    assert expired["recovery_owner"] == "run_manager"
    assert len(transport.contexts) == 1

    tick = run_manager_tick(
        state_dir=state, writer=runtime.event_writer, config=config,
        project_root=project, event_log=runtime.event_log,
        action_filter={"worker-lifecycle-recover"}, spawn_repairs=False,
    )
    assert tick.actions_applied == 1
    [request] = [e for e in runtime.event_log.read_all() if e.type == "worker.respawn.requested"]
    assert request.payload["attempt_id"] == first.attempt_id
    assert request.payload["operation_id"] == first.operation_id
    if reopen:
        runtime = Orchestrator(state, config, transport, project_root=project)

    # A full idle tick consumes the durable request. Success emits completed;
    # failure emits failed and must not authorize a same-operation redrive.
    decisions = runtime.run_once()
    events = runtime.event_log.read_all()
    assert respawn_calls == ["impl-1"]
    assert respawn_action in [d.action for d in decisions]
    assert not any(e.type == "kernel.housekeeping.failed" for e in events)
    if respawn_action != "respawn":
        completed = [e for e in events if e.type == "worker.respawn.completed"]
        redrives = [e for e in events if e.type == "workflow.operation.redrive_admitted"]
        if completed or redrives or len(transport.contexts) != 1:
            raise IncompleteRespawnPromoted(
                f"{respawn_action}: completed={len(completed)}, redrive={len(redrives)}, "
                f"dispatch={len(transport.contexts)}; expected 0/0/1"
            )
    if respawn_action == "respawn":
        [completed] = [e for e in events if e.type == "worker.respawn.completed"]
        assert completed.causation_id == request.id
        assert len(transport.contexts) == 2
        second = transport.contexts[1]
        assert second.operation_id == first.operation_id
        assert second.attempt_id != first.attempt_id
        assert second.placement_epoch == 2
        assert sum(event.type == "workflow.operation.redrive_admitted" for event in events) == 1
        assert store.get(first.attempt_id)["status"] == "superseded"
        assert store.get(second.attempt_id)["status"] == "sent"
        # A real commit, signed result admission, verify and candidate freeze
        # all remain in this same recovery run; no terminal facts are injected.
        impl, body = _submit_generation_stage(runtime, second, tokens["impl-1"], repair=True)
        assert impl.payload["attempt_id"] == second.attempt_id
        target = body["target_commit"]
        runtime.run_once()
        assert len(transport.contexts) == 3
        verify = transport.contexts[-1]
        assert verify.task_pipeline_stage == "verify"
        assert store.get(second.attempt_id)["status"] == "succeeded"
        _submit_generation_stage(runtime, verify, tokens["verify-1"])
        runtime.run_once()
        assert store.get(verify.attempt_id)["status"] == "succeeded"
        final = runtime.event_log.read_all()
        [integrated] = [e for e in final if e.type == "integration.queue.integrated"]
        receipt = hydrate_sidecar_ref(state, integrated.payload["receipt_ref"]).payload
        assert receipt["task_commit"] == target
        [smoke_ref] = receipt["rolling_smoke_receipt_refs"]
        smoke = hydrate_sidecar_ref(state, smoke_ref).payload
        assert smoke["exit_code"] == 0
        assert smoke["target_commit"] == receipt["new_candidate_head"]
        [ready] = [e for e in final if e.type == "candidate.ready"]
        frozen = hydrate_sidecar_ref(state, ready.payload["freeze_receipt_ref"]).payload
        assert frozen["status"] == "frozen"
        assert frozen["task_integration_receipt_refs"] == [integrated.payload["receipt_ref"]]
        assert frozen["candidate_head"] == ready.payload["commit"] == receipt["new_candidate_head"]
        assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
        assert runtime.task_store.get(task.id).status == "done"
    else:
        [failed] = [e for e in events if e.type == "worker.respawn.failed"]
        assert failed.causation_id == request.id
        assert failed.payload["action"] == respawn_action
        assert failed.payload["continuation_delivered"] is False
        assert failed.payload["orchestrator_checkpoint_requeued"] is False
        assert reduce_workflow_operations(events)[first.operation_id]["status"] == "suspended"
        assert runtime.task_store.get(task.id).status != "done"

    expected_sends = 3 if respawn_action == "respawn" else 1
    for _ in range(2):
        runtime = Orchestrator(state, config, transport, project_root=project)
        runtime.run_once()
        assert len(transport.contexts) == expected_sends
    final = runtime.event_log.read_all()
    for event_type in ("worker.respawn.requested", "worker.respawn.started"):
        assert sum(e.type == event_type for e in final) == 1
    for event_type in ("worker.respawn.completed", "workflow.operation.redrive_admitted",
                       "task.done", "candidate.ready", "integration.queue.integrated"):
        assert sum(e.type == event_type for e in final) == int(respawn_action == "respawn")
    assert respawn_calls == ["impl-1"]
    assert not any(e.type in {"kernel.housekeeping.failed", "task.pipeline.stage.dispatch_deferred"}
                   for e in final)


@pytest.mark.parametrize("interrupted_stage", ["impl", "verify"])
def test_workflow_batch_resume_control_action_redrives_interrupted_blocking_operation(
    blocking_runtime, monkeypatch, interrupted_stage,
):
    """Formal operator resume preserves stage identity through candidate freeze."""
    runtime, transport, _admission = blocking_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    store = TaskAttemptStore(state / "task_attempts.json")
    config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    task = runtime.task_store.get("TASK-BLOCKING")
    assert task is not None
    task.contract.scope = ["README.md"]
    task.contract.acceptance_criteria = ["README exists at the pinned commit"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    task.contract.validation = {"commands": [{
        "id": "contract-verification", "command": task.contract.verification,
        "tier": "runtime", "rolling_smoke": True, "deterministic": True, "reusable": True,
    }]}
    runtime.task_store.update(task.id, contract=task.contract)
    tokens = {role: provision_role_submit_credential(state, role)
              for role in ("impl-1", "verify-1")}

    def control(checkpoint):
        payload = {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "safe_resume_action": checkpoint["safe_resume_action"],
        }
        requested = runtime.event_writer.append(ZfEvent(
            type="control.action.requested", actor="operator", correlation_id="run-1",
            payload={"action": "workflow-batch-resume", **payload},
        ))
        result = ControlledActionService(
            state, runtime.event_writer, config=config, project_root=project,
            actor="operator", source="test", surface="test",
        ).execute(
            action="workflow-batch-resume", requested_action="operator-request",
            payload=payload, requested=requested,
        )
        event = next(e for e in runtime.event_log.read_all() if e.id == result["event_id"])
        assert event.causation_id == requested.id
        assert event.payload["checkpoint_id"] == checkpoint["checkpoint_id"]
        return result

    assert any(d.action == "task_pipeline_dispatch" for d in runtime.run_once())
    target = ""
    if interrupted_stage == "verify":
        _, body = _submit_generation_stage(
            runtime, transport.contexts[-1], tokens["impl-1"], repair=True,
        )
        target = body["target_commit"]
        runtime.run_once()
    first = transport.contexts[-1]
    assert first.task_pipeline_stage == interrupted_stage
    [live_checkpoint] = build_workflow_resume_projection(state, config)["operation_checkpoints"]
    assert live_checkpoint["safe_resume_action"] == "no_action"
    assert control(live_checkpoint)["status"] == "no_op"
    with monkeypatch.context() as clock:
        clock.setattr("zf.runtime.task_attempt_runtime._expired", lambda _value: True)
        runtime.run_once()
    assert store.get(first.attempt_id)["status"] == "expired"
    count_before = len(transport.contexts)

    # A checkpoint captured while the lease was live cannot authorize recovery
    # after the interruption changes the operation's canonical event identity.
    assert control(live_checkpoint)["status"] == "no_op"
    assert not any(e.type == "workflow.operation.redrive_admitted"
                   for e in runtime.event_log.read_all())
    assert len(transport.contexts) == count_before

    projection = build_workflow_resume_projection(
        runtime.state_dir,
        runtime.config,
        events=runtime.event_log.read_all(),
    )
    [checkpoint] = projection["operation_checkpoints"]
    assert checkpoint["safe_resume_action"] == (
        "redrive_interrupted_task_pipeline_operation"
    )
    runtime = Orchestrator(state, config, transport, project_root=project)
    result = control(checkpoint)
    assert result["ok"] is True
    assert result["status"] == "applied"
    events = runtime.event_log.read_all()
    assert sum(e.type == "workflow.resume.control_action.result" for e in events) == 3
    assert sum(e.type == "workflow.operation.redrive_admitted" for e in events) == 1
    assert len(transport.contexts) == count_before
    [redrive] = [e for e in events if e.type == "workflow.operation.redrive_admitted"]
    assert redrive.payload["source_attempt_id"] == first.attempt_id
    assert redrive.payload["recovery_decision_owner"] == "kernel_replay"

    runtime = Orchestrator(
        runtime.state_dir, runtime.config, transport,
        project_root=runtime.project_root,
    )
    decisions = runtime.run_once()
    assert any(d.action == "task_pipeline_dispatch" for d in decisions)
    assert len(transport.contexts) == count_before + 1
    second = transport.contexts[-1]
    assert second.operation_id == first.operation_id
    assert second.attempt_id != first.attempt_id
    assert second.dispatch_id != first.dispatch_id
    assert second.lease_id != first.lease_id
    assert second.task_pipeline_stage == interrupted_stage
    assert store.get(first.attempt_id)["superseded_by"] == second.attempt_id
    assert store.get(second.attempt_id)["status"] == "sent"

    assert control(checkpoint)["status"] == "no_op"
    assert len(transport.contexts) == count_before + 1
    if interrupted_stage == "impl":
        _, body = _submit_generation_stage(runtime, second, tokens["impl-1"], repair=True)
        target = body["target_commit"]
        runtime.run_once()
    verify = transport.contexts[-1]
    assert verify.task_pipeline_stage == "verify"
    _submit_generation_stage(runtime, verify, tokens["verify-1"])
    runtime.run_once()
    assert runtime.task_store.get(task.id).status == "done"
    assert store.get(second.attempt_id)["status"] == "succeeded"
    assert store.get(verify.attempt_id)["status"] == "succeeded"
    final = runtime.event_log.read_all()
    [integrated] = [e for e in final if e.type == "integration.queue.integrated"]
    receipt = hydrate_sidecar_ref(state, integrated.payload["receipt_ref"]).payload
    assert receipt["task_commit"] == target
    [smoke_ref] = receipt["rolling_smoke_receipt_refs"]
    assert hydrate_sidecar_ref(state, smoke_ref).payload["exit_code"] == 0
    [ready] = [e for e in final if e.type == "candidate.ready"]
    freeze = hydrate_sidecar_ref(state, ready.payload["freeze_receipt_ref"]).payload
    assert freeze["status"] == "frozen"
    assert freeze["task_integration_receipt_refs"] == [integrated.payload["receipt_ref"]]
    assert freeze["candidate_head"] == ready.payload["commit"] == receipt["new_candidate_head"]
    assert _git(project, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
    for _ in range(2):
        runtime = Orchestrator(state, config, transport, project_root=project)
        assert control(checkpoint)["status"] == "no_op"
        runtime.run_once()
    assert len(transport.contexts) == 3
    final = runtime.event_log.read_all()
    for kind in ("workflow.operation.redrive_admitted", "integration.queue.integrated",
                 "task.done", "candidate.ready"):
        assert sum(e.type == kind for e in final) == 1
    assert not any(e.type in {"kernel.housekeeping.failed", "worker.respawn.requested"}
                   for e in final)
