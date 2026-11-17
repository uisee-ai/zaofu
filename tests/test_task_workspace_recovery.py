"""Recovery and maintenance must resolve the current task's configured workspace."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_maintenance_worktree_checkpoint import _project
from zf.core.config.schema import ProjectConfig, RoleConfig, RuntimeConfig, WorkdirConfig, ZfConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.maintenance_worktree import checkpoint_root
from zf.runtime.orchestrator_lifecycle import LifecycleManagerMixin
from zf.runtime.task_workspaces import TaskWorkspaceManager


@pytest.fixture
def workspace(tmp_path):
    project = tmp_path / "source"
    base = _project(project)
    state = tmp_path / "custom-state"
    role = RoleConfig(name="dev-1", backend="mock", role_kind="writer")
    config = ZfConfig(project=ProjectConfig(name="handoff"), roles=[role],
        runtime=RuntimeConfig(workdirs=WorkdirConfig(
            enabled=True, mode="worktree", root=str(state / "execution"))))
    manager = TaskWorkspaceManager(state_dir=state, project_root=project, config=config)
    plan = manager.prepare(role=role, workflow_run_id="r", task_id="T7",
        task_map_generation="map-g1", workspace_generation=1, base_ref=base)
    from tests.test_work_record import setup
    from tests.test_work_record_dispatch import dispatch

    _, task, log, _, _ = setup(state, canonical_request=True)
    writer = EventWriter(log)
    dispatch(log, Path(plan.project_path) / "progress.json")
    event = log.read_all()[-1]
    runtime = SimpleNamespace(state_dir=state, project_root=project, config=config,
        task_store=TaskStore(state / "kanban.json"), event_log=log, _terminal_task_pipeline_assignments=lambda: {})
    return runtime, role, task, Path(plan.project_path), writer, event


def test_checkpoint_uses_task_workspace_instead_of_existing_role_directory(workspace):
    runtime, role, task, task_root, _, _ = workspace
    role_root = runtime.state_dir / "execution" / role.instance_id / "project"
    _project(role_root)
    (role_root / "other-task.txt").write_text("unrelated work")
    assert checkpoint_root(runtime.state_dir, runtime.project_root,
        runtime.config, task.id, role.instance_id) == task_root


def test_respawn_honors_custom_workdir_root(workspace):
    runtime, role, _, task_root, _, _ = workspace
    assert LifecycleManagerMixin._active_task_pipeline_spawn_cwd(runtime, role) == task_root


def test_role_reuse_does_not_select_previous_tasks_directory(workspace):
    runtime, role, task, task_root, writer, event = workspace
    previous = runtime.state_dir / "execution/tasks/run-1/OTHER/g1/project"
    _project(previous)
    writer.append(ZfEvent(type=event.type, actor="kernel", task_id="OTHER",
        correlation_id="run-1", payload={**event.payload, "task_id": "OTHER",
            "attempt_id": "old-attempt", "dispatch_id": "old-dispatch", "workdir": str(previous)}))
    assert LifecycleManagerMixin._active_task_pipeline_spawn_cwd(runtime, role) == task_root


def test_spawn_checkpoint_and_recovery_git_share_persisted_task_worktree(workspace):
    import subprocess
    from zf.runtime.maintenance_worktree import capture_workspace
    from zf.runtime.recovery import capture_recovery_git_context
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    runtime, role, task, task_root, writer, _ = workspace
    runtime.event_writer = writer
    runtime._active_task_pipeline_spawn_cwd = lambda r: LifecycleManagerMixin._active_task_pipeline_spawn_cwd(runtime, r)
    (task_root / "staged.txt").write_text("staged work\n")
    subprocess.run(["git", "-C", str(task_root), "add", "staged.txt"], check=True)
    (task_root / "staged.txt").write_text("new unstaged work\n")
    (task_root / "new.txt").write_text("untracked work\n")
    assert LifecycleManagerMixin._role_spawn_cwd(runtime, role, source="test-restart") == task_root
    root = checkpoint_root(runtime.state_dir, runtime.project_root, runtime.config, task.id, role.instance_id)
    snapshot = hydrate_sidecar_ref(runtime.state_dir, capture_workspace(runtime.state_dir, root, "test")).payload
    assert snapshot["status"] == "captured"
    assert snapshot["provider_quiescence"] == "unverified"
    assert snapshot["staged_patch_base64"] and snapshot["unstaged_patch_base64"]
    assert snapshot["untracked"][0]["path"] == "new.txt"
    assert "staged.txt" in capture_recovery_git_context(runtime, role, task).dirty_files
    # A fresh reader after restart must preserve the same admitted path.
    runtime.event_log = EventLog(runtime.state_dir / "events.jsonl")
    runtime.task_store = TaskStore(runtime.state_dir / "kanban.json")
    assert LifecycleManagerMixin._role_spawn_cwd(runtime, role, source="replay") == task_root


@pytest.mark.parametrize("fault", ["missing", "stale_dispatch", "missing_dispatch", "wrong_run", "outside", "metadata"])
def test_pipeline_binding_failure_never_falls_back_to_role_directory(workspace, fault):
    runtime, role, task, task_root, writer, event = workspace
    role_root = runtime.state_dir / "execution" / role.instance_id / "project"
    _project(role_root)
    if fault == "missing":
        task_root.rename(task_root.with_name("preserved"))
    elif fault == "stale_dispatch":
        runtime.task_store.update(task.id, active_dispatch_id="replacement-dispatch")
    elif fault == "missing_dispatch":
        runtime.task_store.update(task.id, active_dispatch_id="")
    elif fault == "wrong_run":
        runtime.task_store.update(task.id,
            execution_binding=TaskExecutionBinding(workflow_run_id="other-run"))
    elif fault == "outside":
        writer.append(ZfEvent(type=event.type, actor="kernel", task_id=task.id,
            correlation_id="run-1", payload={**event.payload, "workdir": str(role_root)}))
    else:
        import json
        path = task_root.parent / "meta.json"
        body = json.loads(path.read_text())
        body["task_id"] = "OTHER"
        path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="workspace"):
        checkpoint_root(runtime.state_dir, runtime.project_root,
            runtime.config, task.id, role.instance_id)


@pytest.mark.parametrize("fault", ["origin", "attempt_id", "lease_id", "digest", "placement_epoch"])
@pytest.mark.parametrize("consumer", ["checkpoint", "spawn"])
def test_all_recovery_consumers_reject_untrusted_current_dispatch(workspace, fault, consumer):
    from tests.test_work_record_dispatch import dispatch
    runtime, role, task, root, _, _ = workspace
    dispatch(runtime.event_log, root / "progress.json", fault=fault)
    with pytest.raises(ValueError):
        if consumer == "checkpoint":
            checkpoint_root(runtime.state_dir, runtime.project_root, runtime.config, task.id, role.instance_id)
        else:
            LifecycleManagerMixin._active_task_pipeline_spawn_cwd(runtime, role)


def test_checkpoint_rejects_workspace_outside_current_configured_root(workspace):
    runtime, role, task, _, _, _ = workspace
    runtime.config.runtime.workdirs.root = str(runtime.state_dir / "replacement-root")
    with pytest.raises(ValueError, match="workspace"):
        checkpoint_root(runtime.state_dir, runtime.project_root, runtime.config, task.id, role.instance_id)
