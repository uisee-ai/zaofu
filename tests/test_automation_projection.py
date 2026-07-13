from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.automation_projection import project_automations


def _now(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def _automation_item(projection: dict, automation_id: str) -> dict:
    return next(
        item for item in projection["items"]
        if item["automation_id"] == automation_id
    )


def test_project_automations_collect_runtime_signals(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    project_id = "proj-alpha"
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-ACTIVE", title="active", status="in_progress", assigned_to="dev-1"))
    store.add(Task(id="TASK-BLOCKED", title="blocked", status="blocked"))
    store.add(Task(
        id="TASK-DONE",
        title="done",
        status="in_progress",
        started_at=_now(timedelta(hours=-3)),
    ))
    store.update(
        "TASK-DONE",
        status="done",
        completed_at=_now(timedelta(hours=-1)),
    )
    store.add(Task(id="TASK-CANCELLED", title="cancelled", status="in_progress"))
    store.update("TASK-CANCELLED", status="cancelled", cancelled_at=_now())

    CostTracker(state_dir / "cost.jsonl").record_usage(
        "dev",
        input_tokens=1000,
        output_tokens=250,
        backend="codex",
    )
    (state_dir / "role_sessions.yaml").write_text(
        yaml.safe_dump({
            "instance_meta": {
                "dev-1": {
                    "backend": "codex",
                    "last_heartbeat_at": _now(),
                    "last_heartbeat_payload": {
                        "state": "busy",
                        "current_task_id": "TASK-ACTIVE",
                        "context_used_ratio": 0.82,
                    },
                },
            },
            "roles": {"dev-1": "11111111-1111-1111-1111-111111111111"},
        }),
        encoding="utf-8",
    )

    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task.created", actor="zf-cli", task_id="TASK-ACTIVE"))
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev-1",
        task_id="TASK-ACTIVE",
        payload={"to": "busy"},
    ))
    log.append(ZfEvent(
        type="worker.context.warning",
        actor="dev-1",
        task_id="TASK-ACTIVE",
        payload={"reason": "context usage high", "context_used_ratio": 0.82},
    ))
    log.append(ZfEvent(
        type="review.rejected",
        actor="review",
        task_id="TASK-ACTIVE",
        payload={"reason": "missing evidence"},
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.requested",
        actor="channel",
        payload={
            "channel_id": "ch-main",
            "thread_id": "main",
            "source": "test",
            "request_id": "reply-pending",
            "message_id": "msg-1",
            "target_member_id": "architect",
        },
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.requested",
        actor="channel",
        payload={
            "channel_id": "ch-main",
            "thread_id": "main",
            "source": "test",
            "request_id": "reply-failed",
            "message_id": "msg-2",
            "target_member_id": "qa",
        },
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.failed",
        actor="channel",
        payload={
            "channel_id": "ch-main",
            "thread_id": "main",
            "source": "test",
            "request_id": "reply-failed",
            "target_member_id": "qa",
            "reason": "provider unavailable",
        },
    ))
    log.append(ZfEvent(
        id="evt-workflow-request",
        type="workflow.invoke.requested",
        actor="channel",
        task_id="TASK-ACTIVE",
        payload={
            "task_id": "TASK-ACTIVE",
            "pattern_id": "star",
            "requested_by": "architect",
            "reason": "fanout review",
            "source": "channel",
            "channel_id": "ch-main",
        },
    ))
    log.append(ZfEvent(
        type="workflow.invoke.rejected",
        actor="kernel",
        task_id="TASK-ACTIVE",
        payload={
            "task_id": "TASK-ACTIVE",
            "pattern_id": "star",
            "source_event_id": "evt-workflow-request",
            "reason": "scope missing",
        },
    ))
    log.append(ZfEvent(
        type="automation.proposal.created",
        actor="automation",
        payload={
            "automation_id": "daily-brief",
            "project_id": project_id,
            "source": "test",
            "proposal_id": "proposal-1",
            "output_mode": "proposal",
            "summary": "retry failed channel reply",
            "action": "retry-channel-reply",
        },
    ))
    log.append(ZfEvent(
        type="automation.proposal.created",
        actor="automation",
        payload={
            "automation_id": "daily-brief",
            "project_id": "other-project",
            "source": "test",
            "proposal_id": "proposal-other",
            "output_mode": "proposal",
            "summary": "other project",
        },
    ))
    run_payload = {
        "automation_id": "daily-brief",
        "project_id": project_id,
        "source": "test",
    }
    log.append(ZfEvent(
        type="automation.run.started",
        actor="automation",
        payload={**run_payload, "run_id": "daily-run-1", "trigger": "manual"},
    ))
    log.append(ZfEvent(
        type="automation.run.completed",
        actor="automation",
        payload={
            **run_payload,
            "run_id": "daily-run-1",
            "trigger": "manual",
            "outputs": [{
                "type": "report",
                "summary": "daily ready",
                "refs": {
                    "artifact_refs": [{"kind": "diff", "path": "reports/daily.diff"}],
                    "report_refs": ["reports/daily.md"],
                },
            }],
        },
    ))
    log.append(ZfEvent(
        type="automation.run.failed",
        actor="automation",
        payload={**run_payload, "run_id": "daily-run-2", "reason": "provider timeout"},
    ))
    log.append(ZfEvent(
        type="automation.run.skipped",
        actor="automation",
        payload={**run_payload, "run_id": "daily-run-3", "reason": "daily budget"},
    ))
    log.append(ZfEvent(
        type="automation.run.completed",
        actor="automation",
        payload={
            "automation_id": "daily-brief",
            "project_id": "other-project",
            "source": "test",
            "run_id": "other-run",
        },
    ))
    log.append(ZfEvent(
        type="dispatch.silent_stall",
        actor="orchestrator",
        task_id="TASK-ACTIVE",
        payload={"reason": "assigned without dispatch"},
    ))
    log.append(ZfEvent(
        type="cost.budget.exceeded",
        actor="orchestrator",
        payload={"reason": "budget exceeded"},
    ))

    projection = project_automations(state_dir, project_id=project_id)
    daily_item = _automation_item(projection, "daily-brief")
    daily = daily_item["outputs"][0]
    weekly = _automation_item(projection, "weekly-review")["outputs"][0]
    monitor = _automation_item(projection, "project-monitor")["outputs"][0]
    today = datetime.now(timezone.utc).date().isoformat()
    daily_runs = {
        row["date"]: row
        for row in daily_item["run_counts_by_day"]
    }

    assert daily["window"] == "1d"
    assert daily["task_counts"]["in_progress"] == 1
    assert daily["task_counts"]["blocked"] == 1
    assert daily["task_counts"]["done"] == 1
    assert daily["task_counts"]["cancelled"] == 1
    assert daily["board_counts"] == {
        "Todo": 0,
        "In Progress": 1,
        "Verify": 0,
        "Blocked": 1,
        "Done": 2,
    }
    assert daily["done_tasks"] == ["TASK-DONE"]
    assert daily["cancelled_tasks"] == ["TASK-CANCELLED"]
    assert daily["worker_health"]["context_warnings"][0]["type"] == "worker.context.warning"
    assert daily["channel_attention"]["summary"]["failed_replies"] == 1
    assert daily["channel_attention"]["summary"]["pending_replies"] == 1
    assert daily["pending_proposals"][0]["proposal_id"] == "proposal-1"
    assert {p["proposal_id"] for p in daily["pending_proposals"]} == {"proposal-1"}
    assert daily["token_context_cost"]["dev"]["entries"] == 1
    assert {"TASK-ACTIVE", "TASK-BLOCKED", "TASK-DONE"} <= {
        item["task_id"] for item in daily["refs"]["task_refs"]
    }
    assert {
        item["event_id"] for item in daily["refs"]["event_refs"]
    } >= set(daily["failed_events"])
    assert daily["refs"]["preview_refs"][0]["proposal_id"] == "proposal-1"
    daily_insight_ids = {insight["id"] for insight in daily["insights"]}
    assert daily_insight_ids >= {
        "daily-blocked-tasks",
        "daily-failure-events",
        "daily-worker-attention",
        "daily-channel-attention",
        "daily-pending-proposals",
    }
    assert daily_item["window"] == "1d"
    assert len(daily_item["all_runs"]) == 3
    assert {
        event["type"] for event in daily_item["source_events"]
    } >= {"automation.proposal.created", "automation.run.started"}
    assert daily_runs[today] == {
        "date": today,
        "started": 1,
        "completed": 1,
        "failed": 1,
        "skipped": 1,
        "terminal_total": 3,
        "events_total": 4,
        "success_rate": 0.333333,
    }
    assert daily_item["run_counts_summary"]["events_total"] == 4
    assert daily_item["run_counts_summary"]["terminal_total"] == 3

    assert weekly["window"] == "7d"
    assert weekly["comparison_window"] == "14d"
    assert weekly["task_counts"]["done"] == 1
    assert weekly["board_counts"]["In Progress"] == 1
    assert weekly["board_counts"]["Blocked"] == 1
    assert weekly["board_counts"]["Done"] == 2
    assert weekly["delivery_metrics"]["done"] == 1
    assert weekly["delivery_metrics"]["cancelled"] == 1
    assert weekly["delivery_metrics"]["terminal_success_rate"] == 0.5
    assert weekly["delivery_metrics_14d"]["done"] == 1
    assert weekly["failure_taxonomy"]["counts"]["business.gate"] == 1
    assert weekly["failure_taxonomy_14d"]["counts"]["business.gate"] == 1
    assert weekly["backlog_drift"]["created_events"] == 1
    assert weekly["cost_trend"]["entries"] == 1
    assert weekly["proposal_outcomes"]["counts"]["created"] == 1
    assert weekly["refs"]["task_refs"][0]["task_id"] == "TASK-DONE"
    assert weekly["refs"]["event_refs"]
    weekly_insight_ids = {insight["id"] for insight in weekly["insights"]}
    assert weekly_insight_ids >= {
        "weekly-throughput",
        "weekly-low-success-rate",
        "weekly-rework",
        "weekly-top-failure",
    }

    assert monitor["type"] == "alert"
    assert monitor["summary"].startswith("6 monitor alerts")
    assert {alert["type"] for alert in monitor["alerts"]} >= {
        "worker.context.warning",
        "dispatch.silent_stall",
        "cost.budget.exceeded",
    }
    assert monitor["channel_alerts"]
    assert monitor["open_proposals"][0]["proposal_id"] == "proposal-1"
    assert monitor["refs"]["event_refs"]
    assert monitor["refs"]["preview_refs"][0]["proposal_id"] == "proposal-1"
    assert monitor["board_counts"]["In Progress"] == 1
    assert monitor["board_counts"]["Blocked"] == 1
    monitor_insight_ids = {insight["id"] for insight in monitor["insights"]}
    assert monitor_insight_ids >= {
        "monitor-runtime-alerts",
        "monitor-channel-alerts",
        "monitor-worker-attention",
        "monitor-open-proposals",
    }
    assert daily_item["all_runs"][0]["outputs"][0]["refs"]["artifact_refs"][0]["path"] == "reports/daily.diff"
    assert daily_item["all_runs"][0]["outputs"][0]["refs"]["report_refs"] == ["reports/daily.md"]


def test_weekly_success_rate_is_na_without_terminal_sample(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-ACTIVE", title="active", status="in_progress"),
    )

    projection = project_automations(state_dir, project_id="proj-alpha")
    weekly = _automation_item(projection, "weekly-review")["outputs"][0]

    assert weekly["delivery_metrics"]["terminal_success_rate"] is None
    assert {
        insight["id"] for insight in weekly["insights"]
    } == {"weekly-throughput"}


def test_automation_run_controlled_action_emits_runtime_events(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={
            "action": "automation-run",
            "requested_action": "automation-run",
            "request": {
                "automation_id": "daily-brief",
                "project_id": "proj-alpha",
            },
        },
    )

    response = ControlledActionService(
        state_dir,
        writer,
        actor="web",
        source="automation",
        surface="web",
    ).execute(
        action="automation-run",
        requested_action="automation-run",
        requested=requested,
        payload={
            "automation_id": "daily-brief",
            "project_id": "proj-alpha",
            "source": "test",
            "trigger": "manual",
        },
    )

    assert response["ok"] is True
    assert response["status"] == "completed"
    assert response["automation_id"] == "daily-brief"
    events = EventLog(state_dir / "events.jsonl").read_all()
    types = [event.type for event in events]
    assert "automation.run.started" in types
    assert "automation.run.completed" in types
    assert "runtime.action.completed" in types
    assert "web.action.completed" in types

    projection = project_automations(state_dir, project_id="proj-alpha")
    daily_item = _automation_item(projection, "daily-brief")
    assert daily_item["status"] == "completed"
    assert daily_item["last_run"]["run_id"] == response["run_id"]
    assert daily_item["run_counts_summary"]["started"] == 1
    assert daily_item["run_counts_summary"]["completed"] == 1


def test_event_ref_carries_content_based_problem_fingerprint() -> None:
    """ZF-E2E-MINI-P3: alert refs carry a registry-dedupe-key fingerprint so
    the attention layer folds repeats of one problem (per-event ids never
    fold)."""
    from zf.core.events.model import ZfEvent
    from zf.runtime.automation_projection import _event_ref

    a = _event_ref(ZfEvent(
        type="cost.budget.exceeded",
        id="evt-a",
        actor="zf-cli",
        payload={"scope": "global", "role": None, "budget_usd": 6.0,
                 "current_usd": 6.7},
    ))
    b = _event_ref(ZfEvent(
        type="cost.budget.exceeded",
        id="evt-b",
        actor="zf-cli",
        payload={"scope": "global", "role": None, "budget_usd": 6.0,
                 "current_usd": 9.9},
    ))
    assert a["problem_fingerprint"] == b["problem_fingerprint"]
    assert "evt-" not in a["problem_fingerprint"]
    assert a["problem_fingerprint"].startswith("cost.budget.exceeded:")

    unregistered = _event_ref(ZfEvent(
        type="totally.unregistered.event",
        id="evt-c",
        task_id="TASK-9",
        payload={},
    ))
    assert unregistered["problem_fingerprint"] == (
        "totally.unregistered.event:TASK-9"
    )
