from __future__ import annotations

from zf.autoresearch.failure_signals import FailureSignal
from zf.core.events.model import ZfEvent
from zf.runtime.problem_taxonomy import (
    abnormal_event_projection,
    problem_envelope_from_action,
    problem_envelope_from_attention,
    problem_envelope_from_event,
    problem_envelope_from_failure_signal,
)


def test_failure_signal_projects_to_unified_problem_envelope() -> None:
    signal = FailureSignal(
        signal_id="sig-worker",
        source_kind="events",
        source_path=".zf/events.jsonl",
        event_ids=["evt-worker"],
        fingerprint="worker:dev-lane-0",
        category="worker_stuck",
        severity="high",
        summary="worker heartbeat stopped",
    )

    envelope = problem_envelope_from_failure_signal(signal)

    assert envelope["problem_class"] == "worker_lifecycle"
    assert envelope["failure_class"] == "worker_stuck"
    assert envelope["owner_route"] == "controlled_action"
    assert envelope["action_policy"] == "auto_decide"
    assert envelope["source_event_ids"] == ["evt-worker"]


def test_attention_projects_workflow_resume_problem() -> None:
    envelope = problem_envelope_from_attention({
        "source": "workflow_resume",
        "fingerprint": "workflow_resume_batch:ck-1",
        "severity": "high",
        "title": "Workflow batch checkpoint can be resumed",
        "summary": "repair failed children",
        "suggested_route": "run_manager_recovery",
        "suggested_action": {
            "kind": "workflow-batch-resume",
            "safe_resume_action": "repair_failed_children",
        },
        "source_event_ids": ["evt-ck"],
    })

    assert envelope["problem_class"] == "workflow_progress"
    assert envelope["owner_route"] == "run_manager"
    assert "workflow-batch-resume" in envelope["allowed_actions"]


def test_run_manager_action_projects_source_repair_problem() -> None:
    envelope = problem_envelope_from_action({
        "action": "repair-closeout-validate",
        "checkpoint_id": "repair-1",
        "failure_class": "self_repair_validation",
        "owner_route": "controlled_action",
        "action_policy": "auto_decide",
        "intervention_class": "auto_recover",
        "source_ref": "events.jsonl#evt-repair",
    })

    assert envelope["problem_class"] == "source_repair"
    assert envelope["failure_class"] == "self_repair_validation"
    assert envelope["owner_route"] == "controlled_action"
    assert envelope["source_refs"] == ["events.jsonl#evt-repair"]


def test_abnormal_event_registry_projects_runtime_stall() -> None:
    event = ZfEvent(
        type="dispatch.silent_stall",
        id="evt-stall",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-impl",
            "reason": "task.assigned had no matching task",
        },
    )

    projection = abnormal_event_projection(event)
    envelope = problem_envelope_from_event(event)

    assert projection is not None
    assert projection["suggested_route"] == "run_manager_recovery"
    assert projection["source_event_ids"] == ["evt-stall"]
    assert envelope is not None
    assert envelope["problem_class"] == "workflow_progress"
    assert envelope["owner_route"] == "run_manager"
    assert envelope["source_refs"] == ["events.jsonl#evt-stall"]


def test_budget_exceeded_projection_uses_stable_policy_fingerprint() -> None:
    first = ZfEvent(
        type="cost.budget.exceeded",
        id="evt-budget-1",
        payload={
            "scope": "global",
            "budget_usd": 60.0,
            "current_usd": 96.03,
        },
    )
    second = ZfEvent(
        type="cost.budget.exceeded",
        id="evt-budget-2",
        payload={
            "scope": "global",
            "budget_usd": 60.0,
            "current_usd": 96.03,
        },
    )

    p1 = abnormal_event_projection(first)
    p2 = abnormal_event_projection(second)

    assert p1 is not None
    assert p2 is not None
    assert p1["fingerprint"] == p2["fingerprint"]
    assert p1["notification_policy"] == "owner_on_human_required"
    assert p1["recovery_policy"] == "run_manager"
    assert p1["problem_envelope"]["problem_class"] == "runtime_liveness"


def test_expected_negative_event_projects_without_becoming_actionable() -> None:
    event = ZfEvent(
        type="verify.failed",
        id="evt-verify-failed",
        task_id="TASK-2",
        payload={"reason": "missing real provider evidence"},
    )

    projection = abnormal_event_projection(event)
    envelope = problem_envelope_from_event(event)

    assert projection is None
    assert envelope is not None
    assert envelope["problem_class"] == "candidate_quality"
    assert envelope["suggested_route"] == "workflow_rework"


def test_flow_semantic_events_project_to_product_gap_envelopes() -> None:
    for event_type, failure_class in {
        "flow.discovery.failed": "flow_discovery_failed",
        "goal.rescan.failed": "flow_discovery_failed",
        "module.parity.scan.failed": "flow_discovery_failed",
        "cangjie.module.parity.scan.failed": "flow_discovery_failed",
        "flow.goal.blocked": "flow_goal_blocked",
        "goal.closure.blocked": "flow_goal_blocked",
        "module.parity.blocked": "flow_goal_blocked",
    }.items():
        envelope = problem_envelope_from_event(ZfEvent(
            type=event_type,
            id=f"evt-{event_type}",
            payload={"pdd_id": "PDD-1", "reason": "gap remains"},
        ))

        assert envelope is not None
        assert envelope["problem_class"] == "product_gap"
        assert envelope["failure_class"] == failure_class
        assert envelope["owner_route"] == "run_manager"
        assert envelope["suggested_route"] == "run_manager_recovery"


def test_refactor_plan_blocked_projects_to_artifact_contract_envelope() -> None:
    envelope = problem_envelope_from_event(ZfEvent(
        type="zaofu.refactor.plan.blocked",
        id="evt-plan-blocked",
        payload={"pdd_id": "PDD-1", "reason": "task map missing"},
    ))

    assert envelope is not None
    assert envelope["problem_class"] == "artifact_contract"
    assert envelope["failure_class"] == "refactor_plan_blocked"
    assert envelope["owner_route"] == "run_manager"


def test_owner_delivery_failure_overrides_external_gate_to_run_manager() -> None:
    event = ZfEvent(
        type="owner.visible_message.failed",
        id="evt-owner-failed",
        payload={
            "message_id": "omsg-1",
            "reason": "Feishu delivery failed",
        },
    )

    envelope = problem_envelope_from_event(event)

    assert envelope is not None
    assert envelope["problem_class"] == "external_gate"
    assert envelope["owner_route"] == "run_manager"
    assert envelope["action_policy"] == "needs_diagnosis"
