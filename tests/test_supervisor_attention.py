from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.supervisor_attention import build_attention_items


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
                payload={"reason": "unit failure"},
            ),
            ZfEvent(
                type="test.failed",
                id="evt-test-failed-2",
                task_id="TASK-TEST",
                payload={"reason": "unit failure"},
            ),
        ],
        automation={},
        failure_signals=[],
        plan_integrity={},
    )

    assert len(items) == 1
    assert items[0]["suggested_route"] == "autoresearch_trigger"
    assert items[0]["problem_envelope"]["problem_class"] == "candidate_quality"
    assert items[0]["source_event_ids"] == ["evt-test-failed-1", "evt-test-failed-2"]


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
