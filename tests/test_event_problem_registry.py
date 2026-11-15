from __future__ import annotations

from zf.core.config.workflow_profiles import expand_workflow_profile
from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.runtime.event_problem_registry import (
    EVENT_PROBLEM_SPECS,
    NOTIFICATION_POLICIES,
    RECOVERY_POLICIES,
    autoresearch_eligible_failure_classes,
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


def test_goal_identity_invalid_tries_run_manager_rebuild_before_autoresearch() -> None:
    identity = spec_for_event("goal.closure.identity.invalid")

    assert identity is not None
    assert identity.owner_route == "run_manager"
    assert identity.suggested_action_kind == "fanout-aggregate-rebuild"
    assert identity.run_manager_semantics == ("pending_action",)
    assert identity.autoresearch_eligible is False
    assert identity.effective_recovery_policy == "run_manager_then_autoresearch"
    assert (
        "goal_closure_identity_invalid"
        not in autoresearch_eligible_failure_classes()
    )


def test_retained_cleanup_is_observable_without_authorizing_automatic_deletion():
    spec = spec_for_event("workspace.cleanup.blocked")
    assert spec is not None and spec.supervisor_attention == "on_single"
    assert spec.effective_recovery_policy == "none"
    assert spec.effective_notification_policy == "trace_only"
    assert not spec.autoresearch_eligible
    assert event_consumer_contract_gaps({"workspace.cleanup.blocked"}) == []


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


def test_controlled_action_rejection_is_not_an_autoresearch_trigger() -> None:
    from zf.core.events.model import ZfEvent
    from zf.runtime.run_manager import _pending_semantic_event_actions

    spec = spec_for_event("runtime.action.rejected")
    assert spec is not None
    assert spec.event_class == "expected_negative"
    assert spec.action_policy == "kernel_consumed"
    assert spec.autoresearch_eligible is False

    event = ZfEvent(
        type="runtime.action.rejected",
        payload={
            "action": "create-task",
            "reason": "contract.verification_tiers is required",
        },
    )
    assert _pending_semantic_event_actions([event]) == []


def test_failure_envelopes_do_not_create_recovery_actions() -> None:
    from zf.core.events.model import ZfEvent
    from zf.runtime.run_manager import _pending_semantic_event_actions

    events = [
        ZfEvent(type="runtime.action.attempt.failed", payload={
            "attempt_id": "attempt-1", "action": "question-resolve",
        }),
        ZfEvent(type="runtime.action.failed", payload={
            "action": "question-resolve",
        }),
        ZfEvent(type="web.action.failed", payload={
            "action": "question-resolve",
        }),
        ZfEvent(type="agent.session.run.failed", payload={
            "run_id": "provider-run-1",
        }),
    ]

    assert _pending_semantic_event_actions(events) == []
    assert all(
        spec_for_event(event.type).event_class == "projection_only"
        for event in events
    )


def test_channel_question_dedup_rejection_stays_with_bounded_kernel_retry() -> None:
    spec = spec_for_event("channel.question.dedup.rejected")

    assert spec is not None
    assert spec.event_class == "projection_only"
    assert spec.problem_class == "contract"
    assert spec.owner_route == "run_manager"
    assert spec.run_manager_semantics == ()
    assert spec.autoresearch_eligible is False
    assert spec.effective_notification_policy == "trace_only"
    assert spec.effective_recovery_policy == "none"
    assert spec.dedupe_key_fields == (
        "channel_id",
        "thread_id",
        "request_id",
    )


def test_channel_question_dedup_exhaustion_routes_to_run_manager_once() -> None:
    spec = spec_for_event("channel.question.dedup.remediation.exhausted")

    assert spec is not None
    assert spec.event_class == "abnormal"
    assert spec.run_manager_semantics == ("pending_action",)
    assert spec.autoresearch_eligible is False
    assert spec.effective_recovery_policy == "run_manager"


def test_channel_result_receipt_failure_stays_with_bounded_reconciler() -> None:
    spec = spec_for_event("channel.result.receipt.failed")

    assert spec is not None
    assert spec.event_class == "expected_negative"
    assert spec.problem_class == "external_side_effect"
    assert spec.action_policy == "kernel_consumed"
    assert spec.supervisor_attention == "none"
    assert spec.autoresearch_eligible is False
    assert spec.effective_notification_policy == "trace_only"
    assert spec.effective_recovery_policy == "none"
    assert spec.dedupe_key_fields == (
        "channel_id",
        "thread_id",
        "idempotency_key",
    )


def test_role_lifecycle_suspended_is_normal_observation() -> None:
    spec = spec_for_event("role.lifecycle.suspended")

    assert spec is not None
    assert spec.is_projection_only
    assert spec.problem_class == "worker_lifecycle"
    assert spec.supervisor_attention == "none"
    assert spec.autoresearch_eligible is False
    assert spec.effective_notification_policy == "trace_only"
    assert spec.effective_recovery_policy == "none"


def test_role_lifecycle_suspended_never_enters_repair_paths(tmp_path) -> None:
    from zf.core.events.model import ZfEvent
    from zf.runtime.failure_to_eval import materialize_failure_candidates_from_events
    from zf.runtime.run_manager import _pending_semantic_event_actions

    event = ZfEvent(
        type="role.lifecycle.suspended",
        actor="orchestrator",
        payload={
            "role": "dev",
            "instance_id": "dev-lane-0",
            "from": "suspending",
            "to": "suspended",
        },
    )

    assert _pending_semantic_event_actions([event]) == []
    assert materialize_failure_candidates_from_events(tmp_path, [event]) == []


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


def test_context_warning_is_owned_by_kernel_lifecycle_not_recovery() -> None:
    from zf.core.events.model import ZfEvent
    from zf.runtime.run_manager import _pending_semantic_event_actions

    warning = spec_for_event("worker.context.warning")

    assert warning is not None
    assert warning.is_expected_negative
    assert warning.action_policy == "kernel_consumed"
    assert warning.run_manager_semantics == ()
    assert warning.supervisor_attention == "on_single"
    assert warning.autoresearch_eligible is False
    assert warning.effective_notification_policy == "trace_only"
    assert warning.effective_recovery_policy == "none"

    event = ZfEvent(
        type="worker.context.warning",
        actor="refactor-plan-synth",
        payload={
            "instance_id": "refactor-plan-synth",
            "role": "refactor-plan-synth",
            "context_usage_ratio": 0.61,
            "reason": "recycle_threshold_exceeded",
            "idle": False,
        },
    )
    assert _pending_semantic_event_actions([event]) == []


def test_context_critical_keeps_bounded_recovery_route() -> None:
    critical = spec_for_event("worker.context.critical")

    assert critical is not None
    assert critical.is_expected_negative
    assert critical.run_manager_semantics == ("pending_action",)
    assert critical.autoresearch_eligible is True
