import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.rework_triage import (
    classify_rework_trigger,
    is_plan_level_task_contract_blocker,
)
from zf.runtime.run_manager_rework_triage import pending_immediate_replan_actions


@pytest.mark.parametrize("failure_class", [
    "task_context_stale", "workspace_binding_mismatch", "runner_failure",
    "input_delivery_failed", "harness_failure", "runtime_result_admission",
])
def test_context_blocker_never_requests_product_replan(failure_class):
    event = ZfEvent(
        type="dev.blocked",
        task_id="TASK-1",
        origin="kernel",
        payload={
            "failure_class": failure_class,
            # Even a mixed legacy scope diagnosis must not replace the product plan.
            "blocker_kind": "upstream_contract_gap",
            "reason": "capsule differs from snapshot; outside this child contract",
            "evidence_refs": ["artifacts/context-diff.json"],
        },
    )
    result = classify_rework_trigger(event)
    assert not is_plan_level_task_contract_blocker(event)
    assert result.classification == "harness_rule_issue"
    assert result.recommended_action == "suspend_for_harness_fix"
    assert not result.should_increment_retry
    triage = ZfEvent(
        type="task.rework.triage.completed",
        task_id=event.task_id,
        payload=result.to_payload(event),
    )
    assert pending_immediate_replan_actions([event, triage]) == []


def test_real_upstream_gap_keeps_semantic_replan():
    event = ZfEvent(
        type="dev.blocked",
        task_id="TASK-1",
        payload={
            "failure_class": "upstream_contract_gap",
            "reason": "required API is outside allowed_paths",
            "evidence_refs": ["artifacts/contract-gap.json"],
        },
    )
    assert is_plan_level_task_contract_blocker(event)
    assert classify_rework_trigger(event).recommended_action == "request_replan"


@pytest.mark.parametrize("failure_class", ["workspace_binding_mismatch", "runner_failure", "input_delivery_failed"])
def test_reader_infrastructure_failure_does_not_loop_or_consume_semantic_budget(failure_class):
    from types import SimpleNamespace
    from zf.runtime.stage_failure_replan import plan_reader_stage_replan
    from zf.runtime.failure_kind import failure_kind_from_payload

    stage = SimpleNamespace(
        id="prd-discovery", topology="fanout_reader", trigger="flow.discovery.requested",
        failure_event="", aggregate=SimpleNamespace(failure_event="flow.discovery.failed"),
    )
    config = SimpleNamespace(workflow=SimpleNamespace(stages=[stage]))
    failure = ZfEvent(type="flow.discovery.failed", correlation_id="run", payload={
        "failure_class": failure_class, "recommendation": "needs_rework",
        "target_ref": "same-commit", "reason": "cannot run assigned acceptance command",
    })
    for events in ([failure], [failure, failure, failure]):
        replan, note = plan_reader_stage_replan(config, events, failure)
        assert replan is None
        assert note == "infrastructure_failure"
    assert failure_kind_from_payload(failure.payload) == "infra"
