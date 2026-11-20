"""A terminate request alone does not prove an old writer has stopped."""
from pathlib import Path

import pytest

from tests.test_role_lifecycle_runtime import _runtime
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting, _activate_task_stage_binding


@pytest.mark.parametrize("observation", ["alive", "unknown"])
def test_stage_rebinding_waits_for_observed_worker_exit(tmp_path: Path, observation: str):
    runtime, role, transport = _runtime(tmp_path)
    transport.alive.add(role.instance_id)
    workspace = tmp_path / "task-workspace"
    workspace.mkdir()
    candidate = workspace / "uncommitted.txt"
    candidate.write_text("retain this candidate")

    def terminate(instance_id):
        transport.terminated.append(instance_id)
        if observation == "unknown":
            def unavailable(_):
                raise OSError("worker status unavailable")
            transport.is_alive = unavailable

    transport.terminate = terminate
    with pytest.raises(TaskPipelineWaiting, match="old worker exit"):
        _activate_task_stage_binding(
            runtime, role=role, task=Task(id="T1", title="Resume"),
            workflow_run_id="run-1", task_map_generation="map-1", stage="impl",
            workspace_generation=1, placement_epoch=2, role_config_digest="digest-1",
            project_path=workspace, workspace_root=tmp_path,
        )
    assert transport.spawned == []
    assert candidate.read_text() == "retain this candidate"
    registry = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", project_root=str(tmp_path))
    assert registry.task_stage_binding(workflow_run_id="run-1", task_id="T1", stage="impl",
                                       rework_affinity_id="map-1:impl") is None
