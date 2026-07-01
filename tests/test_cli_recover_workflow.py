from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def test_recover_workflow_resume_pending_cli(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: cli-recover\n"
        "  state_dir: .zf\n"
        "session:\n"
        "  tmux_session: cli-recover\n"
        "workflow:\n"
        "  dag:\n"
        "    enabled: true\n"
        "    graph_review_test_judge_reconcile: true\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "    publishes: [dev.build.done]\n"
        "  - name: review\n"
        "    backend: mock\n"
        "    triggers: [static_gate.passed]\n"
        "    publishes: [review.approved, review.rejected]\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-CLI",
        title="cli",
        status="in_progress",
        assigned_to="dev",
    ))
    log = EventLog(state_dir / "events.jsonl")
    gate = ZfEvent(type="static_gate.passed", task_id="TASK-CLI")
    log.append(gate)

    rc = main(["recover", "workflow", "--resume-pending", "--json"])

    out = json.loads(capsys.readouterr().out)
    task = store.get("TASK-CLI")
    assert rc == 0
    assert out["applied"] == 1
    assert Path(out["projection_path"]).exists()
    assert task is not None
    assert task.assigned_to == "review"
    assert any(
        event.type == "task.assigned"
        and event.payload.get("source") == "workflow_resume"
        and event.payload.get("trigger_event_id") == gate.id
        for event in log.read_all()
    )
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("source") == "workflow_resume"
        and event.payload.get("trigger_event_id") == gate.id
        for event in log.read_all()
    )


def test_recover_workflow_checkpoint_id_filters_cli(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: cli-recover\n"
        "  state_dir: .zf\n"
        "session:\n"
        "  tmux_session: cli-recover\n"
        "workflow:\n"
        "  dag:\n"
        "    enabled: true\n"
        "    graph_review_test_judge_reconcile: true\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "    publishes: [dev.build.done]\n"
        "  - name: review\n"
        "    backend: mock\n"
        "    triggers: [static_gate.passed]\n"
        "    publishes: [review.approved, review.rejected]\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-CLI",
        title="cli",
        status="in_progress",
        assigned_to="dev",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="static_gate.passed", task_id="TASK-CLI"))

    rc = main([
        "recover",
        "workflow",
        "--resume-pending",
        "--checkpoint-id",
        "wfres-does-not-exist",
        "--json",
    ])

    out = json.loads(capsys.readouterr().out)
    task = store.get("TASK-CLI")
    assert rc == 0
    assert out["applied"] == 0
    assert out["checkpoint_id"] == "wfres-does-not-exist"
    assert out["no_op_reason"] == "checkpoint not found"
    assert task is not None
    assert task.assigned_to == "dev"
    assert not [
        event for event in log.read_all()
        if event.type == "task.assigned"
        and event.payload.get("source") == "workflow_resume"
    ]


def test_recover_workflow_task_map_ref_override_cli(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: cli-recover\n"
        "  state_dir: .zf\n"
        "session:\n"
        "  tmux_session: cli-recover\n"
        "workflow:\n"
        "  dag:\n"
        "    enabled: true\n"
        "    graph_review_test_judge_reconcile: true\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "    publishes: [dev.build.done, dev.failed]\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    original_task_map = state_dir / "artifacts" / "plan" / "task_map.json"
    override_task_map = (
        state_dir / "artifacts" / "workflow-resume" / "operator" / "task_map.json"
    )
    original_task_map.parent.mkdir(parents=True)
    override_task_map.parent.mkdir(parents=True)
    original_task_map.write_text(
        json.dumps({"schema_version": "task-map.v1", "tasks": []}),
        encoding="utf-8",
    )
    override_task_map.write_text(
        json.dumps({"schema_version": "task-map.v1", "tasks": []}),
        encoding="utf-8",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-current",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "pdd_id": "CJMIN-R37",
            "task_map_ref": str(original_task_map),
            "source_commit": "base123",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "failed_children": [
                "queued-CJMIN-PACKAGING-DOCKER-SECURITY-001-8",
            ],
        },
        correlation_id="trace-r37",
    ))
    inspect_rc = main(["recover", "workflow", "--json"])
    inspect_out = json.loads(capsys.readouterr().out)
    checkpoint = inspect_out["projection"]["batch_checkpoints"][0]

    rc = main([
        "recover",
        "workflow",
        "--resume-pending",
        "--checkpoint-id",
        checkpoint["checkpoint_id"],
        "--task-map-ref",
        str(override_task_map),
        "--json",
    ])

    out = json.loads(capsys.readouterr().out)
    requeued = [
        event for event in log.read_all()
        if event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
    ]
    assert inspect_rc == 0
    assert rc == 0
    assert out["applied"] == 1
    assert requeued[0].payload["task_map_ref"] == str(override_task_map)
    assert requeued[0].payload["task_map_repair"]["kind"] == (
        "operator_task_map_override"
    )


def test_recover_workflow_rejects_explicit_state_dir_mismatch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: cli-recover\n"
        "  state_dir: .zf-current\n"
        "session:\n"
        "  tmux_session: cli-recover\n"
        "workflow:\n"
        "  dag:\n"
        "    enabled: true\n"
        "roles:\n"
        "  - name: dev\n"
        "    backend: mock\n"
        "    publishes: [dev.build.done]\n",
        encoding="utf-8",
    )
    old_state = tmp_path / ".zf-old"
    old_state.mkdir()
    (old_state / "kanban.json").write_text("[]\n", encoding="utf-8")
    SessionStore(old_state / "session.yaml").create(
        project_root=str(tmp_path / "old"),
    )
    log = EventLog(old_state / "events.jsonl")

    rc = main([
        "recover",
        "workflow",
        "--state-dir",
        str(old_state),
        "--resume-pending",
        "--json",
    ])

    out = json.loads(capsys.readouterr().out)
    events = log.read_all()
    assert rc == 1
    assert out["applied"] == 0
    assert out["rejected"] == 2
    assert {item["code"] for item in out["rejections"]} == {
        "state_dir_mismatch",
        "session_project_root_mismatch",
    }
    assert any(event.type == "workflow.resume.rejected" for event in events)


def test_apply_out_of_band_gate_dispatcher_executes(tmp_path: Path) -> None:
    # B7 (doc 91 P4 / R25 ISSUE-006): needs_gate_dispatch + dispatcher
    # → 直接执行孵化(applied=True, mode=out_of_band),不再只发标记。
    import json as _json

    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.events.writer import EventWriter
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_resume import WorkflowResumeCheckpoint
    from zf.runtime.workflow_resume_apply import _apply_checkpoint

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    blocking = writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={"pdd_id": "P-1"},
    ))
    checkpoint = WorkflowResumeCheckpoint(
        task_id="pi-core",
        last_completed_stage="candidate",
        expected_next_stage="review",
        expected_next_role="",
        blocking_event_id=blocking.id,
        last_trusted_event_id=blocking.id,
        evidence_event_ids=[blocking.id],
        safe_resume_action="needs_gate_dispatch",
        reason="ready",
        idempotency_key="wfres-test0001",
    )
    dispatched: list[str] = []
    result = _apply_checkpoint(
        store, writer, checkpoint,
        gate_dispatcher=lambda e: dispatched.append(e.id),
        events=log.read_all(),
    )
    assert result.applied is True
    assert dispatched == [blocking.id]
    applied = [
        e for e in log.read_all()
        if e.type == "workflow.resume.applied"
        and e.payload.get("mode") == "out_of_band_gate_dispatch"
    ]
    assert applied, "out-of-band apply 必须留痕"


def test_apply_without_dispatcher_keeps_marker_behavior(tmp_path: Path) -> None:
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.events.writer import EventWriter
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_resume import WorkflowResumeCheckpoint
    from zf.runtime.workflow_resume_apply import _apply_checkpoint

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    checkpoint = WorkflowResumeCheckpoint(
        task_id="pi-core",
        last_completed_stage="candidate",
        expected_next_stage="review",
        expected_next_role="",
        blocking_event_id="evt-x",
        last_trusted_event_id="evt-x",
        evidence_event_ids=[],
        safe_resume_action="needs_gate_dispatch",
        reason="ready",
        idempotency_key="wfres-test0002",
    )
    result = _apply_checkpoint(
        TaskStore(state_dir / "kanban.json"), writer, checkpoint,
    )
    # 无 dispatcher → 旧 marker 行为(向后兼容,主循环健康时仍可消费)
    assert result.applied is True or "stalled" in (result.reason or "")


def test_apply_terminal_closeout_marks_task_done(tmp_path: Path) -> None:
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.events.writer import EventWriter
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_resume import WorkflowResumeCheckpoint
    from zf.runtime.workflow_resume_apply import _apply_checkpoint

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-JUDGE", title="judge", status="in_progress"))
    judge = writer.append(ZfEvent(
        type="judge.passed",
        actor="zf-cli",
        task_id="TASK-JUDGE",
        payload={"status": "completed"},
    ))
    checkpoint = WorkflowResumeCheckpoint(
        task_id="TASK-JUDGE",
        last_completed_stage="judge",
        expected_next_stage="terminal:done",
        expected_next_role="",
        blocking_event_id=judge.id,
        last_trusted_event_id=judge.id,
        evidence_event_ids=[judge.id],
        safe_resume_action="needs_terminal_closeout",
        reason="ready",
        idempotency_key="wfres-terminal",
        source_event_type="judge.passed",
    )

    result = _apply_checkpoint(store, writer, checkpoint)
    events = log.read_all()
    task = store.get("TASK-JUDGE")

    assert result.applied is True
    assert result.reason == "task terminal closeout"
    assert task is not None
    assert task.status == "done"
    assert store.list_all() == []
    assert any(
        event.type == "task.status_changed"
        and event.task_id == "TASK-JUDGE"
        and event.payload.get("to") == "done"
        and event.payload.get("source") == "workflow_resume"
        for event in events
    )
    assert any(
        event.type == "task.done.evidence"
        and event.task_id == "TASK-JUDGE"
        and event.payload.get("idempotency_key") == "wfres-terminal"
        for event in events
    )


def test_terminal_closeout_old_marker_without_effect_can_reapply(tmp_path: Path) -> None:
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.events.writer import EventWriter
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_resume import WorkflowResumeCheckpoint
    from zf.runtime.workflow_resume_apply import (
        _apply_checkpoint,
        _idempotency_seen,
        _idempotent_resume_effect_seen,
    )

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-JUDGE", title="judge", status="in_progress"))
    judge = writer.append(ZfEvent(
        type="judge.passed",
        actor="zf-cli",
        task_id="TASK-JUDGE",
    ))
    checkpoint = WorkflowResumeCheckpoint(
        task_id="TASK-JUDGE",
        last_completed_stage="judge",
        expected_next_stage="terminal:done",
        expected_next_role="",
        blocking_event_id=judge.id,
        last_trusted_event_id=judge.id,
        evidence_event_ids=[judge.id],
        safe_resume_action="needs_terminal_closeout",
        reason="ready",
        idempotency_key="wfres-terminal",
        source_event_type="judge.passed",
    )
    writer.append(ZfEvent(
        type="workflow.resume.applied",
        actor="zf-cli",
        task_id="TASK-JUDGE",
        payload={
            "safe_resume_action": "needs_terminal_closeout",
            "idempotency_key": "wfres-terminal",
            "reason": "stage transition stalled",
        },
    ))
    events = log.read_all()

    assert _idempotency_seen(events, "wfres-terminal") is True
    assert _idempotent_resume_effect_seen(store, events, checkpoint) is False

    result = _apply_checkpoint(store, writer, checkpoint)

    assert result.applied is True
    assert store.get("TASK-JUDGE").status == "done"  # type: ignore[union-attr]
