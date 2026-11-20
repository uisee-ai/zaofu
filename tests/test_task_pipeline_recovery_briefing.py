"""Provider recovery preserves the current pipeline's typed output contract."""

from pathlib import Path

import pytest

from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.runtime.recovery import build_recovery_briefing


def setup_attempt(tmp_path: Path):
    state = tmp_path / "custom-state"
    path = state / "briefings" / "task-pipeline" / "impl-g2-p2.md"
    path.parent.mkdir(parents=True)
    task = Task(id="T", status="in_progress", assigned_to="dev-0",
                active_dispatch_id="dispatch-new",
                execution_binding=TaskExecutionBinding(workflow_run_id="RUN"))
    store = TaskAttemptStore(state / "task_attempts.json")
    row = store.ensure_for_dispatch(
        run_id="RUN", task_id="T", dispatch_id="dispatch-new", role="dev-0",
        instance_id="dev-0", operation_id="op-new", briefing_ref=str(path),
        created_at="2026-10-28T00:00:00Z", lease_expires_at="", max_attempts=3,
        identity_version="operation-v2", placement_epoch=2,
    ).attempt
    body = ("Active task: T\n\n## Output Contract\n"
            "zf result submit --operation op-new --file result.json\n"
            f"dispatch-new {row['attempt_id']} {row['lease_id']}\n")
    path.write_text(body)
    instructions = state / "instructions" / "dev-0.md"
    instructions.parent.mkdir()
    instructions.write_text("zf emit dev.build.done --dispatch-id stale-verify\n")
    return state, task, store, row, path, body


def test_recovery_preserves_current_typed_briefing_not_legacy_completion(tmp_path):
    state, task, _, _, path, body = setup_attempt(tmp_path)
    result = build_recovery_briefing(state, "dev-0", task, project_root=tmp_path)
    assert result.startswith(body)
    assert "stale-verify" not in result
    assert "Provider Recovery" in result
    assert path.read_text() == body


def test_provider_recovery_entrypoint_delivers_current_pipeline_contract(tmp_path, monkeypatch):
    from tests.integration.test_context_recycle_e2e import _FakeTransport
    from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator

    state, task, _, _, _, body = setup_attempt(tmp_path)
    TaskStore(state / "kanban.json").add(task)
    role = RoleConfig(name="dev-0", backend="mock")
    config = ZfConfig(project=ProjectConfig(name="test", state_dir="custom-state"), roles=[role])
    transport = _FakeTransport()
    runtime = Orchestrator(state, config, transport, project_root=tmp_path)
    monkeypatch.setattr(runtime, "_ensure_recovery_contract_sufficient", lambda *_: True)
    assert runtime._inject_recovery_briefing(role)
    delivered = Path(transport.send_task_calls[-1][1]).read_text()
    assert delivered.startswith(body)
    assert "stale-verify" not in delivered


@pytest.mark.parametrize("case", ["dispatch", "role", "missing", "outside", "task", "operation"])
def test_pipeline_recovery_does_not_fall_back_to_legacy_on_invalid_binding(tmp_path, case):
    state, task, _, _, path, body = setup_attempt(tmp_path)
    if case == "dispatch":
        task.active_dispatch_id = "stale-verify"
    elif case == "role":
        task.assigned_to = "other-worker"
    elif case == "missing":
        path.unlink()
    elif case == "outside":
        outside = tmp_path / "external.md"
        outside.write_text(body)
        path.unlink()
        path.symlink_to(outside)
    elif case == "task":
        path.write_text(body.replace("Active task: T", "Active task: OTHER"))
    elif case == "operation":
        path.write_text(body.replace("op-new", "op-old"))
    with pytest.raises(ValueError, match="pipeline recovery"):
        build_recovery_briefing(state, "dev-0", task, project_root=tmp_path)
