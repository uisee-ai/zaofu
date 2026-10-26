"""恢复手术动词:前置条件即护栏(agent 裁决错也不伤真相)。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.workflow_anchor import mark_workflow_managed_task


def _service(tmp_path: Path) -> ControlledActionService:
    state = tmp_path / ".zf"
    state.mkdir(parents=True, exist_ok=True)
    (state / "kanban.json").write_text(json.dumps([
        {"id": "T1", "title": "t", "status": "in_progress",
         "assigned_to": "dev-lane-0"},
        {"id": "T2", "title": "t2", "status": "done", "assigned_to": ""},
    ]))
    log = EventLog(state / "events.jsonl")
    return ControlledActionService(state, EventWriter(log), actor="zf-cli")


def _req(payload: dict) -> ZfEvent:
    return ZfEvent(type="controlled.action.requested", actor="web", payload=payload)


def test_task_requeue_requires_dead_carrier(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    # 活 child 在飞 → 拒绝
    log.append(ZfEvent(type="fanout.child.dispatched", actor="zf-cli",
                       payload={"task_id": "T1", "fanout_id": "f1", "child_id": "c1"}))
    r = svc._task_requeue_action(
        requested=_req({}), action="task-requeue",
        requested_action="task-requeue", payload={"task_id": "T1"},
    )
    assert r["ok"] is False and "live fanout child" in r["reason"]
    # child 死 → 放行
    log.append(ZfEvent(type="fanout.child.failed", actor="zf-cli",
                       payload={"task_id": "T1", "fanout_id": "f1", "child_id": "c1"}))
    r2 = svc._task_requeue_action(
        requested=_req({}), action="task-requeue",
        requested_action="task-requeue", payload={"task_id": "T1"},
    )
    assert r2["ok"] is True
    assert any(e.type == "task.requeued" for e in log.read_all())


def test_child_rebuild_preconditions(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    # 无历史 child → 拒
    r = svc._child_rebuild_action(
        requested=_req({}), action="child-rebuild",
        requested_action="child-rebuild", payload={"task_id": "T1"},
    )
    assert r["ok"] is False
    # done 任务 → 拒
    r2 = svc._child_rebuild_action(
        requested=_req({}), action="child-rebuild",
        requested_action="child-rebuild", payload={"task_id": "T2"},
    )
    assert r2["ok"] is False
    # 死 child → 放行,交给现有 repair action executor 重建承接
    log.append(ZfEvent(type="fanout.child.failed", actor="zf-cli",
                       payload={"task_id": "T1", "fanout_id": "f1", "child_id": "c9"}))
    r3 = svc._child_rebuild_action(
        requested=_req({}), action="child-rebuild",
        requested_action="child-rebuild", payload={"task_id": "T1"},
    )
    assert r3["ok"] is True
    requests = [e for e in log.read_all() if e.type == "repair.action.requested"]
    assert requests
    assert requests[-1].payload["kind"] == "rerun_fanout_child"
    assert requests[-1].payload["fanout_id"] == "f1"
    assert requests[-1].payload["fanout_child_id"] == "c9"


def test_stage_retrigger_idempotent_and_generational(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    src = svc.writer.append(ZfEvent(type="task_map.ready", actor="zf-cli",
                             payload={"task_map_ref": "x"}))
    r = svc._stage_retrigger_action(
        requested=_req({}), action="stage-retrigger",
        requested_action="stage-retrigger",
        payload={"source_event_id": src.id},
    )
    assert r["ok"] is True
    re = [e for e in log.read_all()
          if e.type == "task_map.ready" and (e.payload or {}).get("redrive_of") == src.id]
    assert len(re) == 1 and re[0].payload["rework_of"] == src.id
    # 第二次同源 → 幂等拒绝
    r2 = svc._stage_retrigger_action(
        requested=_req({}), action="stage-retrigger",
        requested_action="stage-retrigger",
        payload={"source_event_id": src.id},
    )
    assert r2["ok"] is False and "already retriggered" in r2["reason"]
    # 非推进事件 → 拒
    other = svc.writer.append(ZfEvent(type="worker.heartbeat", actor="w", payload={}))
    r3 = svc._stage_retrigger_action(
        requested=_req({}), action="stage-retrigger",
        requested_action="stage-retrigger",
        payload={"source_event_id": other.id},
    )
    assert r3["ok"] is False
    candidate = svc.writer.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={"candidate_ref": "candidate/PDD-1"},
    ))
    r4 = svc._stage_retrigger_action(
        requested=_req({}),
        action="stage-retrigger",
        requested_action="stage-retrigger",
        payload={"source_event_id": candidate.id},
    )
    assert r4["ok"] is True
    redriven = [
        event for event in log.read_all()
        if event.type == "candidate.ready"
        and event.payload.get("redrive_of") == candidate.id
    ]
    assert len(redriven) == 1


def test_stage_replan_new_generation_reopens_run_and_parent_once(
    tmp_path: Path,
) -> None:
    svc = _service(tmp_path)
    stage = SimpleNamespace(
        id="issue-triage",
        attempt_domain="plan",
        topology="fanout_reader",
        trigger="issue.requested",
        failure_event="",
        aggregate=SimpleNamespace(failure_event="issue.triage.failed"),
    )
    svc.config = SimpleNamespace(
        workflow=SimpleNamespace(stages=[stage]),
    )
    store = TaskStore(svc.state_dir / "kanban.json")
    store.update(
        "T1",
        status="blocked",
        contract=mark_workflow_managed_task(
            Task(id="T1", title="t")
        ).contract,
        blocked_reason="stage replan cap exhausted",
    )
    log = svc.writer.event_log
    log.append(ZfEvent(
        id="evt-run-start",
        type="run.goal.started",
        task_id="T1",
        correlation_id="RUN-1",
        payload={"run_id": "RUN-1", "objective": "deliver issue"},
    ))
    log.append(ZfEvent(
        id="evt-origin",
        type="issue.requested",
        task_id="T1",
        correlation_id="RUN-1",
        payload={
            "workflow_run_id": "RUN-1",
            "issue_ref": "docs/issues/TODO.md",
        },
    ))
    failure = ZfEvent(
        id="evt-stage-failure",
        type="issue.triage.failed",
        task_id="T1",
        correlation_id="RUN-1",
        payload={
            "workflow_run_id": "RUN-1",
            "reason": "task map admission rejected",
        },
    )
    log.append(failure)
    escalation = ZfEvent(
        id="evt-stage-escalation",
        type="human.escalate",
        task_id="T1",
        correlation_id="RUN-1",
        causation_id=failure.id,
        payload={
            "failure_class": "stage_replan_cap_exhausted",
            "source_event_id": failure.id,
            "allowed_actions": ["operator_review", "start_new_generation"],
        },
    )
    log.append(escalation)

    result = svc._execute_action(
        requested=_req({}),
        action="stage-replan-new-generation",
        requested_action="stage-replan-new-generation",
        payload={
            "escalation_event_id": escalation.id,
            "approval_ref": "operator:test",
        },
    )
    replay = svc._execute_action(
        requested=_req({}),
        action="stage-replan-new-generation",
        requested_action="stage-replan-new-generation",
        payload={
            "escalation_event_id": escalation.id,
            "approval_ref": "operator:test",
        },
    )

    assert result["ok"] is True
    assert result["replayed"] is False
    assert replay["ok"] is True
    assert replay["replayed"] is True
    generation = result["stage_replan_generation"]
    events = log.read_all()
    goal_update = next(
        event for event in events
        if event.type == "run.goal.updated"
        and event.payload.get("stage_replan_generation") == generation
    )
    triggers = [
        event for event in events
        if event.type == "issue.requested"
        and event.payload.get("stage_replan_generation") == generation
    ]
    assert len(triggers) == 1
    assert triggers[0].payload["rework_attempt"] == 1
    assert triggers[0].payload["operator_authorized"] is True
    assert events.index(goal_update) < events.index(triggers[0])
    task = store.get("T1")
    assert task is not None
    assert task.status == "in_progress"
    assert task.blocked_reason == ""


def test_fanout_aggregate_rebuild_requires_terminal_manifest_and_is_idempotent(
    tmp_path: Path,
) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    fanout_id = "fanout-rebuild-1"
    manifest_path = svc.state_dir / "fanouts" / fanout_id / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({
        "fanout_id": fanout_id,
        "children": [{"child_id": "child-1", "status": "completed"}],
    }), encoding="utf-8")
    source = svc.writer.append(ZfEvent(
        type="flow.discovery.completed",
        actor="zf-cli",
        correlation_id="run-1",
        payload={"fanout_id": fanout_id, "goal_id": "wrong"},
    ))
    invalid = svc.writer.append(ZfEvent(
        type="goal.closure.identity.invalid",
        actor="zf-cli",
        correlation_id="run-1",
        payload={"source_event_id": source.id, "goal_id": "goal-1"},
    ))

    result = svc._fanout_aggregate_rebuild_action(
        requested=_req({}),
        action="fanout-aggregate-rebuild",
        requested_action="fanout-aggregate-rebuild",
        payload={"source_event_id": invalid.id},
    )
    duplicate = svc._fanout_aggregate_rebuild_action(
        requested=_req({}),
        action="fanout-aggregate-rebuild",
        requested_action="fanout-aggregate-rebuild",
        payload={"source_event_id": invalid.id},
    )

    assert result["ok"] is True
    requests = [
        event for event in log.read_all()
        if event.type == "fanout.aggregate.rebuild.requested"
    ]
    assert len(requests) == 1
    assert requests[0].payload["source_event_id"] == source.id
    assert requests[0].payload["identity_invalid_event_id"] == invalid.id
    assert duplicate["ok"] is False


def test_fanout_aggregate_rebuild_accepts_blocked_goal_closure_schema_event(
    tmp_path: Path,
) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    fanout_id = "fanout-goal-closure"
    manifest_path = svc.state_dir / "fanouts" / fanout_id / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({
        "fanout_id": fanout_id,
        "aggregate_config": {
            "success_event": "goal.closure.synthesized",
        },
        "children": [{"child_id": "judge", "status": "completed"}],
    }), encoding="utf-8")
    blocked = svc.writer.append(ZfEvent(
        type="discriminator.failed",
        actor="zf-cli",
        correlation_id="run-goal",
        payload={
            "fanout_id": fanout_id,
            "blocked_event_id": "evt-blocked-goal-closure",
            "blocked_event_type": "goal.closure.synthesized",
            "blocked_event_payload": {"fanout_id": fanout_id},
            "failed_d": ["EventSchemaD"],
        },
    ))

    result = svc._fanout_aggregate_rebuild_action(
        requested=_req({}),
        action="fanout-aggregate-rebuild",
        requested_action="fanout-aggregate-rebuild",
        payload={"source_event_id": blocked.id},
    )

    assert result["ok"] is True
    request = next(
        event for event in log.read_all()
        if event.type == "fanout.aggregate.rebuild.requested"
    )
    assert request.payload["source_event_id"] == blocked.id
    assert request.payload["schema_failure_event_id"] == blocked.id
    assert request.payload["rebuild_scope"] == "blocked_success_aggregate"
    assert request.payload["expected_success_event"] == (
        "goal.closure.synthesized"
    )


def test_goal_lineage_rebuild_targets_upstream_reader_aggregate(
    tmp_path: Path,
) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    verify_fanout_id = "fanout-verify-lineage"
    verify_manifest = (
        svc.state_dir / "fanouts" / verify_fanout_id / "manifest.json"
    )
    verify_manifest.parent.mkdir(parents=True)
    verify_manifest.write_text(json.dumps({
        "fanout_id": verify_fanout_id,
        "aggregate_config": {"success_event": "test.passed"},
        "children": [
            {"child_id": "verify-1", "status": "completed"},
            {"child_id": "verify-2", "status": "completed"},
        ],
    }), encoding="utf-8")
    verified = svc.writer.append(ZfEvent(
        type="test.passed",
        actor="zf-cli",
        correlation_id="run-lineage",
        payload={"fanout_id": verify_fanout_id},
    ))

    discovery_fanout_id = "fanout-discovery-lineage"
    discovery_manifest = (
        svc.state_dir / "fanouts" / discovery_fanout_id / "manifest.json"
    )
    discovery_manifest.parent.mkdir(parents=True)
    discovery_manifest.write_text(json.dumps({
        "fanout_id": discovery_fanout_id,
        "trigger_payload": {"source_event_id": verified.id},
        "aggregate_config": {"success_event": "flow.discovery.completed"},
        "children": [{"child_id": "discovery-1", "status": "completed"}],
    }), encoding="utf-8")
    discovered = svc.writer.append(ZfEvent(
        type="flow.discovery.completed",
        actor="zf-cli",
        correlation_id="run-lineage",
        payload={"fanout_id": discovery_fanout_id},
    ))
    invalid = svc.writer.append(ZfEvent(
        type="goal.closure.identity.invalid",
        actor="zf-cli",
        correlation_id="run-lineage",
        payload={
            "source_event_id": discovered.id,
            "reason": "current task-map generation has no pinned goal claim set",
        },
    ))

    result = svc._fanout_aggregate_rebuild_action(
        requested=_req({}),
        action="fanout-aggregate-rebuild",
        requested_action="fanout-aggregate-rebuild",
        payload={"source_event_id": invalid.id},
    )

    assert result["ok"] is True
    assert result["fanout_id"] == verify_fanout_id
    assert result["source_event_id"] == verified.id
    assert result["rebuild_scope"] == "upstream_lineage_aggregate"
    rebuild = next(
        event for event in log.read_all()
        if event.type == "fanout.aggregate.rebuild.requested"
    )
    assert rebuild.payload["fanout_id"] == verify_fanout_id
    assert rebuild.payload["source_event_id"] == verified.id
    assert rebuild.payload["identity_invalid_event_id"] == invalid.id
    assert rebuild.payload["expected_success_event"] == "test.passed"


def test_fanout_aggregate_rebuild_fails_closed_for_invalid_source_state(
    tmp_path: Path,
) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log

    missing = svc.writer.append(ZfEvent(
        type="flow.discovery.completed",
        payload={"fanout_id": "fanout-missing"},
    ))
    missing_result = svc._fanout_aggregate_rebuild_action(
        requested=_req({}),
        action="fanout-aggregate-rebuild",
        requested_action="fanout-aggregate-rebuild",
        payload={"source_event_id": missing.id},
    )
    assert missing_result["ok"] is False
    assert "manifest" in missing_result["reason"]

    pending_id = "fanout-pending"
    pending_path = svc.state_dir / "fanouts" / pending_id / "manifest.json"
    pending_path.parent.mkdir(parents=True)
    pending_path.write_text(json.dumps({
        "fanout_id": pending_id,
        "children": [{"child_id": "child-1", "status": "dispatched"}],
    }), encoding="utf-8")
    pending = svc.writer.append(ZfEvent(
        type="flow.discovery.completed",
        payload={"fanout_id": pending_id},
    ))
    pending_result = svc._fanout_aggregate_rebuild_action(
        requested=_req({}),
        action="fanout-aggregate-rebuild",
        requested_action="fanout-aggregate-rebuild",
        payload={"source_event_id": pending.id},
    )
    assert pending_result["ok"] is False
    assert pending_result["reason"] == "fanout children are not terminal"

    stale_id = "fanout-stale"
    replacement_id = "fanout-current"
    stale_path = svc.state_dir / "fanouts" / stale_id / "manifest.json"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_text(json.dumps({
        "fanout_id": stale_id,
        "children": [{"child_id": "child-1", "status": "completed"}],
    }), encoding="utf-8")
    for fanout_id in (stale_id, replacement_id):
        log.append(ZfEvent(
            type="fanout.started",
            payload={
                "fanout_id": fanout_id,
                "stage_id": "goal-discovery",
                "target_ref": "goal/G-1",
                "pdd_id": "G-1",
                "feature_id": "G-1",
            },
        ))
    stale = svc.writer.append(ZfEvent(
        type="flow.discovery.completed",
        payload={"fanout_id": stale_id},
    ))
    stale_result = svc._fanout_aggregate_rebuild_action(
        requested=_req({}),
        action="fanout-aggregate-rebuild",
        requested_action="fanout-aggregate-rebuild",
        payload={"source_event_id": stale.id},
    )
    assert stale_result["ok"] is False
    assert "stale" in stale_result["reason"]
    assert not [
        event for event in log.read_all()
        if event.type == "fanout.aggregate.rebuild.requested"
    ]


def test_rescan_grant_requires_exhaustion(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    log = svc.writer.event_log
    r = svc._rescan_grant_action(
        requested=_req({}), action="rescan-grant",
        requested_action="rescan-grant", payload={},
    )
    assert r["ok"] is False  # 无弹尽记录
    log.append(ZfEvent(type="human.escalate", actor="zf-cli",
                       payload={"reason": "goal idle rescans exhausted"}))
    r2 = svc._rescan_grant_action(
        requested=_req({}), action="rescan-grant",
        requested_action="rescan-grant", payload={},
    )
    assert r2["ok"] is True
    assert any(e.type == "run.goal.rescan.granted" for e in log.read_all())
    # 冷却内再来 → 拒
    r3 = svc._rescan_grant_action(
        requested=_req({}), action="rescan-grant",
        requested_action="rescan-grant", payload={},
    )
    assert r3["ok"] is False


def test_stale_candidate_rework_action_rejects_goal_compat_projection(tmp_path):
    svc = _service(tmp_path)
    source = svc.writer.append(ZfEvent(type="judge.failed", payload={
        "pdd_id": "P-1", "authority": "compat_projection", "verdict": "blocked",
    }))
    requested = svc.writer.emit("control.action.requested", actor="operator", payload={})
    result = svc.execute(
        action="candidate-rework-apply", requested_action="candidate-rework-apply",
        requested=requested, payload={"pdd_id": "P-1", "source_event_id": source.id,
                                     "candidate_rework_action": "retrigger"},
    )
    assert result["ok"] is False
    assert "compatibility projection" in result["reason"]
    assert not any(e.type == "task_map.ready" for e in svc.writer.event_log.read_all())
