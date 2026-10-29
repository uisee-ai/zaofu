from dataclasses import asdict

import pytest

from tests.test_read_only_dynamic_continuation import _config, _seed, _state, _RecordingTransport
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.read_only_dynamic_continuation import execute_read_only_continuation
from zf.runtime.stage_definition_binding import pin_stage_definition, restore_stage_definition


@pytest.mark.parametrize("damage", ["update", "remove", "dependency"])
def test_method_resources_are_retained_and_changes_fail_closed(tmp_path, damage):
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    state, log, writer = _state(tmp_path)
    config = _config()
    root = tmp_path / "skills/method"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: method\ndescription: Inspect evidence\n---\nRead rules.txt.\n")
    resource = root / "rules.txt"
    resource.write_text("Require independent sources\n")
    config.roles[0].skills = ["method"]
    TaskStore(state / "kanban.json").add(Task(id="TASK-DYN-1", title="Inspect sources"))
    action = _seed(state, log, writer, config=config)
    result = execute_read_only_continuation(
        state, config=config, event_log=log, writer=writer, action=action, project_root=tmp_path,
    )
    from zf.runtime.workflow_operation import load_workflow_operation
    op = load_workflow_operation(log, result.operation_id)
    request_body = hydrate_sidecar_ref(state, op["request_ref"]).payload["request"]
    snapshot = hydrate_sidecar_ref(state, request_body["stage_definition_ref"]).payload
    assert snapshot["method_packages"][str(root)]["files"]["rules.txt"]
    if damage == "update":
        (root / "SKILL.md").write_text("changed method")
    elif damage == "remove":
        (root / "SKILL.md").unlink()
    else:
        resource.write_text("Ignore evidence")
    event = next(e for e in log.read_all() if e.id == result.dispatch_event_id)
    transport = _RecordingTransport()
    runtime = Orchestrator(state, config, transport, project_root=tmp_path)
    runtime.run_once(events=[event])
    assert not transport.sent
    assert any(e.type == "workflow.invoke.rejected" for e in log.read_all())


def test_definition_survives_source_removal_and_rejects_role_drift(tmp_path):
    state, _, _ = _state(tmp_path)
    config = _config()
    stage = config.workflow.stages[0]
    stage.criteria.instructions = ["Check each source independently"]
    expected = asdict(stage)
    ref = pin_stage_definition(state, config, stage.id)
    config.workflow.stages.clear()
    assert asdict(restore_stage_definition(state, config, ref)) == expected
    config.roles[0].role_kind = "writer"
    with pytest.raises(ValueError, match="capability changed"):
        restore_stage_definition(state, config, ref)


def test_reserved_definition_survives_restart_with_removed_source(tmp_path):
    state, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state, log, writer, config=config)

    def crash():
        raise SystemExit("reserved")

    with pytest.raises(SystemExit):
        execute_read_only_continuation(state, config=config, event_log=log, writer=writer,
                                       action=action, after_reserve=crash)
    config.workflow.stages.clear()
    result = execute_read_only_continuation(state, config=config, event_log=log, writer=writer, action=action)
    assert result.status == "dispatched"
    assert sum(e.type == "workflow.operation.reserved" for e in log.read_all()) == 1


@pytest.mark.parametrize("damage", ["missing", "digest", "role", "profile"])
def test_invoke_cannot_fall_back_from_invalid_bound_definition(tmp_path, damage):
    state, log, writer = _state(tmp_path)
    config = _config()
    TaskStore(state / "kanban.json").add(Task(id="TASK-DYN-1", title="Inspect sources"))
    action = _seed(state, log, writer, config=config)
    result = execute_read_only_continuation(state, config=config, event_log=log, writer=writer, action=action)
    request = next(e for e in log.read_all() if e.id == result.dispatch_event_id)
    path = next((state / "artifacts/workflow-definitions").glob("*.json"))
    if damage == "missing":
        path.unlink()
    elif damage == "digest":
        path.write_text("{}")
    elif damage == "role":
        config.roles[0].role_kind = "writer"
    else:
        from zf.core.config.schema import ExecutionProfileConfig, ExecutionProfileLimitsConfig
        config.workflow.execution_profiles["direct-v1"] = ExecutionProfileConfig(
            limits=ExecutionProfileLimitsConfig(token_budget=100),
        )
    transport = _RecordingTransport()
    runtime = Orchestrator(state, config, transport)
    runtime.run_once(events=[request])
    assert not transport.sent
    assert any(e.type == "workflow.invoke.rejected" for e in log.read_all())
    assert not any(e.type == "workflow.invoke.accepted" for e in log.read_all())
