"""Real RM/respawn/redrive with deterministic process-boundary fault injection.

Transport termination/spawn are recorded, not OS processes. Unlike K465 the
respawn decision, lease, coordinator, recovery briefing and receipts are real.
"""

import json
from pathlib import Path

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    blocking_runtime,  # noqa: F401 -- imported fixture
)
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.workflow_operation import reduce_workflow_operations


class FailedTerminationAdmitsReplacement(AssertionError):
    """An explicitly failed old-worker termination permits replacement/redrive."""


@pytest.mark.parametrize(
    "termination_fails",
    [False, True],
    ids=["terminated", "still-live"],
)
def test_run_manager_respawn_requires_old_worker_termination(
    blocking_runtime, monkeypatch, record_property, termination_fails,
):
    runtime, transport, _admission = blocking_runtime
    state, config, project = runtime.state_dir, runtime.config, runtime.project_root
    store = TaskAttemptStore(state / "task_attempts.json")
    calls = []
    spawn_paths = []
    old_alive = True

    def terminate(instance):
        nonlocal old_alive
        calls.append(("terminate", instance, old_alive))
        if termination_fails:
            raise RuntimeError("injected termination failure: old worker remains live")
        old_alive = False

    def spawn(role, argv, *, cwd=None):
        calls.append(("spawn", role.instance_id, old_alive))
        spawn_paths.append(cwd)
        assert cwd is not None and cwd != project
        assert cwd.is_dir()

    # Only physical effects are substituted. Production SpawnCoordinator and
    # _respawn_instance remain in the actual worker-action consumer call chain.
    monkeypatch.setattr(transport, "terminate", terminate, raising=False)
    monkeypatch.setattr(transport, "spawn", spawn, raising=False)
    assert any(d.action == "task_pipeline_dispatch" for d in runtime.run_once())
    first = transport.contexts[0]
    [initial_dispatch] = [e for e in runtime.event_log.read_all()
                          if e.type == "task.pipeline.stage.dispatched"]
    with monkeypatch.context() as clock:
        clock.setattr("zf.runtime.task_attempt_runtime._expired", lambda _value: True)
        runtime.run_once()
    assert store.get(first.attempt_id)["status"] == "expired"
    assert len(transport.contexts) == 1

    tick = run_manager_tick(
        state_dir=state, writer=runtime.event_writer, config=config,
        project_root=project, event_log=runtime.event_log,
        action_filter={"worker-lifecycle-recover"}, spawn_repairs=False,
    )
    assert tick.actions_applied == 1
    [request] = [e for e in runtime.event_log.read_all() if e.type == "worker.respawn.requested"]
    assert request.payload["attempt_id"] == first.attempt_id
    runtime = Orchestrator(state, config, transport, project_root=project)
    decisions = runtime.run_once()
    events = runtime.event_log.read_all()
    assert not any(e.type == "kernel.housekeeping.failed" for e in events)
    completed = [e for e in events if e.type == "worker.respawn.completed"]
    redrives = [e for e in events if e.type == "workflow.operation.redrive_admitted"]
    dispatches = [c for c in transport.contexts if c.task_pipeline_stage == "impl"]
    observation = {
        "termination_fails": termination_fails, "old_alive": old_alive,
        "calls": calls, "decisions": [d.action for d in decisions],
        "completed": len(completed), "redrives": len(redrives),
        "pipeline_dispatches": len(dispatches),
        "old_attempt_status": store.get(first.attempt_id)["status"],
    }
    record_property("respawn_termination_observation", json.dumps(observation))
    assert calls[0] == ("terminate", "impl-1", True)
    if termination_fails:
        if any(c[0] == "spawn" for c in calls) or completed or redrives or len(dispatches) != 1:
            raise FailedTerminationAdmitsReplacement(json.dumps(observation))
        assert old_alive
        [failed] = [e for e in events if e.type == "worker.respawn.failed"
                    and e.causation_id == request.id]
        assert failed.payload["action"] == "respawn_failed"
        assert "injected termination failure" in failed.payload["reason"]
        assert failed.payload["continuation_delivered"] is False
        assert failed.payload["orchestrator_checkpoint_requeued"] is False
        assert store.get(first.attempt_id)["status"] == "expired"
        assert not any(e.type == "worker.respawned" for e in events)
        assert reduce_workflow_operations(events)[first.operation_id]["status"] == "suspended"
    else:
        assert spawn_paths == [Path(initial_dispatch.payload["workdir"])]
        assert len(transport.contexts) == 2, "recovery must await the new stage briefing, not send an idle task"
        assert calls == [("terminate", "impl-1", True), ("spawn", "impl-1", False)]
        assert len(completed) == len(redrives) == 1
        assert completed[0].causation_id == request.id
        assert len(dispatches) == 2
        second = dispatches[-1]
        assert second.operation_id == first.operation_id
        assert second.attempt_id != first.attempt_id
        assert store.get(first.attempt_id)["superseded_by"] == second.attempt_id
        assert store.get(second.attempt_id)["status"] == "sent"
    # Replay must not repeat either the destructive boundary or recovery delivery.
    before_calls, before_sends = list(calls), len(transport.contexts)
    for _ in range(2):
        runtime = Orchestrator(state, config, transport, project_root=project)
        runtime.run_once()
    assert calls == before_calls
    assert len(transport.contexts) == before_sends
    assert not any(e.type == "task.done" for e in runtime.event_log.read_all())


def test_task_stage_spawn_ignores_unbound_dispatches(blocking_runtime):
    runtime, transport, _ = blocking_runtime
    runtime._dispatch_ready()
    [dispatch] = [e for e in runtime.event_log.read_all()
                  if e.type == "task.pipeline.stage.dispatched"]
    for origin, changed in [
        ("worker", {}), ("kernel", {"placement_epoch": 0}),
        ("kernel", {"role_instance": "other-worker"}),
        ("kernel", {"task_stage_session_binding": "stale-binding"}),
        ("kernel", {"workspace_generation": 2}),
    ]:
        runtime.event_log.append(ZfEvent(
            type=dispatch.type, origin=origin,
            payload={**dispatch.payload, "workdir": str(runtime.project_root), **changed},
        ))
    role = runtime.config.roles[0]
    assert runtime._role_spawn_cwd(role, source="test_recovery") == Path(dispatch.payload["workdir"])
    assert not (runtime.state_dir / "workdirs" / role.instance_id / "project").exists()


@pytest.mark.parametrize("fault", ["missing", "symlink", "placement", "binding"])
def test_unproven_task_stage_spawn_does_not_fall_back(blocking_runtime, fault):
    runtime, transport, _ = blocking_runtime
    runtime._dispatch_ready()
    [context] = transport.contexts
    [dispatch] = [e for e in runtime.event_log.read_all()
                  if e.type == "task.pipeline.stage.dispatched"]
    workdir = Path(dispatch.payload["workdir"])
    registry = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", str(runtime.project_root))
    binding = registry.task_stage_bindings()[context.task_stage_session_binding]
    if fault in {"missing", "symlink"}:
        # Move only this test's isolated workspace; never destroy candidate files.
        workdir.rename(workdir.with_name("preserved-project"))
        if fault == "symlink":
            workdir.symlink_to(workdir.with_name("preserved-project"), target_is_directory=True)
    elif fault == "placement":
        registry.bind_task_stage_session(
            workflow_run_id=binding["workflow_run_id"], task_id=binding["task_id"],
            stage=binding["stage"], rework_affinity_id=binding["rework_affinity_id"],
            role_instance=context.instance_id, role_config_digest=binding["role_config_digest"],
            workspace_generation=binding["workspace_generation"], placement_epoch=2,
            backend="mock",
        )
    else:
        registry.update_instance_meta(context.instance_id, active_task_stage_binding_key="missing")
    with pytest.raises(RuntimeError, match="Task-stage spawn"):
        runtime._role_spawn_cwd(runtime.config.roles[0], source="test_recovery")
    assert not (runtime.state_dir / "workdirs" / context.instance_id / "project").exists()


def test_running_task_stage_recovery_reuses_exact_stage_briefing(blocking_runtime, monkeypatch):
    runtime, transport, _ = blocking_runtime
    runtime._dispatch_ready()
    [context] = transport.contexts
    task_before = runtime.task_store.get(context.task_id)
    attempts_before = TaskAttemptStore(runtime.state_dir / "task_attempts.json").current_rows()
    observed = []
    original_send = transport.send_task

    def send(role_name, briefing_path, prompt, *, context=None):
        observed.append((briefing_path, prompt))
        return original_send(role_name, briefing_path, prompt, context=context)

    monkeypatch.setattr(transport, "send_task", send)
    assert runtime._inject_recovery_briefing(runtime.config.roles[0])
    [briefing] = observed
    assert briefing[0] == context.briefing_path
    assert "Task Pipeline stage briefing" in briefing[1]
    assert "kernel-managed task.md" not in briefing[1]
    assert runtime.task_store.get(context.task_id) == task_before
    assert TaskAttemptStore(runtime.state_dir / "task_attempts.json").current_rows() == attempts_before


@pytest.mark.parametrize("fault", ["missing-briefing", "missing-binding"])
def test_task_stage_recovery_never_falls_back_to_generic_packet(blocking_runtime, fault):
    runtime, transport, _ = blocking_runtime
    runtime._dispatch_ready()
    [context] = transport.contexts
    if fault == "missing-briefing":
        context.briefing_path.rename(context.briefing_path.with_suffix(".preserved"))
    else:
        RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", str(runtime.project_root)).update_instance_meta(
            context.instance_id, active_task_stage_binding_key="missing",
        )
    assert runtime._inject_recovery_briefing(runtime.config.roles[0]) is False
    assert len(transport.contexts) == 1
    assert not (runtime.state_dir / "briefings" / "impl-1-recovery.md").exists()
