"""reader stage 失败机械 replan(prod-e2e:prd/issue 两流死端根治)。"""

from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.stage_failure_replan import (
    STAGE_REPLAN_CAP,
    plan_reader_stage_replan,
)


def _config():
    stage = SimpleNamespace(
        id="issue-triage",
        topology="fanout_reader",
        trigger="issue.requested",
        failure_event="",
        aggregate=SimpleNamespace(failure_event="issue.triage.failed"),
    )
    return SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))


def _failure(reason="task_map rejected", findings=None):
    return ZfEvent(type="issue.triage.failed", payload={
        "reason": reason,
        "trigger_event_id": "evt-origin",
        **({"findings": findings} if findings else {}),
    })


def test_replan_re_emits_trigger_with_feedback() -> None:
    origin = ZfEvent(type="issue.requested", payload={"issue_ref": "docs/issues/TODO.md"})
    failure = _failure(findings=[{"severity": "high", "message": "root paths unowned"}])
    replan, note = plan_reader_stage_replan(_config(), [origin, failure], failure)
    assert replan is not None and "issue-triage" in note
    assert replan.type == "issue.requested"
    assert replan.payload["issue_ref"] == "docs/issues/TODO.md"
    assert replan.payload["rework_attempt"] == 1
    assert replan.payload["rework_feedback"][0]["message"] == "root paths unowned"
    assert replan.causation_id == failure.id


def test_replan_preserves_plan_admission_incident_identity() -> None:
    origin = ZfEvent(type="issue.requested", payload={"issue_ref": "docs/issues/TODO.md"})
    failure = _failure(findings=[{"severity": "high", "message": "missing root owner"}])
    failure.payload.update({
        "plan_admission_incident_id": "plan-admission-123",
        "task_map_digest": "abc123",
    })

    replan, _ = plan_reader_stage_replan(_config(), [origin, failure], failure)

    assert replan is not None
    assert replan.payload["plan_admission_incident_id"] == "plan-admission-123"
    assert replan.payload["task_map_digest"] == "abc123"


def test_replan_preserves_failure_target_ref_without_origin_trigger() -> None:
    failure = ZfEvent(type="issue.triage.failed", payload={
        "reason": "bad task_map",
        "target_ref": "docs/issues/fix-list.md",
        "source_refs": {"source_ref": "docs/issues/fix-list.md"},
    })

    replan, note = plan_reader_stage_replan(_config(), [failure], failure)

    assert replan is not None and "issue-triage" in note
    assert replan.payload["target_ref"] == "docs/issues/fix-list.md"
    assert replan.payload["issue_ref"] == "docs/issues/fix-list.md"
    assert replan.payload["source_refs"]["source_ref"] == "docs/issues/fix-list.md"


def test_idempotent_per_failure_event() -> None:
    origin = ZfEvent(type="issue.requested", payload={})
    failure = _failure()
    already = ZfEvent(type="issue.requested", payload={"rework_attempt": 1},
                      causation_id=failure.id)
    replan, note = plan_reader_stage_replan(
        _config(), [origin, failure, already], failure,
    )
    assert replan is None and note == "already_replanned"


def test_cap_exhausted_escalates() -> None:
    origin = ZfEvent(type="issue.requested", payload={})
    priors = [_failure() for _ in range(STAGE_REPLAN_CAP)]
    failure = _failure()
    replan, note = plan_reader_stage_replan(
        _config(), [origin, *priors, failure], failure,
    )
    assert replan is None and note == "cap_exhausted"


def test_unknown_failure_event_ignored() -> None:
    failure = ZfEvent(type="something.else.failed", payload={})
    replan, note = plan_reader_stage_replan(_config(), [failure], failure)
    assert replan is None and note == "no_reader_stage_for_failure"


def test_lane_verify_failure_is_not_generic_reader_stage_replan() -> None:
    """Lane Verify owns its bounded back-edge; reader replan must not race it."""
    stage = SimpleNamespace(
        id="prd-lanes-verify",
        topology="fanout_reader",
        trigger="lane.stage.completed",
        failure_event="",
        aggregate=SimpleNamespace(failure_event="lane.stage.failed"),
        on_fail=SimpleNamespace(
            event="verify.child.failed",
            restart_stage="prd-lanes-impl",
        ),
        assignment=SimpleNamespace(strategy="affinity_stage_slots"),
    )
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))
    failure = ZfEvent(
        type="lane.stage.failed",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "pipeline_id": "prd-lanes",
            "lane_id": "lane0",
            "stage_slot": "verify",
            "reason": "verification rejected",
        },
    )

    replan, note = plan_reader_stage_replan(config, [failure], failure)

    assert replan is None
    assert note == "no_reader_stage_for_failure"


def test_stage_replan_rejects_discriminator_blocked_trigger() -> None:
    from zf.runtime.workflow_resume import WorkflowResumeCheckpoint
    from zf.runtime.workflow_resume_apply import _apply_stage_replan

    failure = _failure()
    checkpoint = WorkflowResumeCheckpoint(
        task_id="TASK-1",
        last_trusted_event_id=failure.id,
        last_completed_stage="issue.triage",
        expected_next_stage="replan:issue-triage",
        expected_next_role="",
        blocking_event_id=failure.id,
        safe_resume_action="needs_stage_replan",
        idempotency_key="stage-replan-rejected",
    )

    class RejectingWriter:
        def __init__(self) -> None:
            self.events: list[ZfEvent] = []

        def append(self, event: ZfEvent) -> ZfEvent:
            if event.type == "issue.requested":
                event = ZfEvent(
                    type="discriminator.failed",
                    payload={
                        "blocked_event_type": "issue.requested",
                        "reason": "schema rejected",
                    },
                )
            self.events.append(event)
            return event

    writer = RejectingWriter()
    result = _apply_stage_replan(
        writer,  # type: ignore[arg-type]
        checkpoint,
        [],
        config=_config(),
        events=[failure],
    )

    assert result.applied is False
    assert "discriminator.failed" in result.reason
    assert not any(event.type == "workflow.resume.applied" for event in writer.events)
    assert any(event.type == "workflow.resume.rejected" for event in writer.events)


def test_layer2_active_reader_stage_failure_replans_in_kernel(tmp_path) -> None:
    from zf.core.config.schema import (
        FanoutAggregateConfig,
        ProjectConfig,
        RoleConfig,
        SessionConfig,
        WorkflowConfig,
        WorkflowStageConfig,
        ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.state.session import SessionStore
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    log = EventLog(state_dir / "events.jsonl")
    origin = ZfEvent(type="prd.scan.completed", actor="zf-cli",
                     payload={"target_ref": "docs/prd/TODO.md"})
    failure = ZfEvent(type="prd.plan.failed", actor="zf-cli",
                      payload={"reason": "bad task_map"})
    log.append(origin)
    log.append(failure)
    cfg = ZfConfig(
        project=ProjectConfig(name="stage-replan"),
        session=SessionConfig(tmux_session="stage-replan"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(name="planner", backend="mock"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="prd-plan",
                trigger="prd.scan.completed",
                topology="fanout_reader",
                aggregate=FanoutAggregateConfig(
                    success_event="task_map.ready",
                    failure_event="prd.plan.failed",
                ),
            ),
        ]),
    )
    orch = Orchestrator(
        state_dir,
        cfg,
        TmuxTransport(TmuxSession(session_name="stage-replan", dry_run=True)),
    )

    orch.run_once(events=[failure])

    replans = [
        event for event in log.read_all()
        if event.type == "prd.scan.completed" and event.causation_id == failure.id
    ]
    assert len(replans) == 1
    assert replans[0].payload["rework_attempt"] == 1


def test_workflow_resume_replays_reader_stage_failure_as_stage_replan(
    tmp_path,
) -> None:
    from zf.core.config.schema import (
        FanoutAggregateConfig,
        ProjectConfig,
        SessionConfig,
        WorkflowConfig,
        WorkflowStageConfig,
        ZfConfig,
    )
    from zf.core.events.log import EventLog
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.workflow_anchor import mark_workflow_fanout_anchor
    from zf.runtime.workflow_resume import (
        apply_workflow_resume,
        build_workflow_resume_projection,
    )

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    anchor = mark_workflow_fanout_anchor(
        Task(id="PRD-WFINT-1", title="PRD flow", status="in_progress"),
        request_id="wfint-1",
        pattern_id="prd-scan",
    )
    store.add(anchor)
    log = EventLog(state_dir / "events.jsonl")
    origin = ZfEvent(type="prd.scan.completed", actor="zf-cli",
                     payload={"target_ref": "docs/prd/TODO.md"})
    failure = ZfEvent(type="prd.plan.failed", actor="zf-cli",
                      payload={"reason": "bad task_map"})
    log.append(origin)
    log.append(failure)
    cfg = ZfConfig(
        project=ProjectConfig(name="stage-replan"),
        session=SessionConfig(tmux_session="stage-replan"),
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="prd-plan",
                trigger="prd.scan.completed",
                topology="fanout_reader",
                aggregate=FanoutAggregateConfig(
                    success_event="task_map.ready",
                    failure_event="prd.plan.failed",
                ),
            ),
        ]),
    )

    projection = build_workflow_resume_projection(state_dir, cfg)
    pending = [
        item for item in projection["checkpoints"]
        if item["safe_resume_action"] != "no_action"
    ]
    assert [item["safe_resume_action"] for item in pending] == [
        "needs_stage_replan",
    ]
    assert pending[0]["task_id"] == "PRD-WFINT-1"

    result = apply_workflow_resume(state_dir, cfg)

    assert result["applied"] == 1
    events = log.read_all()
    assert any(
        event.type == "prd.scan.completed" and event.causation_id == failure.id
        for event in events
    )
    assert any(
        event.type == "workflow.resume.applied"
        and event.payload.get("mode") == "stage_replan_trigger"
        for event in events
    )
    after = build_workflow_resume_projection(state_dir, cfg)
    assert all(
        item["safe_resume_action"] == "no_action"
        for item in after["checkpoints"]
    )


def test_workflow_resume_does_not_broadcast_taskless_events_to_all_tasks() -> None:
    from zf.runtime.workflow_resume import _events_for_task

    explicit = ZfEvent(
        type="lane.stage.completed",
        actor="zf-cli",
        task_id="cli-impl",
        payload={"child_id": "verify-lane-0-cli-impl"},
    )
    taskless_child_name_only = ZfEvent(
        type="lane.stage.completed",
        actor="zf-stall-redispatch",
        payload={"child_id": "verify-lane-0-cli-impl"},
    )
    taskless_exact_payload = ZfEvent(
        type="runtime.attention.needed",
        actor="zf-supervisor",
        payload={"task_id": "cli-tests"},
    )
    taskless_aggregate_payload = ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={"completed_task_ids": ["cli-impl", "cli-tests"]},
    )
    events = [
        explicit,
        taskless_child_name_only,
        taskless_exact_payload,
        taskless_aggregate_payload,
    ]

    assert _events_for_task(events, "cli-impl") == [explicit]
    assert _events_for_task(events, "scaffold") == []
    assert _events_for_task(events, "cli-tests") == [taskless_exact_payload]
