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
        event_log=log,
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
                stage=kw["stage_id"],
                stage_revision=("implementation-result.v1" if kw["stage_id"] == "impl" else "verification-result.v1"),
                operation_generation=kw["payload"]["operation_generation"],
            ).operation_id,
        )

    monkeypatch.setattr(
        "zf.runtime.call_result_runtime.prepare_call_operation", prepare
    )

    monkeypatch.setattr(
        "zf.runtime.task_pipeline_targets.prepare_verify_target", lambda *a, **kw: {},
    )

    def dispatch(generation=1, stage="impl"):
        return dispatch_task_pipeline_stage(
            runtime,
            policy={},
            task=task,
            assignment={
                "stage": stage,
                "role_instance": "dev-0",
                "operation_generation": generation,
            },
            generation_context={
                "workflow_run_id": "run-1",
                "flow_kind": "prd",
                "task_map_generation": "map-new",
                "generation_admitted_event_id": "evt-admitted",
                **package,
            },
            operation_rows=[],
            attempt_rows=[],
        )

    return runtime, task, snapshot, prepared, dispatch


@pytest.mark.parametrize("old_doc", [False, True])
def test_running_or_settled_operation_does_not_rewrite_capsule(
    dispatch_fixture, old_doc
):
    runtime, task, _, prepared, dispatch = dispatch_fixture
    if old_doc:
        write_task_doc(
            runtime.state_dir, Task(id=task.id, contract=TaskContract(behavior="old"))
        )
    from zf.runtime.task_doc import task_doc_path
    doc = task_doc_path(runtime.state_dir, task.id)
    previous = doc.read_bytes() if doc.exists() else None
    before = asdict(runtime.task_store.get(task.id))
    dispatch()
    assert (doc.read_bytes() if doc.exists() else None) == previous
    assert asdict(runtime.task_store.get(task.id)) == before
    assert len(prepared) == 1
    assert prepared[0]["payload"]["flow_kind"] == "prd"


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

    from zf.runtime import call_result_runtime
    original = call_result_runtime.prepare_call_operation

    def prepare(*a, **kw):
        op = original(*a, **kw)
        op.should_dispatch = True
        return op

    monkeypatch.setattr(call_result_runtime, "prepare_call_operation", prepare)
    monkeypatch.setattr("zf.runtime.task_doc.write_task_doc", fail)
    with pytest.raises(OSError, match="projection unavailable"):
        dispatch()
    assert len(prepared) == 1


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
        assert "## Read Order (authoritative)" in briefing.read_text()
        assert str(task_doc_path(runtime.state_dir, task.id).resolve()) in briefing.read_text()
        assert "Controlled Artifact Inputs" in briefing.read_text()
        assert "## Worker Execution Envelope" in briefing.read_text()
        assert context.worker_envelope_digest
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


@pytest.mark.parametrize("old_dispatch,generation", [("", 1), ("tpd-old-p1", 2)])
@pytest.mark.parametrize("send_fails", [False, True])
@pytest.mark.parametrize("stage", ["impl", "verify"])
def test_dispatch_document_identity_matches_transport_before_delivery(
    dispatch_fixture, monkeypatch, old_dispatch, generation, send_fails, stage
):
    import json
    from zf.runtime.task_doc import task_doc_path
    from zf.runtime import call_result_runtime

    runtime, task, _, _, dispatch = dispatch_fixture
    feedback = [{
        "rework_item_id": "RW-01", "reason": "大整数边界必须保留；不省略用户约束",
        "evidence_refs": ["evidence/full-failure.log"],
        "reproduction_commands": ["python -m unittest tests.test_boundary"],
    }, {
        "rework_item_id": "RW-02", "reason": "大指数失败是独立问题",
        "evidence_refs": ["evidence/exponent.log"],
    }]
    monkeypatch.setattr("zf.runtime.task_pipeline_runtime._verify_rework_feedback", lambda *a, **kw: feedback.copy())
    runtime.task_store.update(task.id, active_dispatch_id=old_dispatch, assigned_to="previous-role")
    before = asdict(runtime.task_store.get(task.id))
    original = call_result_runtime.prepare_call_operation

    def prepare(*args, **kw):
        op = original(*args, **kw)
        op.should_dispatch = True
        kw["payload"].update(
            semantic_result_submit_mode="blocking", operation_id=op.operation_id,
            result_scratch_ref="tmp/result-submit/test/result.json",
        )
        return op

    monkeypatch.setattr(call_result_runtime, "prepare_call_operation", prepare)
    monkeypatch.setattr(call_result_runtime, "mark_call_operation_started", lambda *a, **kw: None)
    runtime._set_worker_state = lambda *a, **kw: None
    sent = []

    def send(role, briefing, prompt, context):
        doc = task_doc_path(runtime.state_dir, task.id)
        manifest = json.loads((doc.parent / "manifest.json").read_text())
        assert manifest["active_dispatch_id"] == context.dispatch_id
        assert manifest["dispatch_id"] == context.dispatch_id
        assert manifest["assigned_to"] == context.instance_id
        assert f"active_dispatch_id: {context.dispatch_id}\n" in doc.read_text()
        assert f"- dispatch_id: `{context.dispatch_id}`" in briefing.read_text()
        assert str(doc.resolve()) in briefing.read_text()
        assert str((doc.parent / "source.md").resolve()) in briefing.read_text()
        assert str((doc.parent / "progress.md").resolve()) in briefing.read_text()
        text = briefing.read_text()
        assert f"cost --operation {context.operation_id} --json" in text
        compact = json.loads(text.split("## Compact Execution Context", 1)[1].split("```json\n", 1)[1].split("```", 1)[0])
        assert "attempt_id" not in compact  # exact identity remains in header
        assert f"- attempt_id: `{context.attempt_id}`" in text
        if generation > 1:
            recovered = json.loads(text.split("## Rework Feedback", 1)[1].split("```json\n", 1)[1].split("```", 1)[0])
            assert recovered == feedback
        assert asdict(runtime.task_store.get(task.id)) == before
        sent.append(context)
        if send_fails:
            raise OSError("transport offline")
        return context

    runtime._send_transport_task = send
    expected_sends = 2 if send_fails else 1
    if send_fails:
        with pytest.raises(OSError, match="transport offline"):
            dispatch(generation, stage)
        assert asdict(runtime.task_store.get(task.id)) == before
        assert not any(e.type == "task.dispatched" for e in runtime.event_log.read_all())
        # Reopen durable state as a restarted runtime would, then retry delivery.
        runtime.task_store = TaskStore(runtime.state_dir / "kanban.json")
        send_fails = False
        dispatch(generation, stage)
        assert runtime.task_store.get(task.id).active_dispatch_id == sent[-1].dispatch_id
    else:
        dispatch(generation, stage)
        assert runtime.task_store.get(task.id).active_dispatch_id == sent[0].dispatch_id
    assert len(sent) == expected_sends


@pytest.mark.parametrize("corrupt", ["manifest", "header", "body", "role"])
def test_capsule_identity_corruption_after_prepare_blocks_transport(
    dispatch_fixture, monkeypatch, corrupt
):
    import json
    from zf.runtime import call_result_runtime
    from zf.runtime.task_doc import task_doc_path

    runtime, task, _, _, dispatch = dispatch_fixture
    original = call_result_runtime.prepare_call_operation
    from zf.runtime import task_pipeline_briefing
    write_briefing = task_pipeline_briefing.write_task_pipeline_briefing
    before = asdict(runtime.task_store.get(task.id))

    def prepare(*args, **kw):
        op = original(*args, **kw)
        op.should_dispatch = True
        kw["payload"].update(
            semantic_result_submit_mode="blocking", operation_id=op.operation_id,
            result_scratch_ref="tmp/result-submit/test/result.json",
        )
        return op

    def corrupt_after_publish(*args, **kw):
        briefing = write_briefing(*args, **kw)
        path = task_doc_path(runtime.state_dir, task.id)
        if corrupt == "manifest":
            manifest_path = path.parent / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["active_dispatch_id"] = "old-dispatch"
            manifest_path.write_text(json.dumps(manifest))
        else:
            old = kw["payload"]["dispatch_id"]
            content = path.read_text()
            if corrupt == "header":
                content = content.replace(f"active_dispatch_id: {old}", "active_dispatch_id: old")
            elif corrupt == "body":
                content = content.replace(f"**Active dispatch id**: `{old}`", "**Active dispatch id**: `old`")
            else:
                content = content.replace("assigned_to: dev-0", "assigned_to: stale-role")
            path.write_text(content)
        return briefing

    monkeypatch.setattr(call_result_runtime, "prepare_call_operation", prepare)
    monkeypatch.setattr(task_pipeline_briefing, "write_task_pipeline_briefing", corrupt_after_publish)
    runtime._send_transport_task = lambda *a: pytest.fail("must not deliver stale context")
    with pytest.raises(TaskPipelineRuntimeError, match="delivery preflight"):
        dispatch()
    assert asdict(runtime.task_store.get(task.id)) == before
