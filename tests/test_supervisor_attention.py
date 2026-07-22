from __future__ import annotations

from datetime import datetime, timezone

from zf.core.events.model import ZfEvent
from zf.runtime.supervisor_attention import (
    apply_attention_lifecycle,
    build_attention_items,
)


def test_abnormal_event_registry_routes_runtime_events_to_attention() -> None:
    items = build_attention_items(
        events=[
            ZfEvent(
                type="worker.stuck",
                id="evt-worker-stuck",
                actor="dev-lane-0",
                task_id="TASK-1",
                payload={"role_instance": "dev-lane-0", "reason": "heartbeat stale"},
            ),
            ZfEvent(
                type="dispatch.silent_stall",
                id="evt-dispatch-stall",
                task_id="TASK-2",
                payload={"fanout_id": "fanout-impl", "reason": "no matching task"},
            ),
            ZfEvent(
                type="owner.visible_message.failed",
                id="evt-owner-failed",
                payload={"message_id": "omsg-1", "reason": "Feishu delivery failed"},
            ),
        ],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    by_source = {str(item["source"]): item for item in items}
    assert by_source["runtime_worker"]["suggested_route"] == "run_manager_recovery"
    assert (
        by_source["runtime_worker"]["problem_envelope"]["problem_class"]
        == "worker_lifecycle"
    )
    assert by_source["workflow_runtime"]["failure_class"] == "dispatch_silent_stall"
    assert (
        by_source["workflow_runtime"]["problem_envelope"]["problem_class"]
        == "workflow_progress"
    )
    assert by_source["owner_delivery"]["problem_envelope"]["owner_route"] == "run_manager"
    assert by_source["owner_delivery"]["problem_envelope"]["problem_class"] == "external_gate"


def test_single_expected_negative_event_does_not_create_attention() -> None:
    items = build_attention_items(
        events=[
            ZfEvent(
                type="verify.failed",
                id="evt-verify-failed",
                task_id="TASK-VERIFY",
                payload={"reason": "missing real provider evidence"},
            ),
        ],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert items == []


def test_pane_observation_and_one_runner_failure_do_not_open_source_diagnosis() -> None:
    items = build_attention_items(
        events=[
            ZfEvent(
                type="worker.pane.dead_observed",
                id="evt-pane-dead",
                actor="dev-lane-0",
                task_id="TASK-1",
            ),
            ZfEvent(
                type="worker.runner.failed",
                id="evt-runner-failed",
                actor="dev-lane-0",
                task_id="TASK-1",
            ),
        ],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert items == []


def test_repeated_runner_failure_still_opens_one_lifecycle_attention() -> None:
    items = build_attention_items(
        events=[
            ZfEvent(
                type="worker.runner.failed",
                id="evt-runner-failed-1",
                actor="dev-lane-0",
                task_id="TASK-1",
                payload={"reason": "respawn pending"},
            ),
            ZfEvent(
                type="worker.runner.failed",
                id="evt-runner-failed-2",
                actor="dev-lane-0",
                task_id="TASK-1",
                payload={"reason": "respawn pending"},
            ),
        ],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert len(items) == 1
    assert items[0]["failure_class"] == "worker_runner_failed"


def test_plan_admission_stays_on_bounded_plan_revision_route() -> None:
    items = build_attention_items(
        events=[
            ZfEvent(
                type="prd.plan.failed",
                id="evt-plan-admission",
                correlation_id="trace-plan-admission-fault-001",
                payload={
                    "failure_scope": "plan_admission",
                    "plan_admission_incident_id": "plan-admission-001",
                    "expected_fault": True,
                    "reason": "task map lacks source refs",
                    "workflow_run_id": "run-plan-1",
                },
            ),
        ],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert len(items) == 1
    item = items[0]
    assert item["source"] == "plan_admission"
    assert item["suggested_route"] == "plan_revision"
    assert item["failure_scope"] == "plan_admission"
    assert item["expected_fault"] is True
    assert item["notification_policy"] == "trace_only"


def test_run_manager_human_escalate_becomes_run_manager_decision_attention() -> None:
    items = build_attention_items(
        events=[
            ZfEvent(
                type="human.escalate",
                id="evt-human-rm",
                actor="run-manager",
                payload={
                    "schema_version": "human-escalation-package.v1",
                    "owner_route": "run_manager",
                    "decision_token": "hdec-r5",
                    "reason": "approve bounded resume",
                },
            ),
        ],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert len(items) == 1
    assert items[0]["source"] == "run_manager_decision"
    assert items[0]["suggested_route"] == "run_manager_human_decision"
    assert items[0]["human_action_required"] is True
    assert items[0]["decision_token"] == "[REDACTED_SECRET]"


def test_repeated_expected_negative_event_creates_diagnostic_attention() -> None:
    items = build_attention_items(
        events=[
            ZfEvent(
                type="test.failed",
                id="evt-test-failed-1",
                task_id="TASK-TEST",
                payload={"fanout_id": "verify-attempt-1", "reason": "unit failure"},
            ),
            ZfEvent(
                type="test.failed",
                id="evt-test-failed-2",
                task_id="TASK-TEST",
                payload={"fanout_id": "verify-attempt-2", "reason": "unit failure"},
            ),
        ],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert len(items) == 1
    assert items[0]["suggested_route"] == "run_manager_recovery"
    assert items[0]["problem_envelope"]["problem_class"] == "candidate_quality"
    assert items[0]["problem_envelope"]["owner_route"] == "run_manager"
    assert items[0]["source_event_ids"] == ["evt-test-failed-1", "evt-test-failed-2"]


def test_attention_lifecycle_resolves_after_run_completed() -> None:
    item = {
        "fingerprint": "supervisor_projection_stale:/tmp/snapshot.json",
        "attention_id": "attn-stale",
        "status": "open",
        "source_event_ids": ["evt-stale"],
    }
    events = [
        ZfEvent(
            id="evt-stale",
            type="supervisor.projection.stale",
            payload={"snapshot_path": "/tmp/snapshot.json"},
        ),
        ZfEvent(
            id="evt-run-completed",
            type="run.completed",
            payload={"status": "passed", "run_id": "R-ISSUE"},
        ),
    ]

    updated = apply_attention_lifecycle(
        [item],
        events,
        now=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )

    assert updated[0]["status"] == "resolved"
    assert updated[0]["quiesced_by"] == "later_progress"


def test_attention_lifecycle_resolves_after_matching_fanout_terminal() -> None:
    item = {
        "fingerprint": "failure:fanout_child_pending:fanout-1:child-1",
        "attention_id": "attn-fanout",
        "status": "open",
        "source_event_ids": ["evt-fanout-pending"],
    }
    events = [
        ZfEvent(
            id="evt-fanout-pending",
            type="runtime.attention.needed",
            payload={
                "fanout_id": "fanout-1",
                "child_id": "child-1",
                "summary": "Fanout child dispatched without a terminal child event",
            },
        ),
        ZfEvent(
            id="evt-child-completed",
            type="fanout.child.completed",
            payload={
                "fanout_id": "fanout-1",
                "child_id": "child-1",
                "status": "completed",
            },
        ),
    ]

    updated = apply_attention_lifecycle(
        [item],
        events,
        now=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )

    assert updated[0]["status"] == "resolved"


def test_attention_lifecycle_resolves_stuck_after_matching_worker_activity() -> None:
    item = {
        "fingerprint": "failure:worker_stuck:dev-lane-0",
        "attention_id": "attn-stuck",
        "status": "open",
        "source_event_ids": ["evt-worker-stuck"],
    }
    events = [
        ZfEvent(
            id="evt-worker-stuck",
            type="worker.stuck",
            actor="zf-cli",
            payload={"instance_id": "dev-lane-0"},
        ),
        ZfEvent(
            id="evt-worker-active",
            type="agent.usage",
            actor="dev-lane-0",
            payload={},
        ),
    ]

    updated = apply_attention_lifecycle(
        [item],
        events,
        now=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )

    assert updated[0]["status"] == "resolved"
    assert updated[0]["quiesced_by"] == "later_progress"


def test_repeated_flow_goal_blocked_routes_to_run_manager() -> None:
    items = build_attention_items(
        events=[
            ZfEvent(
                type="flow.goal.blocked",
                id="evt-flow-blocked-1",
                payload={"pdd_id": "PDD-1", "reason": "dashboard gap"},
            ),
            ZfEvent(
                type="flow.goal.blocked",
                id="evt-flow-blocked-2",
                payload={"pdd_id": "PDD-1", "reason": "dashboard gap"},
            ),
        ],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert len(items) == 1
    assert items[0]["suggested_route"] == "run_manager_recovery"
    assert items[0]["failure_class"] == "flow_goal_blocked"
    assert items[0]["problem_envelope"]["problem_class"] == "product_gap"


def test_every_attention_item_carries_problem_envelope() -> None:
    """131-P1-2 forcing:Supervisor attention 必带 problem_envelope,缺失即红。"""
    events = [
        ZfEvent(type="human.escalate", payload={"reason": "cap exceeded", "task_id": "T-1"}),
        ZfEvent(type="orchestrator.tick.failed", payload={"error": "boom"}),
        ZfEvent(type="zaofu.bug.detected", payload={"summary": "bug"}),
    ]
    items = build_attention_items(
        events=events,
        automation={},
        failure_signals=[],
        plan_integrity={},
    )
    assert items, "事件应产生 attention items"
    for item in items:
        envelope = item.get("problem_envelope")
        assert isinstance(envelope, dict), f"item {item.get('fingerprint')} 缺 envelope"
        assert envelope.get("schema_version"), f"item {item.get('fingerprint')} envelope 无 schema"
        assert envelope.get("problem_class"), f"item {item.get('fingerprint')} envelope 无 problem_class"


def test_automation_alerts_fold_by_problem_fingerprint() -> None:
    """ZF-E2E-MINI-P3 (2026-07-11): repeats of the same problem through the
    project-monitor alerts channel must share one fingerprint (registry
    dedupe keys), not one per event id — a single frozen budget produced 13
    owner-inbox rows because automation:alerts:evt-<id> never folds."""
    def _alert_ref(evt_id: str) -> dict:
        return {
            "event_id": evt_id,
            "type": "cost.budget.exceeded",
            "task_id": "",
            "actor": "zf-cli",
            "reason": "budget exceeded",
            "problem_fingerprint": (
                "cost.budget.exceeded:scope=global:budget_usd=6.0"
            ),
        }

    automation = {
        "items": [
            {
                "automation_id": "project-monitor",
                "outputs": [{
                    "alerts": [_alert_ref(f"evt-{i}") for i in range(5)],
                }],
            },
        ],
    }
    items = build_attention_items(
        events=[],
        automation=automation,
        failure_signals=[],
        plan_integrity={},
    )
    fingerprints = {
        str(item["fingerprint"]) for item in items
        if str(item["source"]) == "automation"
    }
    assert fingerprints == {
        "automation:alerts:cost.budget.exceeded:scope=global:budget_usd=6.0"
    }


def test_automation_alerts_without_problem_fingerprint_keep_event_id() -> None:
    # Old-shape refs (no problem_fingerprint) keep the per-event fallback —
    # no silent behavior change for projections built by older code.
    automation = {
        "items": [
            {
                "automation_id": "project-monitor",
                "outputs": [{
                    "alerts": [{
                        "event_id": "evt-legacy",
                        "type": "dispatch.silent_stall",
                        "reason": "no matching task",
                    }],
                }],
            },
        ],
    }
    items = build_attention_items(
        events=[],
        automation=automation,
        failure_signals=[],
        plan_integrity={},
    )
    automation_items = [
        item for item in items if str(item["source"]) == "automation"
    ]
    assert len(automation_items) == 1
    assert automation_items[0]["fingerprint"] == "automation:alerts:evt-legacy"
