from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager_router import (
    build_no_progress_projection,
    classify_recovery_context,
    decide_action_policy,
    preflight_action,
    recovery_closeout_contract_report,
    route_for_safe_action,
)


def test_action_router_registry_classifies_safe_resume() -> None:
    route = route_for_safe_action("repair_failed_children")

    assert route.failure_class == "deterministic_resume"
    assert route.owner_route == "controlled_action"
    assert route.action_policy == "auto_decide"
    assert route.intervention_class == "auto_recover"
    assert "task_map.ready" in route.expected_downstream_events


def test_action_router_classifies_semantic_replan_and_diagnosis() -> None:
    replan = route_for_safe_action("trigger_rework")
    unknown = route_for_safe_action("replex_reflection")

    assert replan.failure_class == "task_map_drift"
    assert replan.owner_route == "orchestrator_replan"
    assert replan.intervention_class == "semantic_replan"
    assert unknown.failure_class == "unknown_complex"
    assert unknown.owner_route == "run_manager"
    assert unknown.intervention_class == "diagnose"


def test_low_severity_does_not_block_auto_recover_decision() -> None:
    payload = {
        "checkpoint_id": "ck-1",
        "safe_resume_action": "repair_failed_children",
        "severity": "low",
        **classify_recovery_context({"safe_resume_action": "repair_failed_children"}),
    }

    decision = decide_action_policy(action="workflow-batch-resume", payload=payload)

    assert decision["decision"] == "auto_decide"
    assert decision["intervention_class"] == "auto_recover"


def test_diagnose_attention_policy_routes_to_run_manager_diagnosis() -> None:
    payload = {
        "checkpoint_id": "attention-diagnosis-1",
        "safe_resume_action": "diagnose_attention",
        "fingerprint": "runtime:dispatch.silent_stall:T1",
        "failure_class": "worker_noop_or_terminal_missing",
        "owner_route": "run_manager",
        "action_policy": "needs_diagnosis",
        "intervention_class": "diagnose",
        "verify_condition": (
            "expected_downstream_event:"
            "run.manager.autoresearch.requested,run.manager.resident.prompted"
        ),
        "source_event_ids": ["evt-stall-1"],
    }

    preflight = preflight_action(action="diagnose-attention", payload=payload)
    decision = decide_action_policy(action="diagnose-attention", payload=payload)

    assert preflight["status"] == "passed"
    assert preflight["expected_downstream_events"] == [
        "run.manager.autoresearch.requested",
        "run.manager.resident.prompted",
    ]
    assert decision["decision"] == "needs_diagnosis"
    assert decision["executable"] is True
    assert decision["owner_route"] == "run_manager"
    assert decision["intervention_class"] == "diagnose"


def test_unclassified_action_routes_to_run_manager_diagnosis() -> None:
    decision = decide_action_policy(
        action="new-runtime-attention",
        payload={
            "checkpoint_id": "ck-unknown-1",
            "fingerprint": "runtime:new:1",
            "source_event_ids": ["evt-1"],
        },
    )

    assert decision["decision"] == "needs_diagnosis"
    assert decision["executable"] is True
    assert decision["owner_route"] == "run_manager"


def test_preflight_blocks_mutating_rework_without_human_decision() -> None:
    payload = {
        "checkpoint_id": "ck-1",
        "safe_resume_action": "trigger_rework",
        **classify_recovery_context({"safe_resume_action": "trigger_rework"}),
    }

    preflight = preflight_action(action="workflow-batch-resume", payload=payload)
    decision = decide_action_policy(action="workflow-batch-resume", payload=payload)

    assert preflight["status"] == "blocked"
    assert "mutating_resume_requires_human_decision" in preflight["failures"]
    assert decision["decision"] == "human_escalate"
    assert decision["intervention_class"] == "human_decision"
    assert decision["preflight"]["status"] == "blocked"


def test_no_progress_projection_trips_after_repeated_fingerprint() -> None:
    events = [
        ZfEvent(
            type="run.manager.action.verify.failed",
            payload={"checkpoint_id": "ck-1", "reason": "missing downstream"},
        ),
        ZfEvent(
            type="run.manager.action.blocked",
            payload={"checkpoint_id": "ck-1", "reason": "needs human"},
        ),
        ZfEvent(
            type="human.escalate",
            payload={"checkpoint_id": "ck-1", "reason": "needs human"},
        ),
    ]

    projection = build_no_progress_projection(events)

    assert projection["status"] == "tripped"
    assert projection["items"][0]["fingerprint"] == "ck-1"
    assert projection["items"][0]["count"] == 3


def test_no_progress_projection_trips_candidate_quality_loop() -> None:
    events = [
        ZfEvent(
            type="integration.failed",
            payload={
                "pdd_id": "CANGJIE-R3",
                "stage_id": "cangjie-slice-implementation",
                "quality_gates_failed": ["candidate_worktree_clean"],
                "candidate_head_commit": f"head-{index}",
            },
        )
        for index in range(3)
    ]

    projection = build_no_progress_projection(events)

    assert projection["status"] == "tripped"
    assert projection["items"][0]["count"] == 3
    assert projection["items"][0]["fingerprint"].startswith("rmfp-")


def test_no_progress_projection_trips_writer_binding_recovery_loop() -> None:
    events = [
        ZfEvent(
            type="task.dispatch_context.bound",
            payload={
                "source": "writer_fanout_task_binding_recovery",
                "fanout_id": "fanout-dev-old",
                "child_id": "dev-1-TASK-1",
                "dispatch_id": "run-dev-1-TASK-1",
            },
        )
        for _index in range(3)
    ]

    projection = build_no_progress_projection(events)

    assert projection["status"] == "tripped"
    assert projection["items"][0]["count"] == 3
    assert projection["items"][0]["event_type"] == "task.dispatch_context.bound"


def test_no_progress_projection_trips_stale_completion_loop() -> None:
    events = [
        ZfEvent(
            type="fanout.child.stale_completion",
            payload={
                "reason": "superseded_by_latest_fanout",
                "fanout_id": "fanout-dev-old",
                "child_id": "dev-1-TASK-1",
                "source_event_type": "task.dispatch_context.bound",
                "result_event_id": f"evt-changing-{index}",
            },
        )
        for index in range(3)
    ]

    projection = build_no_progress_projection(events)

    assert projection["status"] == "tripped"
    assert projection["items"][0]["count"] == 3
    assert projection["items"][0]["event_type"] == "fanout.child.stale_completion"


def test_recovery_closeout_contract_report_is_complete_for_goal_gap() -> None:
    report = recovery_closeout_contract_report(event_types={"flow.goal.blocked"})

    assert report["ok"] is True
    assert report["summary"]["checked"] == 1
    entry = report["entries"][0]
    assert entry["event_type"] == "flow.goal.blocked"
    assert entry["owner_route"] == "run_manager"
    assert entry["attempt_cap"] >= 1
    assert entry["expected_downstream_events"]
