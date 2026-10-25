from pathlib import Path

import pytest

from tests.test_role_lifecycle_runtime import _runtime

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.runtime.task_pipeline_runtime import _activate_task_stage_binding
from zf.runtime.transport import CompositeTransport, WorkerLifecycleSnapshot


@pytest.mark.parametrize("wrong_cwd", [True, False])
def test_composite_preserves_task_stage_cwd_observation(
    tmp_path: Path, wrong_cwd: bool,
) -> None:
    runtime, role, transport = _runtime(tmp_path)
    workspace = tmp_path / "task-workspace"
    workspace.mkdir()
    old_workspace = tmp_path / "old-role-workspace"
    old_workspace.mkdir()
    transport.alive.add(role.instance_id)

    def snapshot(instance_id: str) -> WorkerLifecycleSnapshot:
        cwd = transport.spawned[-1][2] if transport.spawned else (
            old_workspace if wrong_cwd else workspace
        )
        return WorkerLifecycleSnapshot(
            role_name=instance_id,
            alive=transport.is_alive(instance_id),
            current_path=str(cwd),
        )

    transport.lifecycle_snapshot = snapshot
    runtime.transport = CompositeTransport({role.instance_id: transport})
    registry = RoleSessionRegistry(
        runtime.state_dir / "role_sessions.yaml", project_root=str(tmp_path)
    )
    binding = registry.bind_task_stage_session(
        workflow_run_id="run-1", task_id="TASK-1", stage="impl",
        rework_affinity_id="map-1:impl", role_instance=role.instance_id,
        role_config_digest="digest-1", workspace_generation=1,
        placement_epoch=1, backend=role.backend,
    )
    registry.activate_task_stage_session(
        binding_key=binding["binding_key"], role_instance=role.instance_id
    )
    registry.update_instance_meta(role.instance_id, lifecycle_state="active")

    _activate_task_stage_binding(
        runtime, role=role, task=Task(id="TASK-1", title="Scaffold"),
        workflow_run_id="run-1", task_map_generation="map-1", stage="impl",
        workspace_generation=1, placement_epoch=2,
        role_config_digest="digest-1", project_path=workspace,
        workspace_root=tmp_path,
    )

    assert transport.terminated == ([role.instance_id] if wrong_cwd else [])
    if wrong_cwd:
        assert transport.spawned[-1][2] == workspace
    else:
        assert transport.spawned == []
    assert runtime.transport.lifecycle_snapshot(role.instance_id).current_path == str(workspace)


def test_composite_routes_snapshot_to_selected_instance() -> None:
    class ObservedTransport:
        def lifecycle_snapshot(self, instance_id: str) -> WorkerLifecycleSnapshot:
            return WorkerLifecycleSnapshot(instance_id, True, current_path=f"/{instance_id}")

    router = CompositeTransport({"dev-1": ObservedTransport(), "dev-2": ObservedTransport()})
    assert router.lifecycle_snapshot("dev-2").current_path == "/dev-2"


def test_composite_does_not_invent_cwd_for_adapter_without_snapshot() -> None:
    class BasicTransport:
        def is_alive(self, instance_id: str) -> bool:
            return True

    router = CompositeTransport({"dev-1": BasicTransport()})
    snapshot = router.lifecycle_snapshot("dev-1")
    assert snapshot.alive
    assert snapshot.current_path == ""
