from __future__ import annotations

from zf.core.config.schema_profiles import resolve_schema_profile
from zf.core.events.model import ZfEvent
from zf.runtime.event_problem_registry import spec_for_event
from zf.runtime.orchestrator_reactor import _BUILTIN_HANDLER_METHODS
from zf.runtime.run_manager import run_goal_completion_gate_event
from zf.runtime.wake_patterns import WAKE_PATTERNS


def test_canonical_dag_v5_adds_durable_call_contracts_without_mutating_v4() -> None:
    v4 = resolve_schema_profile("canonical-dag/v4")
    v5 = resolve_schema_profile("canonical-dag/v5")
    assert "workflow.call.result.admitted" not in v4
    assert "workflow.call.result.admitted" in v5
    assert "workflow.operation.settled" in v5
    assert v5["verify.child.completed"] == v4["verify.child.completed"]


def test_call_result_problem_policy_keeps_local_repair_bounded_before_run_manager() -> None:
    repair = spec_for_event("workflow.call.result.repair.requested")
    invalid = spec_for_event("workflow.call.result.invalid")
    assert repair is not None and repair.recovery_policy == "none"
    assert repair.notification_policy == "trace_only"
    assert repair.run_manager_semantics == ()
    assert repair.autoresearch_eligible is False
    assert invalid is not None and invalid.recovery_policy == "run_manager_then_autoresearch"
    assert invalid.autoresearch_eligible is True


def test_durable_aggregate_settlement_does_not_restore_fanout_self_wake() -> None:
    handlers = dict(_BUILTIN_HANDLER_METHODS)
    assert "fanout.aggregate.completed" not in handlers
    assert "fanout.aggregate.completed" not in WAKE_PATTERNS


def test_goal_gate_blocks_only_declared_unsettled_operations() -> None:
    started = ZfEvent(
        type="run.goal.started",
        payload={"run_id": "run-goal", "objective": "deliver"},
    )
    operation = ZfEvent(
        type="workflow.operation.requested",
        task_id="T1",
        correlation_id="run-goal",
        payload={
            "workflow_run_id": "run-goal",
            "operation_id": "op-required",
            "request_hash": "hash-1",
        },
    )
    claim = ZfEvent(
        id="claim-1",
        type="run.goal.completion.claimed",
        correlation_id="run-goal",
        payload={"run_id": "run-goal", "objective": "deliver"},
    )
    blocked = run_goal_completion_gate_event(
        [started, operation, claim],
        claim=claim,
        required_operation_ids=["op-required"],
    )
    assert blocked is not None
    assert blocked.type == "run.goal.completion.blocked"
    assert "unsettled_required_operation" in blocked.payload["blockers"]

    settled = ZfEvent(
        type="workflow.operation.settled",
        task_id="T1",
        correlation_id="run-goal",
        payload={
            "workflow_run_id": "run-goal",
            "operation_id": "op-required",
            "request_hash": "hash-1",
            "admitted_call_result_ref": {
                "ref": "artifacts/result.json",
                "sha256": "a" * 64,
            },
        },
    )
    claim2 = ZfEvent(
        id="claim-2",
        type="run.goal.completion.claimed",
        correlation_id="run-goal",
        payload={"run_id": "run-goal", "objective": "deliver"},
    )
    passed = run_goal_completion_gate_event(
        [started, operation, settled, claim2],
        claim=claim2,
        required_operation_ids=["op-required"],
    )
    assert passed is not None and passed.type == "run.goal.completed"
