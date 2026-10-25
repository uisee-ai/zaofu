from dataclasses import asdict
from types import SimpleNamespace

import pytest

from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.task_doc import (
    compute_task_capsule_revisions,
    verify_task_capsule,
    write_task_doc,
)
from zf.runtime.task_pipeline_dispatch import dispatch_task_pipeline_stage
from zf.runtime.task_pipeline_identity import task_pipeline_operation_identity
from zf.runtime.task_pipeline_runtime import TaskPipelineRuntimeError


@pytest.fixture
def dispatch_fixture(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    store = TaskStore(state / "kanban.json")
    task = Task(id="TASK-1", title="current", contract=TaskContract(behavior="current"))
    store.add(task)
    log = EventLog(state / "events.jsonl")
    role = RoleConfig(
        name="dev",
        instance_id="dev-0",
        backend="mock",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    )
    runtime = SimpleNamespace(
        state_dir=state,
        project_root=tmp_path,
        config=SimpleNamespace(),
        task_store=store,
        event_writer=EventWriter(log),
        _find_role_by_instance=lambda _: role,
    )
    workspace = SimpleNamespace(
        enabled=True,
        mode="worktree",
        project_path=str(tmp_path),
        workdir=str(tmp_path),
        branch="task/test",
        base_commit="d" * 40,
    )
    monkeypatch.setattr(
        "zf.runtime.task_workspaces.TaskWorkspaceManager",
        lambda **kw: SimpleNamespace(prepare=lambda **kw: workspace),
    )
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_terminal.task_pipeline_workspace_base",
        lambda *a, **kw: "d" * 40,
    )
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_runtime._activate_task_stage_binding",
        lambda *a, **kw: {},
    )
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_runtime._role_config_digest",
        lambda *a, **kw: "digest",
    )
    package = {
        "plan_artifact_package_id": "planpkg-" + "b" * 64,
        "plan_artifact_package_ref": "artifacts/package.json",
        "plan_artifact_package_digest": "b" * 64,
    }
    snapshot = {
        "task_ref": "task/TASK-1",
        **compute_task_capsule_revisions(task),
        **package,
    }
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_targets.prepare_contract_snapshot",
        lambda *a, **kw: (
            snapshot,
            {"ref": "artifacts/snapshot.json", "sha256": "e" * 64},
        ),
    )
    prepared = []

    def prepare(*a, **kw):
        prepared.append(kw)
        return SimpleNamespace(
            should_dispatch=False,
            operation_id=task_pipeline_operation_identity(
                workflow_run_id="run-1",
                task_id=task.id,
                task_map_generation="map-new",
                stage="impl",
                stage_revision="implementation-result.v1",
                operation_generation=1,
            ).operation_id,
        )

    monkeypatch.setattr(
        "zf.runtime.call_result_runtime.prepare_call_operation", prepare
    )

    def dispatch():
        return dispatch_task_pipeline_stage(
            runtime,
            policy={},
            task=task,
            assignment={
                "stage": "impl",
                "role_instance": "dev-0",
                "operation_generation": 1,
            },
            generation_context={
                "workflow_run_id": "run-1",
                "task_map_generation": "map-new",
                "generation_admitted_event_id": "evt-admitted",
                **package,
            },
            operation_rows=[],
            attempt_rows=[],
        )

    return runtime, task, snapshot, prepared, dispatch


@pytest.mark.parametrize("old_doc", [False, True])
def test_dispatch_refreshes_capsule_before_preparing_operation(
    dispatch_fixture, old_doc
):
    runtime, task, _, prepared, dispatch = dispatch_fixture
    if old_doc:
        write_task_doc(
            runtime.state_dir, Task(id=task.id, contract=TaskContract(behavior="old"))
        )
    assert verify_task_capsule(runtime.state_dir, task)
    before = asdict(runtime.task_store.get(task.id))
    dispatch()
    assert verify_task_capsule(runtime.state_dir, task) == []
    assert asdict(runtime.task_store.get(task.id)) == before
    assert len(prepared) == 1


def test_dispatch_rejects_snapshot_mismatch_before_operation(dispatch_fixture):
    _, _, snapshot, prepared, dispatch = dispatch_fixture
    snapshot["contract_revision"] = "contract-old"
    with pytest.raises(TaskPipelineRuntimeError, match="capsule.*snapshot"):
        dispatch()
    assert prepared == []


def test_dispatch_stops_when_capsule_publication_fails(dispatch_fixture, monkeypatch):
    _, _, _, prepared, dispatch = dispatch_fixture

    def fail(*a, **kw):
        raise OSError("projection unavailable")

    monkeypatch.setattr("zf.runtime.task_doc.write_task_doc", fail)
    with pytest.raises(OSError, match="projection unavailable"):
        dispatch()
    assert prepared == []


def test_dispatch_rejects_canonical_contract_changed_since_selection(dispatch_fixture):
    runtime, task, _, prepared, dispatch = dispatch_fixture
    runtime.task_store.update(task.id, contract=TaskContract(behavior="newer"))
    with pytest.raises(TaskPipelineRuntimeError, match="capsule.*snapshot"):
        dispatch()
    assert prepared == []


def test_transport_receives_fresh_capsule_and_real_briefing(
    dispatch_fixture, monkeypatch
):
    runtime, task, _, _, dispatch = dispatch_fixture
    from zf.runtime.task_doc import task_doc_path

    write_task_doc(
        runtime.state_dir, Task(id=task.id, contract=TaskContract(behavior="old"))
    )
    original_prepare = __import__(
        "zf.runtime.call_result_runtime", fromlist=["prepare_call_operation"]
    ).prepare_call_operation

    def prepare(*args, **kwargs):
        operation = original_prepare(*args, **kwargs)
        operation.should_dispatch = True
        kwargs["payload"].update(
            semantic_result_submit_mode="blocking",
            operation_id=operation.operation_id,
            result_scratch_ref="tmp/result-submit/test/result.json",
        )
        return operation

    monkeypatch.setattr(
        "zf.runtime.call_result_runtime.prepare_call_operation", prepare
    )
    monkeypatch.setattr(
        "zf.runtime.call_result_runtime.mark_call_operation_started",
        lambda *a, **kw: None,
    )
    runtime._set_worker_state = lambda *a, **kw: None
    sent = []

    def send(role, briefing, prompt, context):
        assert verify_task_capsule(runtime.state_dir, task) == []
        assert "current" in task_doc_path(runtime.state_dir, task.id).read_text()
        assert briefing.read_text().startswith("Active task: TASK-1")
        assert "task_context_stale" in briefing.read_text()
        sent.append(context)
        return context

    runtime._send_transport_task = send
    result = dispatch()
    assert result.action == "task_pipeline_dispatch"
    assert len(sent) == 1
    assert runtime.task_store.get(task.id).active_dispatch_id == sent[0].dispatch_id
    events = runtime.event_writer.event_log.read_all()
    assert [e.type for e in events] == [
        "task.doc.published",
        "task.dispatched",
        "task.pipeline.stage.dispatched",
    ]
