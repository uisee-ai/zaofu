from __future__ import annotations

from zf.core.config.workflow_profiles import expand_workflow_profile
from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.runtime.event_problem_registry import (
    EVENT_PROBLEM_SPECS,
    NOTIFICATION_POLICIES,
    RECOVERY_POLICIES,
    event_consumer_contract_gaps,
    spec_for_event,
)


def test_flow_semantic_failure_events_have_consumer_contracts() -> None:
    required = {
        "dev.blocked",
        "dev.failed",
        "flow.discovery.failed",
        "flow.goal.blocked",
        "gate.failed",
        "goal.rescan.failed",
        "goal.closure.blocked",
        "module.parity.blocked",
        "module.parity.scan.failed",
        "cangjie.module.parity.scan.failed",
        "zaofu.refactor.plan.blocked",
        "task.attempt.failed",
    }

    assert required <= set(EVENT_PROBLEM_SPECS)
    for event_type in required:
        spec = spec_for_event(event_type)
        assert spec is not None
        assert spec.event_class == "expected_negative"
        assert spec.owner_route in {"run_manager", "human"}
        assert (
            "pending_action" in spec.run_manager_semantics
            or spec.autoresearch_eligible
            or spec.supervisor_attention != "none"
        )


def test_goal_closure_rejected_is_owned_by_semantic_router_only() -> None:
    rejected = spec_for_event("goal.closure.rejected")
    blocked = spec_for_event("goal.closure.blocked")

    assert rejected is not None
    assert rejected.owner_route == "semantic_router"
    assert rejected.run_manager_semantics == ()
    assert rejected.effective_notification_policy == "trace_only"
    assert blocked is not None
    assert blocked.owner_route == "run_manager"
    assert "pending_action" in blocked.run_manager_semantics


def test_known_flow_failure_events_do_not_have_consumer_contract_gaps() -> None:
    known_flow_failures = {
        event_type
        for event_type in KNOWN_EVENT_TYPES
        if (
            event_type.startswith((
                "dev.",
                "flow.",
                "gate.",
                "goal.",
                "module.parity.",
                "cangjie.module.parity.",
                "issue.",
                "prd.",
                "task_map.",
                "product_delivery.task_map.",
                "workflow.stage.",
            ))
            and event_type.endswith((
                ".failed",
                ".blocked",
                ".rejected",
                ".suspended",
                ".missing",
            ))
        )
    }

    assert event_consumer_contract_gaps(known_flow_failures) == []


def test_refactor_flow_profile_failure_events_have_registry_entries() -> None:
    expansion = expand_workflow_profile({
        "flowProfile": "refactor-flow/v3",
        "entryTrigger": "refactor.scan.requested",
        "assembly": "none",
    })
    failure_events: set[str] = set()
    for stage in expansion["stages"]:
        aggregate = stage.get("aggregate") or {}
        for key in ("failure_event", "child_failure_event"):
            failure_event = str(aggregate.get(key) or "")
            if failure_event:
                failure_events.add(failure_event)

    assert {
        "zaofu.refactor.plan.blocked",
        "verify.failed",
        "verify.bridge.child.failed",
        "module.parity.scan.failed",
        "module.parity.child.failed",
        "goal.closure.synthesis.failed",
        "judge.child.failed",
    } <= failure_events
    assert event_consumer_contract_gaps(failure_events) == []
    assert spec_for_event("impl.child.failed") is not None
    assert spec_for_event("lane.stage.failed") is not None


def test_notification_and_recovery_policies_are_registered_values() -> None:
    for spec in EVENT_PROBLEM_SPECS.values():
        assert spec.effective_notification_policy in NOTIFICATION_POLICIES
        assert spec.effective_recovery_policy in RECOVERY_POLICIES


def test_budget_exceeded_triages_through_run_manager_policy() -> None:
    spec = spec_for_event("cost.budget.exceeded")

    assert spec is not None
    assert spec.owner_route == "run_manager"
    assert spec.effective_recovery_policy == "run_manager"
    assert spec.effective_notification_policy == "owner_on_human_required"
    assert spec.dedupe_key_fields == ("scope", "role", "budget_usd")


def test_channel_route_blocked_is_projection_only() -> None:
    """2026-07-16 operator review: the anti-storm guard event
    channel.route.blocked (auto_route_not_allowed, doc 64 §5) is by-design and
    UI-owned; unregistered, its ".blocked" suffix made run_manager raise an
    "Unregistered actionable event" diagnosis and spin up autoresearch on
    every agent reply."""
    spec = spec_for_event("channel.route.blocked")
    assert spec is not None
    assert spec.event_class == "projection_only"
    assert spec.autoresearch_eligible is False
    assert spec.supervisor_attention == "none"


def test_channel_route_blocked_never_unknown_actionable() -> None:
    from zf.core.events.model import ZfEvent
    from zf.runtime.run_manager import _pending_semantic_event_actions

    event = ZfEvent(
        type="channel.route.blocked",
        payload={"reason": "auto_route_not_allowed", "channel_id": "ch-x"},
    )
    assert _pending_semantic_event_actions([event]) == []

def test_worker_pane_evidence_is_not_an_autoresearch_source_repair_trigger() -> None:
    pane = spec_for_event("worker.pane.dead_observed")
    runner = spec_for_event("worker.runner.failed")

    assert pane is not None
    assert pane.is_projection_only
    assert pane.supervisor_attention == "none"
    assert pane.autoresearch_eligible is False
    assert runner is not None
    assert runner.is_expected_negative
    assert runner.supervisor_attention == "on_repeated"
    assert runner.autoresearch_eligible is False
