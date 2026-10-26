"""Real hook entry respects the current Task Pipeline assignment."""

from types import SimpleNamespace

import pytest

from tests.test_hook_recv_codex import _invoke
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.state.role_sessions import RoleSessionRegistry


@pytest.fixture
def assignment(tmp_path, monkeypatch):
    state = tmp_path / "runtime-state"
    state.mkdir()
    legacy = state / "workdirs/dev-1/project"
    legacy.mkdir(parents=True)
    workspace = state / "workdirs/tasks/run-1/T-NEW/g1/project"
    workspace.mkdir(parents=True)
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state / "kanban.json")
    for task_id, scope in [("T-OLD", ["**/*"]), ("T-NEW", ["src/**"])]:
        store.add(
            Task(id=task_id, status="in_progress", contract=TaskContract(scope=scope))
        )
    writer.append(
        ZfEvent(type="task.dispatched", task_id="T-OLD", payload={"role": "dev-1"})
    )
    payload = {
        "workflow_run_id": "run-1",
        "task_id": "T-NEW",
        "role_instance": "dev-1",
        "task_pipeline_stage": "impl",
        "task_map_generation": "map-2",
        "workspace_generation": 1,
        "placement_epoch": 1,
        "operation_generation": 1,
        "stage_id": "impl",
        "workdir": str(workspace),
        "canonical_success_event": "dev.build.done",
        "canonical_failure_event": "dev.blocked",
    }
    prepared = SimpleNamespace(operation_id="operation-new")
    payload.update(operation_id=prepared.operation_id, dispatch_id="dispatch-new")
    for kind in ["workflow.operation.requested", "workflow.operation.started"]:
        writer.append(
            ZfEvent(type=kind, task_id="T-NEW", origin="kernel", payload=payload)
        )
    registry = RoleSessionRegistry(
        state / "role_sessions.yaml", project_root=str(tmp_path)
    )
    registry.bind_codex_session("dev-1", "8ec85a68-8d54-4eee-9101-b0669f3ea098")
    writer.append(
        ZfEvent(
            type="task.pipeline.stage.dispatched",
            task_id="T-NEW",
            origin="kernel",
            payload={**payload, "dispatch_id": "dispatch-new"},
        )
    )
    monkeypatch.setenv("ZF_ROLE_INSTANCE", "dev-1")
    monkeypatch.setenv("ZF_ROLE_NAME", "dev")
    return state, legacy, workspace, log, writer, prepared


def invoke(assignment, monkeypatch, target, *, backend="codex", shell=False):
    state, _, workspace, _, _, _ = assignment
    tool_input = (
        {"command": f"printf test > {target}"}
        if shell
        else {
            "command": f"*** Begin Patch\n*** Add File: {target}\n+test\n*** End Patch"
        }
    )
    return _invoke(
        state,
        event=f"{'codex' if backend == 'codex' else 'claude'}.hook.pre_tool_use",
        backend=backend,
        payload={
            "session_id": "8ec85a68-8d54-4eee-9101-b0669f3ea098",
            "cwd": str(workspace),
            "tool_name": "Bash" if shell else "apply_patch",
            "tool_input": tool_input,
        },
        monkeypatch=monkeypatch,
    )


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
@pytest.mark.parametrize("shell", [False, True])
def test_current_task_workspace_write_passes_real_hook(
    assignment, monkeypatch, backend, shell
):
    _, _, workspace, log, _, _ = assignment
    assert (
        invoke(
            assignment,
            monkeypatch,
            workspace / "src/app.ts",
            backend=backend,
            shell=shell,
        )
        == 0
    )
    hook = [e for e in log.read_all() if e.type.endswith(".pre_tool_use")][-1]
    dispatch = [
        e for e in log.read_all() if e.type == "task.pipeline.stage.dispatched"
    ][-1]
    assert hook.causation_id == dispatch.id


def test_old_role_directory_is_no_longer_authorized(assignment, monkeypatch):
    _, legacy, _, _, _, _ = assignment
    assert invoke(assignment, monkeypatch, legacy / "src/app.ts") == 2


def test_contract_scope_is_taken_from_pipeline_task(assignment, monkeypatch):
    _, _, workspace, log, _, _ = assignment
    assert invoke(assignment, monkeypatch, workspace / "outside.ts") == 2
    rejected = [e for e in log.read_all() if e.type == "worker.scope_write.rejected"][
        -1
    ]
    assert rejected.task_id == "T-NEW"
    assert rejected.payload.get("allowed_paths") == ["src/**"]


@pytest.mark.parametrize(
    "target_kind", ["other_task", "old_generation", "traversal", "symlink"]
)
def test_other_workspaces_and_escapes_stay_blocked(
    assignment, monkeypatch, target_kind
):
    state, _, workspace, _, _, _ = assignment
    other = state / "workdirs/tasks/run-1/T-OTHER/g1/project/src/app.ts"
    if target_kind == "old_generation":
        other = workspace.parent.parent / "g0/project/src/app.ts"
    elif target_kind == "traversal":
        other = workspace / "../../g0/project/src/app.ts"
    elif target_kind == "symlink":
        destination = state.parent / "escaped"
        destination.mkdir()
        (workspace / "src").symlink_to(destination, target_is_directory=True)
        other = workspace / "src/app.ts"
    assert invoke(assignment, monkeypatch, other) == 2


def test_settled_operation_cannot_authorize_old_task_workspace(assignment, monkeypatch):
    _, _, workspace, _, writer, prepared = assignment
    writer.append(
        ZfEvent(
            type="workflow.operation.settled",
            task_id="T-NEW",
            payload={"operation_id": prepared.operation_id, "workflow_run_id": "run-1"},
        )
    )
    assert invoke(assignment, monkeypatch, workspace / "src/app.ts") == 2


def test_in_memory_dispatch_index_tracks_pipeline_assignment(assignment):
    from zf.cli.hook_recv import _resolve_causation

    state, _, _, log, _, _ = assignment
    dispatch = [
        e for e in log.read_all() if e.type == "task.pipeline.stage.dispatched"
    ][-1]
    assert log.index.latest_dispatch_event_for_actor("dev-1").id == dispatch.id
    assert _resolve_causation(state, "dev-1", log) == dispatch.id


@pytest.mark.parametrize(
    "field,value",
    [
        ("dispatch_id", "old-dispatch"),
        ("placement_epoch", 0),
        ("task_map_generation", "old-map"),
    ],
)
@pytest.mark.parametrize("shell", [False, True])
def test_old_dispatch_cannot_override_current_placement(
    assignment, monkeypatch, field, value, shell
):
    _, _, workspace, log, writer, _ = assignment
    dispatch = [
        e for e in log.read_all() if e.type == "task.pipeline.stage.dispatched"
    ][-1]
    writer.append(
        ZfEvent(
            type="task.pipeline.stage.dispatched",
            task_id="T-NEW",
            origin="kernel",
            payload={
                **dispatch.payload,
                field: value,
                "workdir": str(workspace.parent / "old/project"),
            },
        )
    )
    assert invoke(assignment, monkeypatch, workspace / "src/app.ts", shell=shell) == 0
    assert (
        invoke(
            assignment, monkeypatch, workspace.parent / "old/project/src/app.ts",
            shell=shell,
        )
        == 2
    )


def test_pipeline_write_guard_works_without_legacy_directory(assignment, monkeypatch):
    _, legacy, workspace, _, writer, prepared = assignment
    legacy.rmdir()
    assert invoke(assignment, monkeypatch, workspace / "src/app.ts") == 0
    assert (
        invoke(assignment, monkeypatch, workspace.parent / "other/project/src/app.ts")
        == 2
    )
    writer.append(
        ZfEvent(
            type="workflow.operation.settled",
            task_id="T-NEW",
            payload={"operation_id": prepared.operation_id, "workflow_run_id": "run-1"},
        )
    )
    assert invoke(assignment, monkeypatch, workspace / "src/app.ts") == 2
