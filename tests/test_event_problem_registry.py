from __future__ import annotations

from zf.core.config.workflow_profiles import expand_workflow_profile
from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.runtime.event_problem_registry import (
    EVENT_PROBLEM_SPECS,
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
    }

    assert required <= set(EVENT_PROBLEM_SPECS)
    for event_type in required:
        spec = spec_for_event(event_type)
        assert spec is not None
        assert spec.event_class == "expected_negative"
        assert spec.owner_route in {"run_manager", "orchestrator_replan", "human"}
        assert (
            "pending_action" in spec.run_manager_semantics
            or spec.autoresearch_eligible
            or spec.supervisor_attention != "none"
        )


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
        "judge.failed",
        "judge.child.failed",
    } <= failure_events
    assert event_consumer_contract_gaps(failure_events) == []
    assert spec_for_event("impl.child.failed") is not None
    assert spec_for_event("lane.stage.failed") is not None
