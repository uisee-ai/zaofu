from __future__ import annotations

import json
from pathlib import Path

import yaml

from zf.core.config.schema import RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.supervisor_inspection import (
    SNAPSHOT_SCHEMA_VERSION,
    build_supervisor_snapshot,
    read_supervisor_snapshot,
    run_supervisor_inspection,
    write_supervisor_projection,
)
from zf.runtime.supervisor_plan_integrity import build_plan_integrity_projection
from zf.runtime.supervisor_attention import build_attention_items
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_supervisor_snapshot_projects_attention_and_plan_integrity(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="loop.started",
        actor="zf-cli",
    ))
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="autopilot.proposal.created",
        actor="autopilot",
        task_id="TASK-NOREF",
        payload={
            "dedupe_key": "stuck:TASK-NOREF",
            "severity": "high",
            "title": "worker appears stuck",
            "reason": "heartbeat stale",
            "signal": {"event_id": "evt-source"},
        },
    ))
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-NOREF",
        title="missing plan",
        status="in_progress",
        contract=TaskContract(
            acceptance="manual approval",
            acceptance_criteria=["用户可以完成主流程"],
        ),
    ))
    (state_dir / "role_sessions.yaml").write_text(
        yaml.safe_dump({
            "instance_meta": {
                "dev-1": {
                    "backend": "codex",
                    "last_heartbeat_at": "2026-05-27T00:00:00+00:00",
                    "last_heartbeat_payload": {
                        "state": "busy",
                        "current_task_id": "TASK-NOREF",
                    },
                },
            },
        }),
        encoding="utf-8",
    )
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "2026-05-27-0000-sample.md").write_text(
        "> 状态: active\n\n验收: 用户主流程可用。\n",
        encoding="utf-8",
    )

    snapshot = build_supervisor_snapshot(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
    )
    result = write_supervisor_projection(state_dir, snapshot)
    age_only_snapshot = json.loads(json.dumps(snapshot))
    age_only_snapshot["generated_at"] = "2026-05-27T01:00:00+00:00"
    age_only_snapshot["freshness"]["last_event_age_sec"] = 999
    age_only_snapshot["worker_summary"]["last_heartbeat_age_sec"] = 999
    age_only_snapshot["worker_summary"]["workers"][0]["last_heartbeat_age_sec"] = 999
    unchanged = write_supervisor_projection(state_dir, age_only_snapshot)

    assert snapshot["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert snapshot["project_id"] == "proj-test"
    assert snapshot["task_summary"]["active"] == 1
    assert snapshot["worker_summary"]["total"] == 1
    assert snapshot["plan_integrity"]["summary"]["missing_plan_refs"] == 1
    assert snapshot["plan_integrity"]["summary"]["doc_acceptance_without_verify"] == 1
    assert snapshot["plan_insights"]["summary"]["total"] >= 1
    assert snapshot["plan_insights"]["items"][0]["kind"] == "plan-insight.v1"
    assert {item["source"] for item in snapshot["attention_items"]} >= {
        "autopilot",
        "plan_integrity",
    }
    assert result["changed"] is True
    assert unchanged["changed"] is False
    assert Path(result["snapshot_path"]).exists()
    assert Path(result["attention_path"]).exists()
    assert Path(result["plan_integrity_path"]).exists()
    assert Path(result["plan_insights_path"]).exists()
    assert Path(result["control_loop_path"]).exists()
    assert Path(result["pane_probe_path"]).exists()
    assert read_supervisor_snapshot(state_dir)["project_id"] == "proj-test"


def test_plan_integrity_accepts_existing_contract_refs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    task = Task(
        id="TASK-REF",
        title="has plan",
        status="in_progress",
        contract=TaskContract(
            plan_ref="docs/design/01-plan.md",
            acceptance="step -> verify: pytest passes",
        ),
    )

    projection = build_plan_integrity_projection(
        state_dir,
        project_root=tmp_path,
        tasks=[task],
        events=[],
    )

    assert projection["summary"]["active_tasks"] == 1
    assert projection["summary"]["missing_plan_refs"] == 0
    assert projection["summary"]["weak_acceptance"] == 0


def test_supervisor_snapshot_flags_stale_active_run(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "session.yaml").write_text(
        yaml.safe_dump({"runtime_state": "active"}),
        encoding="utf-8",
    )
    store = TaskStore(state_dir / "kanban.json")
    for index in range(4):
        store.add(Task(
            id=f"TASK-{index}",
            title=f"Task {index}",
            status="in_progress",
        ))
    events = [
        ZfEvent(
            id="progress-old",
            type="dev.build.done",
            actor="dev-1",
            ts="2026-05-27T00:00:00+00:00",
        ),
        ZfEvent(
            id="failure-tail",
            type="task.ref.rejected",
            actor="zf-cli",
            ts="2026-05-27T00:30:00+00:00",
        ),
    ]

    snapshot = build_supervisor_snapshot(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        events=events,
    )

    stale = [
        item for item in snapshot["attention_items"]
        if item.get("source") == "stale_active_run"
    ]
    assert stale
    assert stale[0]["suggested_action"]["kind"] == "recover_stale_active_run"
    assert stale[0]["source_event_ids"] == ["failure-tail"]


def test_supervisor_snapshot_does_not_flag_active_run_with_recent_heartbeat(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "session.yaml").write_text(
        yaml.safe_dump({"runtime_state": "active"}),
        encoding="utf-8",
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="Task 1",
        status="in_progress",
    ))
    events = [
        ZfEvent(
            id="failure-tail",
            type="task.ref.rejected",
            actor="zf-cli",
            ts="2026-05-27T00:00:00+00:00",
        ),
        ZfEvent(
            id="hb-recent",
            type="worker.heartbeat",
            actor="dev-1",
        ),
    ]

    snapshot = build_supervisor_snapshot(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        events=events,
    )

    assert not [
        item for item in snapshot["attention_items"]
        if item.get("source") == "stale_active_run"
    ]


def test_supervisor_inspection_emits_deduped_high_attention_event(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="autopilot.proposal.created",
        actor="autopilot",
        task_id="TASK-STUCK",
        payload={
            "dedupe_key": "stuck:TASK-STUCK",
            "severity": "high",
            "title": "worker appears stuck",
            "reason": "heartbeat stale",
            "signal": {"event_id": "evt-source"},
        },
    ))

    first = run_supervisor_inspection(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )
    second = run_supervisor_inspection(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )

    attention_events = [
        event for event in log.read_all()
        if event.type == "runtime.attention.needed"
    ]
    assert first["attention_events_emitted"] == 1
    assert first["control_loop_events_emitted"] == 2
    assert second["attention_events_emitted"] == 0
    assert second["control_loop_events_emitted"] == 0
    assert len(attention_events) == 1
    assert attention_events[0].actor == "zf-supervisor"
    assert attention_events[0].task_id == "TASK-STUCK"
    assert attention_events[0].payload["project_id"] == "proj-test"
    assert attention_events[0].payload["fingerprint"] == "autopilot:stuck:TASK-STUCK"
    assert attention_events[0].payload["projection_ref"]["snapshot_sha256"]
    diagnostic_ref = attention_events[0].payload["diagnostic_ref"]
    assert diagnostic_ref["ref_schema_version"] == "sidecar-ref.v1"
    hydrated = hydrate_sidecar_ref(state_dir, diagnostic_ref)
    assert hydrated.payload["attention"]["attention_id"] == attention_events[0].payload["attention_id"]
    types = [event.type for event in log.read_all()]
    assert types.count("supervisor.decision.recorded") == 1
    assert types.count("owner.visible_message.requested") == 1


def test_supervisor_inspection_requests_autoresearch_for_runtime_bug(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="zaofu.bug.detected",
        actor="zf-cli",
        payload={
            "signature": "dispatch_loop",
            "confidence": "high",
            "suggested_fix_area": "src/zf/runtime/orchestrator.py",
            "evidence_event_ids": ["evt-a", "evt-b"],
        },
    ))

    first = run_supervisor_inspection(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )
    second = run_supervisor_inspection(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )

    types = [event.type for event in log.read_all()]
    invocations = [
        event for event in log.read_all()
        if event.type == "autoresearch.invocation.requested"
    ]
    assert first["control_loop_events_emitted"] == 3
    assert second["control_loop_events_emitted"] == 0
    assert types.count("runtime.attention.needed") == 1
    assert types.count("supervisor.decision.recorded") == 1
    assert types.count("owner.visible_message.requested") == 1
    assert len(invocations) == 1
    assert invocations[0].payload["level"] == "diagnose"
    assert invocations[0].payload["apply_policy"] == "proposal_only"
    assert invocations[0].payload["sandbox_required"] is True


def test_supervisor_routes_workflow_batch_resume_to_run_manager(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task_map.ready",
        id="evt-task-map",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "trace_id": "trace-r37",
            "task_map_ref": ".zf/artifacts/CJMIN-R37/task_map.json",
            "source_index_ref": ".zf/artifacts/CJMIN-R37/source_index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "target_ref": "dev",
        },
        correlation_id="trace-r37",
    ))
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        id="evt-aggregate",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r37",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "failure_event": "integration.failed",
            "pdd_id": "CJMIN-R37",
            "feature_id": "CJMIN-R37",
            "candidate_ref": "cand/CJMIN-R37",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "completed_task_ids": ["CJMIN-GATEWAY-001"],
            "failed_children": ["dev-lane-0-CJMIN-ASSEMBLY-001"],
        },
        correlation_id="trace-r37",
    )
    log.append(aggregate)
    log.append(ZfEvent(
        type="integration.failed",
        id="evt-integration-failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-r37",
            "pdd_id": "CJMIN-R37",
            "reason": "assembly failed",
        },
        causation_id=aggregate.id,
        correlation_id="trace-r37",
    ))
    config = ZfConfig(
        session=SessionConfig(tmux_session="zf-test"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )

    result = run_supervisor_inspection(
        state_dir,
        config=config,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )
    second = run_supervisor_inspection(
        state_dir,
        config=config,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )

    snapshot = result["snapshot"]
    workflow_items = [
        item for item in snapshot["attention_items"]
        if item.get("source") == "workflow_resume"
    ]
    invocations = [
        event for event in log.read_all()
        if event.type == "autoresearch.invocation.requested"
    ]
    assert snapshot["workflow_resume"]["summary"]["batch_pending"] == 1
    assert result["attention_events_emitted"] == 1
    # 131 §16.3-4 triage-first 闸:workflow_resume 非人类必需项只记
    # decision,不发 owner.visible_message.requested → 1(原为 2)。
    assert result["control_loop_events_emitted"] == 1
    assert second["control_loop_events_emitted"] == 0
    assert workflow_items
    assert workflow_items[0]["suggested_route"] == "run_manager_recovery"
    assert workflow_items[0]["suggested_action"]["kind"] == "workflow-batch-resume"
    assert workflow_items[0]["suggested_action"]["safe_resume_action"] == "repair_failed_children"
    assert "workflow_resume_path" in result
    assert len(invocations) == 0


def test_supervisor_requests_autoresearch_for_human_escalate(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="human.escalate",
        id="evt-escalate",
        actor="zf-cli",
        task_id="CJMIN-ASSEMBLY-001",
        payload={
            "pdd_id": "CJMIN-R37",
            "rework_source": "integration.failed",
            "reason": "candidate rework attempts exhausted",
        },
    ))

    result = run_supervisor_inspection(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )

    events = log.read_all()
    assert result["attention_events_emitted"] == 1
    assert result["control_loop_events_emitted"] == 3
    assert any(event.type == "owner.visible_message.requested" for event in events)
    invocations = [
        event for event in events
        if event.type == "autoresearch.invocation.requested"
    ]
    assert len(invocations) == 1
    assert invocations[0].payload["fingerprint"] == "human_escalate:CJMIN-R37"


def test_supervisor_requests_autoresearch_for_dispatch_failed_signal(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    for index in range(3):
        log.append(ZfEvent(
            type="orchestrator.dispatch_failed",
            id=f"evt-dispatch-{index}",
            actor="zf-cli",
            payload={
                "trigger_event_id": "evt-trigger-1",
                "error": (
                    "refusing to send task to orchestrator: pane is not "
                    "running an agent process (current_command=node)"
                ),
            },
        ))

    result = run_supervisor_inspection(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )

    events = log.read_all()
    assert result["attention_events_emitted"] == 1
    invocations = [
        event for event in events
        if event.type == "autoresearch.invocation.requested"
    ]
    assert len(invocations) == 1
    assert "orchestrator.dispatch_failed" in invocations[0].payload["fingerprint"]


def test_supervisor_attention_ack_prevents_reemit_and_updates_summary(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="autopilot.proposal.created",
        actor="autopilot",
        task_id="TASK-STUCK",
        payload={
            "dedupe_key": "stuck:TASK-STUCK",
            "severity": "high",
            "title": "worker appears stuck",
            "reason": "heartbeat stale",
        },
    ))
    first = run_supervisor_inspection(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )
    needed = [
        event for event in log.read_all()
        if event.type == "runtime.attention.needed"
    ][0]
    log.append(ZfEvent(
        type="runtime.attention.acknowledged",
        actor="operator",
        payload={
            "attention_id": needed.payload["attention_id"],
            "fingerprint": needed.payload["fingerprint"],
        },
    ))
    second = run_supervisor_inspection(
        state_dir,
        project_root=tmp_path,
        project_id="proj-test",
        emit_attention_events=True,
    )

    attention_events = [
        event for event in log.read_all()
        if event.type == "runtime.attention.needed"
    ]
    assert first["attention_events_emitted"] == 1
    assert second["attention_events_emitted"] == 0
    assert len(attention_events) == 1
    assert second["snapshot"]["attention_items"][0]["status"] == "acknowledged"
    assert second["snapshot"]["attention_summary"]["by_status"]["acknowledged"] == 1


def test_supervisor_snapshot_projects_control_loop_context_and_skill_provenance(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "skills.lock.json").write_text(
        json.dumps({
            "version": 1,
            "generated_at": "2026-06-01T00:00:00+00:00",
            "skills": [
                {
                    "role": "dev",
                    "instance_id": "dev-1",
                    "backend": "codex",
                    "name": "plan",
                    "source_name": "agent-skills",
                    "source": "/skills/plan/SKILL.md",
                    "sha256": "abc",
                    "status": "resolved",
                    "warnings": [],
                    "collision_candidates": [],
                },
                {
                    "role": "review",
                    "instance_id": "review-1",
                    "backend": "codex",
                    "name": "review",
                    "source_name": "project",
                    "status": "invalid",
                    "warnings": ["missing frontmatter"],
                    "collision_candidates": ["agent-skills:review"],
                },
            ],
        }),
        encoding="utf-8",
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.context.compact.requested",
        actor="zf-runtime",
        task_id="TASK-CONTEXT",
        payload={"instance_id": "dev-1"},
    ))
    log.append(ZfEvent(
        type="worker.context.compact.failed",
        actor="zf-runtime",
        task_id="TASK-CONTEXT",
        payload={"instance_id": "dev-1", "retry_budget_exhausted": True},
    ))
    log.append(ZfEvent(
        type="owner.visible_message.delivery_attempted",
        actor="zf-supervisor",
        payload={"message_id": "omsg-1", "target": "feishu"},
    ))
    log.append(ZfEvent(
        type="owner.visible_message.failed",
        actor="zf-supervisor",
        payload={"message_id": "omsg-1", "target": "feishu", "reason": "timeout"},
    ))

    snapshot = build_supervisor_snapshot(
        state_dir,
        project_root=tmp_path,
        project_id="proj-control",
    )

    assert snapshot["controlled_action_capabilities"]["by_action"]["create-task"]["requires_token"] is True
    assert snapshot["controlled_action_capabilities"]["by_action"]["create-task"]["idempotency_key_required"] is True
    assert snapshot["owner_message_delivery"]["summary"]["failed"] == 1
    assert snapshot["context_recovery"]["summary"]["retry_budget_exhausted"] is True
    assert snapshot["context_recovery"]["by_instance"]["dev-1"]["current_state"] == "compact_failed"
    assert snapshot["skill_provenance"]["summary"]["total"] == 2
    assert snapshot["skill_provenance"]["summary"]["warnings"] == 1
    assert snapshot["pane_probe"]["enabled"] is False


def test_supervisor_replan_signal_does_not_mutate_taskstore(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-OLD", title="old task", status="in_progress"))
    event = ZfEvent(
        type="replan.contract_eval.completed",
        id="evt-revise",
        payload={
            "eval_id": "eval-1",
            "decision": "revise",
            "failed_checks": ["source_coverage_no_invention"],
            "new_task_map_ref": ".zf/artifacts/F/task-map-v2.json",
        },
    )

    items = build_attention_items(
        events=[event],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert [task.id for task in store.list_all()] == ["TASK-OLD"]
    assert len(items) == 1
    assert items[0]["source"] == "replan_eval"
    assert items[0]["suggested_action"]["kind"] == "review_replan_contract_eval"


def test_replan_eval_revise_routes_to_attention() -> None:
    event = ZfEvent(
        type="replan.contract_eval.completed",
        id="evt-revise",
        payload={
            "eval_id": "eval-2",
            "decision": "revise",
            "failed_checks": ["resume_safety"],
        },
    )

    items = build_attention_items(
        events=[event],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert len(items) == 1
    assert items[0]["summary"] == "resume_safety"
    assert items[0]["suggested_route"] == "l2_orchestrator"


def test_replan_eval_attention_dedupes_repeated_decision() -> None:
    events = [
        ZfEvent(
            type="replan.contract_eval.completed",
            id="evt-a",
            payload={"eval_id": "eval-repeat", "decision": "revise"},
        ),
        ZfEvent(
            type="replan.contract_eval.completed",
            id="evt-b",
            payload={"eval_id": "eval-repeat", "decision": "revise"},
        ),
    ]

    items = build_attention_items(
        events=events,
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert len(items) == 1
    assert items[0]["fingerprint"] == "replan_eval:eval-repeat:revise"


def test_parity_scan_request_without_fanout_routes_to_attention() -> None:
    request = ZfEvent(
        type="verify.parity_scan.requested",
        id="evt-parity-scan-1",
        payload={
            "pdd_id": "CANGJIE",
            "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
        },
    )

    items = build_attention_items(
        events=[request],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert len(items) == 1
    assert items[0]["source"] == "workflow_runtime"
    assert items[0]["suggested_route"] == "run_manager_recovery"
    assert items[0]["suggested_action"] == {
        "kind": "request_fanout",
        "stage_id": "flow-module-parity-scan",
        "trigger_event_id": "evt-parity-scan-1",
        "event_type": "verify.parity_scan.requested",
        "pdd_id": "CANGJIE",
        "task_map_ref": ".zf/artifacts/CANGJIE/task_map.json",
    }


def test_parity_scan_request_with_fanout_started_has_no_attention() -> None:
    request = ZfEvent(
        type="verify.parity_scan.requested",
        id="evt-parity-scan-2",
        payload={"pdd_id": "CANGJIE"},
    )
    started = ZfEvent(
        type="fanout.started",
        id="evt-fanout-started",
        payload={
            "stage_id": "cangjie-module-parity-scan",
            "trigger_event_id": "evt-parity-scan-2",
        },
    )

    items = build_attention_items(
        events=[request, started],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert not [
        item for item in items
        if str(item.get("fingerprint") or "").startswith("parity_scan:no_fanout")
    ]


def test_supervisor_quiesces_attention_after_run_completed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="verify.parity_scan.requested",
        id="evt-parity-scan-r4",
        payload={"pdd_id": "CANGJIE-R4"},
    ))
    log.append(ZfEvent(
        type="run.completed",
        actor="run-manager",
        payload={"status": "passed", "release_status": "not_shipped"},
    ))

    snapshot = build_supervisor_snapshot(
        state_dir,
        project_root=tmp_path,
        project_id="proj-r4",
    )

    items = [
        item for item in snapshot["attention_items"]
        if str(item.get("fingerprint") or "").startswith("parity_scan:no_fanout")
    ]
    assert len(items) == 1
    assert items[0]["status"] == "resolved"
    assert items[0]["severity"] == "info"
    assert items[0]["quiesced_by"] == "run.completed"
    assert "suggested_action" not in items[0]
    assert snapshot["attention_summary"]["open"] == 0


def test_supervisor_snapshot_projects_readonly_pane_probe_mismatch(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "role_sessions.yaml").write_text(
        yaml.safe_dump({
            "instance_meta": {
                "dev-1": {
                    "backend": "codex",
                    "last_heartbeat_at": "2026-06-01T00:00:00+00:00",
                    "last_heartbeat_payload": {
                        "state": "busy",
                        "current_task_id": "TASK-PROBE",
                    },
                },
            },
        }),
        encoding="utf-8",
    )
    config = ZfConfig(
        session=SessionConfig(tmux_session="zf-test"),
        roles=[RoleConfig(name="dev", backend="codex", instance_id="dev-1", stuck_threshold_seconds=60)],
    )
    commands: list[list[str]] = []

    def fake_tmux(args: list[str], **kwargs: object):  # type: ignore[no-untyped-def]
        commands.append(args)
        if args[:3] == ["tmux", "display-message", "-p"]:
            return _completed(args, "%9\tcodex\t/tmp/project\t0\n")
        if args[:2] == ["tmux", "capture-pane"]:
            return _completed(args, "Codex is still applying the implementation\n")
        return _completed(args, "", returncode=1, stderr="unexpected")

    from zf.runtime.pane_probe import build_runtime_pane_probe

    probe = build_runtime_pane_probe(
        state_dir,
        config=config,
        project_root=tmp_path,
        now=_dt("2026-06-01T00:05:00+00:00"),
        runner=fake_tmux,
    )

    assert probe["summary"]["mismatch"] == 1
    assert probe["panes"][0]["activity_status"] == "activity_mismatch"
    assert probe["panes"][0]["output_sha256"]
    assert "send-keys" not in " ".join(" ".join(cmd) for cmd in commands)
    assert "kill-pane" not in " ".join(" ".join(cmd) for cmd in commands)


def test_pane_probe_mismatch_is_observe_only_not_autoresearch() -> None:
    from zf.runtime.pane_probe import pane_probe_attention_items

    items = pane_probe_attention_items({
        "enabled": True,
        "panes": [{
            "activity_status": "activity_mismatch",
            "instance_id": "dev-1",
            "current_task_id": "TASK-PROBE",
            "target": "zf:dev-1",
            "pane": "%9",
            "current_command": "codex",
            "output_sha256": "abc",
        }],
    })

    assert len(items) == 1
    assert items[0]["severity"] == "medium"
    assert items[0]["suggested_route"] == "owner_notify"
    assert items[0]["suggested_action"]["kind"] == "observe_runtime_liveness_gap"


def _completed(
    args: list[str],
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
):
    import subprocess

    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def _dt(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
