"""Actual Task Pipeline dispatch and provider hook must share a write root."""

from pathlib import Path

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import blocking_runtime  # noqa: F401
from tests.test_hook_recv_codex import _invoke
from zf.core.events import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.task_attempt_runtime import reconcile_task_attempts
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


@pytest.mark.parametrize("role_slot_exists", [False, True])
@pytest.mark.parametrize("recovered", [False, True])
def test_pipeline_hook_uses_pinned_workdir(
    blocking_runtime, monkeypatch, role_slot_exists, recovered,
):
    runtime, transport, _ = blocking_runtime
    runtime._dispatch_ready()
    if recovered:
        with monkeypatch.context() as clock:
            clock.setattr("zf.runtime.task_attempt_runtime._expired", lambda _: True)
            reconcile_task_attempts(runtime)
        result = run_manager_tick(
            state_dir=runtime.state_dir, writer=runtime.event_writer, config=runtime.config,
            project_root=runtime.project_root, event_log=runtime.event_log,
            action_filter={"worker-lifecycle-recover"}, spawn_repairs=False,
        )
        assert result.actions_applied == 1
        monkeypatch.setattr(Orchestrator, "_respawn_instance", lambda _, role:
                            WorkflowRuntimeDecision(action="respawn", role=role.instance_id))
        runtime._handle_worker_action_requests()
        runtime._dispatch_ready()
        assert len(transport.contexts) == 2
    context = transport.contexts[-1]
    registry = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", str(runtime.project_root))
    binding = registry.task_stage_bindings()[context.task_stage_session_binding]
    [dispatch] = [e for e in runtime.event_log.read_all()
                  if e.type == "task.pipeline.stage.dispatched"
                  and e.payload["attempt_id"] == context.attempt_id]
    workdir = Path(dispatch.payload["workdir"])
    role_slot = runtime.state_dir / "workdirs" / context.instance_id / "project"
    if role_slot_exists:
        role_slot.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(runtime.project_root)

    def invoke(target):
        return _invoke(runtime.state_dir, "claude.hook.pre_tool_use", "claude-code", {
            "session_id": binding["session_id"], "cwd": str(workdir),
            "tool_name": "Write", "tool_input": {"file_path": str(target), "content": "ok"},
        }, monkeypatch)

    assert invoke(workdir / "app" / "inside.py") == 0
    hooks = [e for e in runtime.event_log.read_all() if e.type == "claude.hook.pre_tool_use"]
    assert hooks[-1].actor == context.instance_id
    assert hooks[-1].causation_id  # No unknown-actor fail-open proof.
    # A newer provider-authored or mismatched placement must not change authority.
    for origin, changed in [
        ("worker", {}), ("kernel", {"attempt_id": "stale-attempt"}),
        ("kernel", {"dispatch_id": "stale-dispatch"}),
        ("kernel", {"role_instance": "other-worker"}),
    ]:
        runtime.event_log.append(ZfEvent(
            type="task.pipeline.stage.dispatched", origin=origin,
            payload={**dispatch.payload, "workdir": str(role_slot), **changed},
        ))
    assert invoke(workdir / "app" / "inside.py") == 0
    assert invoke(runtime.project_root / "app" / "outside.py") == 2
    assert invoke(role_slot / "app" / "old-slot.py") == 2
    assert invoke(workdir / "outside-scope.py") == 2
    (workdir / "app").mkdir(exist_ok=True)
    (workdir / "app" / "escape").symlink_to(runtime.project_root, target_is_directory=True)
    assert invoke(workdir / "app" / "escape" / "app" / "outside.py") == 2
