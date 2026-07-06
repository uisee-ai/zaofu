from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.config.schema import (
    RoleConfig,
    WorkflowAffinityLaneConfig,
    WorkflowAffinityLaneProfileConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.workflow_resume import (
    WorkflowResumeCheckpoint,
    _idempotency_key,
    build_workflow_resume_projection,
)
from zf.runtime.workflow_resume_apply import _apply_checkpoint


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


def test_recover_workflow_allows_env_state_dir_override(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    runtime_state = tmp_path / ".zf-e2e"
    monkeypatch.setenv("ZF_STATE_DIR", str(runtime_state))
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: cli-recover\n"
        "  state_dir: .zf-default\n"
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
    runtime_state.mkdir()
    (runtime_state / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(runtime_state / "events.jsonl")

    rc = main([
        "recover",
        "workflow",
        "--state-dir",
        str(runtime_state),
        "--resume-pending",
        "--json",
    ])

    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["rejected"] == 0
    assert out["no_op_reason"] == "no pending resume action"
    assert not [event for event in log.read_all() if event.type == "workflow.resume.rejected"]


def test_recover_workflow_allows_session_backed_state_dir_override(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\n'
        "project:\n"
        "  name: cli-recover\n"
        "  state_dir: .zf-default\n"
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
    runtime_state = tmp_path / ".zf-e2e"
    runtime_state.mkdir()
    (runtime_state / "kanban.json").write_text("[]\n", encoding="utf-8")
    SessionStore(runtime_state / "session.yaml").create(project_root=str(tmp_path))
    log = EventLog(runtime_state / "events.jsonl")

    rc = main([
        "recover",
        "workflow",
        "--state-dir",
        str(runtime_state),
        "--resume-pending",
        "--json",
    ])

    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["rejected"] == 0
    assert out["no_op_reason"] == "no pending resume action"
    assert not [event for event in log.read_all() if event.type == "workflow.resume.rejected"]


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


def test_apply_rework_dispatch_maps_control_role_to_lane_impl(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    artifact_dir = state_dir / "artifacts" / "plan"
    artifact_dir.mkdir(parents=True)
    task_map = artifact_dir / "task_map.json"
    task_map.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "lane_profile": "refactor-slot",
            "affinity_key": "affinity_tag",
            "lane_affinity_map": {"core-foundation": "lane0"},
            "tasks": [
                {
                    "task_id": "CANGJIE-CORE-001",
                    "affinity_tag": "core-foundation",
                },
            ],
        }),
        encoding="utf-8",
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="CANGJIE-CORE-001",
        title="core",
        status="in_progress",
        assigned_to="orchestrator",
        contract=TaskContract(
            owner_role="dev-core",
            evidence_contract={
                "source": "refactor_task_map",
                "affinity_tag": "core-foundation",
                "source_refs": {"task_map_ref": str(task_map)},
            },
        ),
    ))
    config = ZfConfig(
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                role_kind="writer",
                triggers=["task.assigned"],
            ),
        ],
        workflow=WorkflowConfig(affinity_lanes={
            "refactor-slot": WorkflowAffinityLaneProfileConfig(
                affinity_key="affinity_tag",
                lanes=[
                    WorkflowAffinityLaneConfig(
                        id="lane0",
                        impl="dev-lane-0",
                        verify="verify-lane-0",
                    ),
                ],
            ),
        }),
    )
    checkpoint = WorkflowResumeCheckpoint(
        task_id="CANGJIE-CORE-001",
        last_completed_stage="impl",
        expected_next_stage="rework:fanout.cancelled",
        expected_next_role="orchestrator",
        blocking_event_id="evt-block",
        last_trusted_event_id="evt-block",
        evidence_event_ids=["evt-block"],
        safe_resume_action="needs_rework_dispatch",
        reason="ready",
        idempotency_key="wfres-lane-core",
        source_event_type="fanout.cancelled",
    )

    result = _apply_checkpoint(
        store,
        writer,
        checkpoint,
        config=config,
        state_dir=state_dir,
    )

    assert result.applied is True
    task = store.get("CANGJIE-CORE-001")
    assert task is not None
    assert task.assigned_to == "dev-lane-0"
    assigned = [
        event for event in log.read_all()
        if event.type == "task.assigned"
    ][-1]
    assert assigned.payload["assignee"] == "dev-lane-0"
    assert assigned.payload["target_resolution"] == "lane_affinity.impl"


def test_projection_keeps_wrong_rework_action_pending_for_lane_task(
    tmp_path: Path,
) -> None:
    config = ZfConfig(
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(name="critic", backend="mock", role_kind="reader"),
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                role_kind="writer",
                triggers=["task.assigned"],
            ),
        ],
        workflow=WorkflowConfig(
            rework_routing={"fanout.cancelled": "orchestrator"},
            affinity_lanes={
                "refactor-slot": WorkflowAffinityLaneProfileConfig(
                    affinity_key="affinity_tag",
                    lanes=[
                        WorkflowAffinityLaneConfig(
                            id="lane0",
                            impl="dev-lane-0",
                            verify="verify-lane-0",
                        ),
                    ],
                ),
            },
        ),
    )
    task = Task(
        id="CANGJIE-CORE-001",
        title="core",
        status="in_progress",
        assigned_to="critic",
        contract=TaskContract(
            owner_role="dev-core",
            evidence_contract={
                "source": "refactor_task_map",
                "affinity_tag": "core-foundation",
            },
        ),
    )
    cancelled = ZfEvent(
        id="evt-cancelled",
        type="fanout.cancelled",
        task_id=task.id,
        payload={"fanout_id": "fanout-impl"},
    )
    wrong_rework = ZfEvent(
        id="evt-wrong-rework",
        type="task.rework.requested",
        task_id=task.id,
        payload={
            "role": "orchestrator",
            "assignee": "orchestrator",
            "source": "workflow_resume",
            "trigger_event_type": "fanout.cancelled",
            "trigger_event_id": cancelled.id,
        },
    )
    stale_handoff = ZfEvent(
        id="evt-stale-handoff",
        type="task.assigned",
        task_id=task.id,
        payload={
            "role": "critic",
            "assignee": "critic",
            "source": "pending_handoff_reconcile",
            "trigger_event": "arch.proposal.done",
        },
    )

    projection = build_workflow_resume_projection(
        tmp_path / ".zf",
        config,
        events=[cancelled, wrong_rework, stale_handoff],
        tasks=[task],
    )

    checkpoint = projection["checkpoints"][0]
    assert checkpoint["safe_resume_action"] == "needs_assignment_correction"
    assert checkpoint["expected_next_role"] == ""
    assert checkpoint["blocking_event_id"] == cancelled.id
    assert checkpoint["reason"] == (
        "current assignment targets non-runnable lane role: critic"
    )
    assert checkpoint["idempotency_key"] != _idempotency_key(
        task.id,
        cancelled.id,
        "needs_assignment_correction",
        "dev-lane-0",
    )


def test_apply_assignment_correction_maps_invalid_lane_assignee(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    artifact_dir = state_dir / "artifacts" / "plan"
    artifact_dir.mkdir(parents=True)
    task_map = artifact_dir / "task_map.json"
    task_map.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "lane_profile": "refactor-slot",
            "affinity_key": "affinity_tag",
            "lane_affinity_map": {"core-foundation": "lane0"},
            "tasks": [
                {
                    "task_id": "CANGJIE-CORE-001",
                    "affinity_tag": "core-foundation",
                },
            ],
        }),
        encoding="utf-8",
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="CANGJIE-CORE-001",
        title="core",
        status="in_progress",
        assigned_to="critic",
        contract=TaskContract(
            owner_role="dev-core",
            evidence_contract={
                "source": "refactor_task_map",
                "affinity_tag": "core-foundation",
                "source_refs": {"task_map_ref": str(task_map)},
            },
        ),
    ))
    config = ZfConfig(
        roles=[
            RoleConfig(name="critic", backend="mock", role_kind="reader"),
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                role_kind="writer",
                triggers=["task.assigned"],
            ),
        ],
        workflow=WorkflowConfig(affinity_lanes={
            "refactor-slot": WorkflowAffinityLaneProfileConfig(
                affinity_key="affinity_tag",
                lanes=[
                    WorkflowAffinityLaneConfig(
                        id="lane0",
                        impl="dev-lane-0",
                        verify="verify-lane-0",
                    ),
                ],
            ),
        }),
    )
    checkpoint = WorkflowResumeCheckpoint(
        task_id="CANGJIE-CORE-001",
        last_completed_stage="impl",
        expected_next_stage="assignment_correction",
        expected_next_role="",
        blocking_event_id="evt-block",
        last_trusted_event_id="evt-block",
        evidence_event_ids=["evt-block"],
        safe_resume_action="needs_assignment_correction",
        reason="current assignment targets non-runnable lane role: critic",
        idempotency_key="wfres-lane-core-assignment",
        source_event_type="fanout.cancelled",
    )

    result = _apply_checkpoint(
        store,
        writer,
        checkpoint,
        config=config,
        state_dir=state_dir,
    )

    assert result.applied is True
    task = store.get("CANGJIE-CORE-001")
    assert task is not None
    assert task.assigned_to == "dev-lane-0"
    assigned = [
        event for event in log.read_all()
        if event.type == "task.assigned"
    ][-1]
    assert assigned.payload["source"] == "workflow_resume_assignment_correction"
    assert assigned.payload["target_resolution"] == "lane_affinity.impl"


def test_apply_assignment_correction_maps_special_affinity_from_dispatch(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    artifact_dir = state_dir / "artifacts" / "plan"
    artifact_dir.mkdir(parents=True)
    task_map = artifact_dir / "task_map.json"
    task_map.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "lane_profile": "refactor-slot",
            "affinity_key": "affinity_tag",
            "lane_affinity_map": {"assembly": "assembly"},
            "tasks": [
                {
                    "task_id": "CANGJIE-ASSEMBLY-001",
                    "affinity_tag": "assembly",
                    "owner_role": "assembly",
                },
            ],
        }),
        encoding="utf-8",
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="CANGJIE-ASSEMBLY-001",
        title="assembly",
        status="in_progress",
        assigned_to="orchestrator",
        active_dispatch_id=(
            "run-fanout-cangjie-slice-implementation-evt-1234-"
            "dev-lane-1-CANGJIE-ASSEMBLY-001"
        ),
        contract=TaskContract(
            owner_role="assembly",
            evidence_contract={
                "source": "refactor_task_map",
                "affinity_tag": "assembly",
                "source_refs": {"task_map_ref": str(task_map)},
            },
        ),
    ))
    config = ZfConfig(
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                role_kind="writer",
                triggers=["task.assigned"],
            ),
            RoleConfig(
                name="dev-lane-1",
                backend="mock",
                role_kind="writer",
                triggers=["task.assigned"],
            ),
        ],
        workflow=WorkflowConfig(affinity_lanes={
            "refactor-slot": WorkflowAffinityLaneProfileConfig(
                affinity_key="affinity_tag",
                lanes=[
                    WorkflowAffinityLaneConfig(
                        id="lane0",
                        impl="dev-lane-0",
                        verify="verify-lane-0",
                    ),
                    WorkflowAffinityLaneConfig(
                        id="lane1",
                        impl="dev-lane-1",
                        verify="verify-lane-1",
                    ),
                ],
            ),
        }),
    )
    checkpoint = WorkflowResumeCheckpoint(
        task_id="CANGJIE-ASSEMBLY-001",
        last_completed_stage="impl",
        expected_next_stage="assignment_correction",
        expected_next_role="",
        blocking_event_id="evt-block",
        last_trusted_event_id="evt-block",
        evidence_event_ids=["evt-block"],
        safe_resume_action="needs_assignment_correction",
        reason="current assignment targets non-runnable lane role: orchestrator",
        idempotency_key="wfres-assembly-assignment",
        source_event_type="fanout.cancelled",
    )

    result = _apply_checkpoint(
        store,
        writer,
        checkpoint,
        config=config,
        state_dir=state_dir,
    )

    assert result.applied is True
    task = store.get("CANGJIE-ASSEMBLY-001")
    assert task is not None
    assert task.assigned_to == "dev-lane-1"
    assigned = [
        event for event in log.read_all()
        if event.type == "task.assigned"
    ][-1]
    assert assigned.payload["target_resolution"] == "lane_affinity.impl"


def test_projection_keeps_wrong_rework_action_pending_for_unassigned_lane_task(
    tmp_path: Path,
) -> None:
    config = ZfConfig(
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                role_kind="writer",
                triggers=["task.assigned"],
            ),
        ],
        workflow=WorkflowConfig(
            rework_routing={"fanout.cancelled": "orchestrator"},
            affinity_lanes={
                "refactor-slot": WorkflowAffinityLaneProfileConfig(
                    affinity_key="affinity_tag",
                    lanes=[
                        WorkflowAffinityLaneConfig(
                            id="lane0",
                            impl="dev-lane-0",
                            verify="verify-lane-0",
                        ),
                    ],
                ),
            },
        ),
    )
    task = Task(
        id="CANGJIE-CORE-001",
        title="core",
        status="in_progress",
        assigned_to="",
        contract=TaskContract(
            owner_role="dev-core",
            evidence_contract={
                "source": "refactor_task_map",
                "affinity_tag": "core-foundation",
            },
        ),
    )
    cancelled = ZfEvent(
        id="evt-cancelled",
        type="fanout.cancelled",
        task_id=task.id,
        payload={"fanout_id": "fanout-impl"},
    )
    wrong_rework = ZfEvent(
        id="evt-wrong-rework",
        type="task.rework.requested",
        task_id=task.id,
        payload={
            "role": "orchestrator",
            "assignee": "orchestrator",
            "source": "workflow_resume",
            "trigger_event_type": "fanout.cancelled",
            "trigger_event_id": cancelled.id,
        },
    )

    projection = build_workflow_resume_projection(
        tmp_path / ".zf",
        config,
        events=[cancelled, wrong_rework],
        tasks=[task],
    )

    checkpoint = projection["checkpoints"][0]
    assert checkpoint["safe_resume_action"] == "needs_rework_dispatch"
    assert checkpoint["reason"] == (
        "existing next action targets non-runnable lane role"
    )
    assert checkpoint["idempotency_key"] != _idempotency_key(
        task.id,
        cancelled.id,
        "needs_rework_dispatch",
        "orchestrator",
    )


def test_apply_rework_dispatch_rejects_control_role_without_lane_mapping(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="CANGJIE-CORE-001",
        title="core",
        status="in_progress",
        assigned_to="orchestrator",
        contract=TaskContract(
            owner_role="dev-core",
            evidence_contract={
                "source": "refactor_task_map",
                "affinity_tag": "core-foundation",
            },
        ),
    ))
    config = ZfConfig(
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                role_kind="writer",
                triggers=["task.assigned"],
            ),
        ],
        workflow=WorkflowConfig(affinity_lanes={
            "refactor-slot": WorkflowAffinityLaneProfileConfig(
                affinity_key="affinity_tag",
                lanes=[
                    WorkflowAffinityLaneConfig(id="lane0", impl="dev-lane-0"),
                ],
            ),
        }),
    )
    checkpoint = WorkflowResumeCheckpoint(
        task_id="CANGJIE-CORE-001",
        last_completed_stage="impl",
        expected_next_stage="rework:fanout.cancelled",
        expected_next_role="orchestrator",
        blocking_event_id="evt-block",
        last_trusted_event_id="evt-block",
        evidence_event_ids=["evt-block"],
        safe_resume_action="needs_rework_dispatch",
        reason="ready",
        idempotency_key="wfres-lane-core-missing",
        source_event_type="fanout.cancelled",
    )

    result = _apply_checkpoint(
        store,
        writer,
        checkpoint,
        config=config,
        state_dir=state_dir,
    )

    assert result.applied is False
    assert result.reason.startswith("rejected:")
    assert store.get("CANGJIE-CORE-001").assigned_to == "orchestrator"  # type: ignore[union-attr]
    assert any(event.type == "workflow.resume.rejected" for event in log.read_all())


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
