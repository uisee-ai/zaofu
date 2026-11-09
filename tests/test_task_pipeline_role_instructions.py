"""Task-stage activation must not teach a previous dispatch's emit protocol."""

from pathlib import Path

import pytest

from tests.test_role_lifecycle_runtime import _runtime
from zf.core.task.schema import Task
from zf.runtime.task_pipeline_runtime import _activate_task_stage_binding


@pytest.mark.parametrize("stage", ["impl", "verify"])
@pytest.mark.parametrize("stored_task", [True, False])
def test_task_stage_activation_defers_completion_to_current_briefing(
    tmp_path: Path, stage: str, stored_task: bool,
) -> None:
    runtime, role, transport = _runtime(tmp_path)
    role.publishes = ["dev.build.done", "dev.blocked"] if stage == "impl" else [
        "verify.child.completed", "verify.child.failed",
    ]
    task = Task(id="TASK-1", title="Pipeline task", active_dispatch_id="previous-dispatch")
    if stored_task:
        runtime.task_store.add(task)
    workspace = tmp_path / "task-workspace"
    workspace.mkdir()

    _activate_task_stage_binding(
        runtime, role=role, task=task,
        workflow_run_id="run-1", task_map_generation="map-1", stage=stage,
        workspace_generation=1, placement_epoch=1,
        role_config_digest="digest-1", project_path=workspace,
        workspace_root=tmp_path,
    )

    instructions = (runtime.state_dir / "instructions" / f"{role.instance_id}.md").read_text()
    assert "current dispatch briefing" in instructions
    assert "Output Contract" in instructions
    assert "result submit" in instructions
    assert "previous-dispatch" not in instructions
    assert "Configured Completion Payload Contract" not in instructions
    for event in role.publishes:
        assert f"emit {event}" not in instructions
    assert transport.spawned[-1][2] == workspace
    if stored_task:
        assert runtime.task_store.get(task.id).active_dispatch_id == "previous-dispatch"


def test_legacy_activation_keeps_configured_emit_protocol(tmp_path: Path) -> None:
    runtime, role, _ = _runtime(tmp_path)
    role.publishes = ["dev.build.done", "dev.blocked"]
    runtime.task_store.add(Task(
        id="TASK-LEGACY", title="Legacy task", active_dispatch_id="legacy-dispatch",
    ))

    runtime._ensure_role_active(role, task_id="TASK-LEGACY")

    instructions = (runtime.state_dir / "instructions" / f"{role.instance_id}.md").read_text()
    assert "emit dev.build.done" in instructions
    assert "--dispatch-id legacy-dispatch" in instructions
