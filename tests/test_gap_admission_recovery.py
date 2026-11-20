"""A rejected gap proposal must not settle the reader's bounded recovery."""
from pathlib import Path

import pytest

from zf.core.config.schema import (
    FanoutAggregateConfig, ProjectConfig, SessionConfig, WorkflowConfig,
    WorkflowStageConfig, ZfConfig,
)
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick
from zf.runtime.stage_failure_replan import plan_reader_stage_replan
from zf.runtime.workflow_anchor import mark_workflow_fanout_anchor


def config():
    return ZfConfig(
        project=ProjectConfig(name="gap-recovery"),
        session=SessionConfig(tmux_session="gap-recovery"),
        workflow=WorkflowConfig(stages=[WorkflowStageConfig(
            id="prd-post-verify-discovery", trigger="flow.discovery.requested",
            topology="fanout_reader", aggregate=FanoutAggregateConfig(
                success_event="flow.discovery.completed", failure_event="flow.discovery.failed",
            ),
        )]),
    )


def history(*, attempt=0):
    identity = {"workflow_run_id": "run-1", "pdd_id": "PRD-1"}
    origin = ZfEvent(id="origin", type="flow.discovery.requested", correlation_id="run-1", payload={
        **identity, "target_ref": "accepted-commit", "task_map_generation": "gen-1",
        "plan_artifact_package_digest": "package-1", "rework_attempt": attempt,
    })
    failure = ZfEvent(id="failed", type="flow.discovery.failed", correlation_id="reader-trace", payload={
        **identity, "reason": "upstream artifact contract missing", "task_map_generation": "gen-1",
    })
    gap = ZfEvent(id="gap", type="flow.gap_plan.ready", causation_id=failure.id,
        correlation_id="run-1", payload={
            **identity, "source_event_id": failure.id, "task_map_generation": "gen-1",
            "plan_artifact_package_digest": "package-1", "gap_tasks": [{"task_id": "NEW-GAP"}],
        })
    rejected = ZfEvent(id="rejected", type="task_map.amend.failed", causation_id="amend-request",
        correlation_id="run-1", payload={
            "source_event_id": gap.id, "reason": "rolling_smoke_command_missing",
        })
    return [origin, failure, gap, rejected], failure


def test_rejected_gap_proposal_retries_with_original_gap_and_admission_feedback():
    events, failure = history()
    retry, note = plan_reader_stage_replan(config(), events, failure)
    assert retry is not None, note
    assert retry.type == "flow.discovery.requested"
    assert retry.payload["target_ref"] == "accepted-commit"
    assert retry.payload["rework_attempt"] == 1
    messages = [x["message"] for x in retry.payload["rework_feedback"]]
    assert "upstream artifact contract missing" in messages
    assert any("rolling_smoke_command_missing" in message for message in messages)
    assert retry.payload["gap_admission_repair"]["gap_event_id"] == "gap"
    assert retry.payload["gap_admission_repair"]["rejection_event_id"] == "rejected"
    assert retry.payload["gap_admission_repair"]["gap_event_ref"] == "event:gap"
    again, reason = plan_reader_stage_replan(config(), [*events, retry], failure)
    assert again is None and reason == "already_replanned"


@pytest.mark.parametrize("change", ["pending", "accepted", "unrelated", "cross_run"])
def test_pending_accepted_or_unrelated_rejection_keeps_gap_ownership(change):
    events, failure = history()
    if change == "pending":
        events.pop()
    elif change == "accepted":
        events.append(ZfEvent(type="task_map.amended", correlation_id="run-1", payload={"source_event_id": "gap"}))
    elif change == "unrelated":
        events[-1].payload["source_event_id"] = "other-gap"
    else:
        events[-1].payload["workflow_run_id"] = "other-run"
    retry, reason = plan_reader_stage_replan(config(), events, failure)
    assert retry is None and reason == "handled_by_gap_plan"


def test_gap_rejection_preserves_existing_retry_cap():
    events, failure = history(attempt=2)
    retry, reason = plan_reader_stage_replan(config(), events, failure)
    assert retry is None and reason == "cap_exhausted"


@pytest.mark.parametrize("takeover", ["operator", "admitted_plan"])
def test_causal_takeover_prevents_old_gap_rejection_revival(takeover):
    events, failure = history()
    if takeover == "operator":
        events.append(ZfEvent(type="orchestrator.replan_requested", correlation_id="run-1", payload={
            "workflow_run_id": "run-1", "pdd_id": "PRD-1", "rework_of": "another-rejection",
            "rework_summary": {"source_event_ids": ["rejected"]},
        }))
    else:
        events.append(ZfEvent(type="plan.artifact_package.admitted", correlation_id="run-1", payload={
            "workflow_run_id": "run-1", "supersedes_package_digest": "package-1",
            "package_digest": "package-2", "task_map_generation": "gen-2",
            "source_event_id": "rejected",
        }))
    retry, reason = plan_reader_stage_replan(config(), events, failure)
    assert retry is None and reason == "handled_by_gap_plan"


def test_unrelated_operator_replan_does_not_claim_this_gap():
    events, failure = history()
    events.append(ZfEvent(type="orchestrator.replan_requested", correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "pdd_id": "PRD-1", "rework_of": "another-rejection",
    }))
    retry, note = plan_reader_stage_replan(config(), events, failure)
    assert retry is not None, note


def test_run_manager_applies_bounded_gap_admission_repair(tmp_path: Path):
    state = tmp_path / ".state"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    TaskStore(state / "kanban.json").add(mark_workflow_fanout_anchor(
        Task(id="PRD-1", title="PRD", status="in_progress"),
        request_id="run-1", pattern_id="prd-scan",
    ))
    log = EventLog(state / "events.jsonl")
    events, failure = history()
    for event in events:
        log.append(event)
    projection = build_run_manager_projection(state, events=log.read_all(), config=config())
    pending = [x for x in projection["pending_actions"] if x.get("safe_resume_action") == "needs_stage_replan"]
    assert len(pending) == 1
    assert pending[0]["owner_route"] == "controlled_action"
    outcome = run_manager_tick(state_dir=state, writer=EventWriter(log), event_log=log,
        config=config(), action_filter={"workflow-task-resume"}, spawn_repairs=False)
    assert outcome.actions_applied == 1
    retries = [e for e in log.read_all() if e.type == "flow.discovery.requested" and e.causation_id == failure.id]
    assert len(retries) == 1
    assert retries[0].payload["gap_admission_repair"]["rejection_event_id"] == "rejected"


def test_child_trace_does_not_hide_causally_bound_gap_admission_failure():
    events, failure = history()
    events[-1].correlation_id = "semantic-replan-child"
    events[-1].payload["trace_id"] = "semantic-replan-child"
    retry, note = plan_reader_stage_replan(config(), events, failure)
    assert retry is not None, note
    assert retry.payload["gap_admission_repair"]["rejection_event_id"] == "rejected"


def test_unrelated_successor_package_does_not_settle_rejected_gap():
    events, failure = history()
    events.append(ZfEvent(type="plan.artifact_package.admitted", correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "supersedes_package_digest": "package-1",
        "package_digest": "package-2", "task_map_generation": "gen-2",
        "source_event_id": "another-task-replan",
    }))
    retry, note = plan_reader_stage_replan(config(), events, failure)
    assert retry is not None, note


@pytest.mark.parametrize("attempt", [0, 2])
def test_registered_reactor_repairs_or_escalates_rejected_gap_immediately(tmp_path: Path, attempt):
    from zf.runtime.orchestrator import Orchestrator
    state = tmp_path / ".state"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    log = EventLog(state / "events.jsonl")
    events, failure = history(attempt=attempt)
    for event in events:
        log.append(event)
    runtime = Orchestrator(state, config(), object())
    handler = runtime.event_registry.primary("task_map.amend.failed")
    assert handler is not None
    decision = handler(events[-1])
    assert decision is not None
    assert decision.action == ("dispatch" if attempt == 0 else "escalate")
    rows = log.read_all()
    if attempt == 0:
        retries = [e for e in rows if e.type == "flow.discovery.requested" and e.causation_id == failure.id]
        assert len(retries) == 1
        assert retries[0].payload["gap_admission_repair"]["rejection_event_id"] == "rejected"
    else:
        escalations = [e for e in rows if e.type == "human.escalate"]
        assert len(escalations) == 1
        assert "stage replan cap exhausted" in escalations[0].payload["reason"]
        assert any(e.type == "run.goal.blocked" for e in rows)
    before = len(rows)
    handler(events[-1])
    assert len(log.read_all()) == before


@pytest.mark.parametrize("corruption", ["unknown_source", "cross_run", "not_gap"])
def test_reactor_does_not_route_unbound_amend_rejections(tmp_path: Path, corruption):
    from zf.runtime.orchestrator import Orchestrator
    state = tmp_path / ".state"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    events, _ = history()
    if corruption == "unknown_source":
        events[-1].payload["source_event_id"] = "unknown"
    elif corruption == "cross_run":
        events[-1].payload["workflow_run_id"] = "other-run"
    else:
        events[-2].type = "agent.text"
    log = EventLog(state / "events.jsonl")
    for event in events:
        log.append(event)
    runtime = Orchestrator(state, config(), object())
    handler = runtime.event_registry.primary("task_map.amend.failed")
    assert handler is not None
    before = len(log.read_all())
    assert handler(events[-1]) is None
    assert len(log.read_all()) == before
