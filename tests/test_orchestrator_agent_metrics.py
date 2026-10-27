from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_metrics import (
    build_orchestrator_agent_metrics,
)


def _event(
    event_type: str,
    event_id: str,
    second: int,
    payload: dict | None = None,
    *,
    task_id: str | None = None,
    causation_id: str | None = None,
) -> ZfEvent:
    return ZfEvent(
        type=event_type,
        id=event_id,
        ts=f"2026-07-31T12:00:{second:02d}+00:00",
        task_id=task_id,
        payload=payload or {},
        causation_id=causation_id,
    )


def test_metrics_reconstruct_semantic_control_health_from_events() -> None:
    events = [
        _event("lane.stage.completed", "evt-normal", 0),
        _event(
            "workflow.operation.requested",
            "evt-op-request",
            1,
            {
                "operation_id": "op-plan",
                "operation_type": "orchestrator_agent_semantic",
                "workflow_run_id": "run-1",
                "parent_stage_id": "oa-plan_candidate",
            },
            causation_id="evt-normal",
        ),
        _event(
            "orchestrator.semantic.checkpoint.requested",
            "evt-checkpoint",
            2,
            {
                "operation_id": "op-plan",
                "checkpoint": "plan_candidate",
                "source_event_id": "evt-normal",
            },
        ),
        _event(
            "agent.usage",
            "evt-usage",
            3,
            {
                "operation_id": "op-plan",
                "usage_sample_id": "sample-plan-1",
                "backend": "codex",
                "model": "default",
                "usage": {"input_tokens": 1000, "output_tokens": 100},
            },
        ),
        _event(
            "workflow.call.result.admitted",
            "evt-result",
            4,
            {
                "operation_id": "op-plan",
                "read_ledger_ref": {
                    "ref": "artifacts/read-ledgers/op-plan.json",
                    "sha256": "abc123",
                },
            },
        ),
        _event(
            "workflow.operation.settled",
            "evt-op-settled",
            6,
            {"operation_id": "op-plan"},
        ),
        _event(
            "orchestrator.semantic.decision.observed",
            "evt-observed",
            6,
            {
                "operation_id": "op-plan",
                "checkpoint": "plan_candidate",
                "decision": "adopt",
                "reason_codes": ["plan_complete"],
                "summary": "Plan is complete.",
                "explanation_status": "complete",
            },
        ),
        _event(
            "orchestrator.semantic.checkpoint.skipped",
            "evt-skipped",
            6,
            {
                "operation_id": "op-plan-skipped",
                "workflow_run_id": "run-2",
                "checkpoint": "plan_candidate",
                "reason": "shadow_sample_not_selected",
                "sample_percent": 25,
                "sample_bucket": 80,
            },
        ),
        _event(
            "orchestrator.semantic.rework.requested",
            "evt-target",
            7,
            {
                "task_id": "TASK-1",
                "target_role_instance": "dev-a",
            },
            task_id="TASK-1",
        ),
        _event(
            "task.rework.requested",
            "evt-rework",
            8,
            {
                "trigger_event_id": "evt-target",
                "assignee": "dev-a",
            },
            task_id="TASK-1",
        ),
        _event(
            "orchestrator.semantic.decision.rejected",
            "evt-stale",
            9,
            {
                "operation_id": "op-plan",
                "reason": "plan_package_stale",
            },
        ),
        _event(
            "owner.visible_message.requested",
            "evt-fallback",
            10,
            {
                "message_kind": "run_terminal_delivery",
                "narrative_status": "degraded",
                "terminal_event_id": "evt-terminal",
                "terminal_event_type": "run.goal.completed",
                "dossier_ref": "projections/goals/run-1/goal-dossier.v1.json",
                "dossier_source_fingerprint": "dossier-fingerprint",
                "completion_receipt_ref": (
                    "projections/goals/run-1/goal-completion-receipt.v1.json"
                ),
                "completion_receipt_fingerprint": "receipt-fingerprint",
                "owner_delivery_composite_ref": (
                    "projections/goals/run-1/owner-delivery-composite.v1.json"
                ),
            },
        ),
        _event(
            "owner.delivery.narrative.degraded",
            "evt-narrative-degraded",
            11,
            {"operation_id": "op-owner"},
        ),
    ]

    metrics = build_orchestrator_agent_metrics(
        list(enumerate(events, start=1))
    )
    summary = metrics["summary"]

    assert metrics["schema_version"] == "orchestrator-agent-metrics.v1"
    assert summary["operation_count"] == 1
    assert summary["settled_operation_count"] == 1
    assert summary["avg_operation_latency_seconds"] == 5.0
    assert summary["p95_operation_latency_seconds"] == 5.0
    assert summary["sla_breach_count"] == 0
    assert summary["degraded_explanation_count"] == 0
    assert summary["stale_reject_count"] == 1
    assert summary["required_read_closure_rate"] == 1.0
    assert summary["target_match_rate"] == 1.0
    assert summary["normal_path_oa_turn_rate"] == 1.0
    assert summary["factual_fallback_reconstructible_rate"] == 1.0
    assert summary["narrative_degraded_count"] == 1
    assert summary["checkpoint_request_count"] == 2
    assert summary["checkpoint_executed_count"] == 1
    assert summary["checkpoint_skipped_count"] == 1
    assert summary["checkpoint_execution_rate"] == 0.5
    assert summary["total_tokens"] == 1100
    assert summary["cost_usd"] == 0.0045
    assert metrics["operations"][0]["required_read_closed"] is True
    assert metrics["operations"][0]["decision"] == "adopt"
    assert metrics["operations"][0]["summary"] == "Plan is complete."
    assert metrics["operations"][0]["sla_threshold_seconds"] == 300
    assert metrics["operations"][0]["sla_breached"] is False
    assert metrics["operations"][0]["total_tokens"] == 1100
    assert metrics["checkpoints"]["plan_candidate"][
        "avg_operation_latency_seconds"
    ] == 5.0
    assert metrics["checkpoints"]["plan_candidate"]["decision_counts"] == {
        "adopt": 1,
    }
    assert metrics["checkpoints"]["plan_candidate"]["skipped_count"] == 1
    assert metrics["checkpoints"]["plan_candidate"]["cost_usd"] == 0.0045


def test_metrics_empty_snapshot_is_stable() -> None:
    metrics = build_orchestrator_agent_metrics([])

    assert metrics["operations"] == []
    assert metrics["checkpoints"] == {}
    assert metrics["summary"]["operation_count"] == 0
    assert metrics["summary"]["required_read_closure_rate"] == 0.0


def test_unknown_model_keeps_tokens_and_marks_partial_cost_through_all_rollups() -> None:
    request = _event("workflow.operation.requested", "request", 0, {
        "operation_id": "op", "operation_type": "orchestrator_agent_semantic",
        "parent_stage_id": "oa-plan_candidate",
    })
    unknown = _event("agent.usage", "unknown", 1, {
        "operation_id": "op", "usage_sample_id": "sample-unknown",
        "model": "unlisted-local-model", "usage": {"input_tokens": 12345, "output_tokens": 7},
    })
    priced = _event("agent.usage", "priced", 2, {
        "operation_id": "op", "model": "unlisted-local-model", "total_cost_usd": 0.123456,
        "usage": {"input_tokens": 9, "output_tokens": 2},
    })
    result = build_orchestrator_agent_metrics([request, unknown, unknown, priced])
    for row in [result["operations"][0], result["checkpoints"]["plan_candidate"], result["summary"]]:
        assert row["input_tokens"] == 12354
        assert row["output_tokens"] == 9
        assert row["cost_usd"] is None
        assert row["known_cost_usd"] == 0.123456
        assert row["unpriced_sample_count"] == 1
    assert result["operations"][0]["usage_sample_count"] == 2


@pytest.mark.parametrize("model", ["", "unlisted-local-model"])
@pytest.mark.parametrize("provider_cost", [None, -1, float("nan"), float("inf"), True])
def test_missing_rate_is_not_a_default_or_free_estimate(model, provider_cost) -> None:
    result = build_orchestrator_agent_metrics([
        _event("workflow.operation.requested", "request", 0, {
            "operation_id": "op", "operation_type": "orchestrator_agent_semantic",
        }),
        _event("agent.usage", "usage", 1, {
            "operation_id": "op", "model": model, "total_cost_usd": provider_cost,
            "usage": {"input_tokens": 1000, "output_tokens": 100},
        }),
    ])
    assert result["summary"]["cost_usd"] is None
    assert result["summary"]["total_tokens"] == 1100
    assert result["summary"]["unpriced_sample_count"] == 1


def test_explicit_provider_zero_is_priced_without_fallback() -> None:
    result = build_orchestrator_agent_metrics([
        _event("workflow.operation.requested", "request", 0, {
            "operation_id": "op", "operation_type": "orchestrator_agent_semantic",
        }),
        _event("agent.usage", "usage", 1, {
            "operation_id": "op", "model": "default", "total_cost_usd": 0,
            "usage": {"input_tokens": 1000, "output_tokens": 100},
        }),
    ])
    assert result["summary"]["cost_usd"] == 0
    assert result["summary"]["unpriced_sample_count"] == 0


def test_metrics_projects_pending_and_terminal_sla_breaches() -> None:
    events = [
        ZfEvent(
            type="workflow.operation.requested",
            id="evt-pending",
            ts="2026-08-12T12:00:00+00:00",
            payload={
                "operation_id": "op-pending",
                "operation_type": "orchestrator_agent_semantic",
                "workflow_run_id": "run-1",
                "parent_stage_id": "oa-plan_candidate",
            },
        ),
        ZfEvent(
            type="workflow.operation.requested",
            id="evt-terminal-request",
            ts="2026-08-12T12:00:00+00:00",
            payload={
                "operation_id": "op-terminal",
                "operation_type": "orchestrator_agent_semantic",
                "workflow_run_id": "run-1",
                "parent_stage_id": "oa-semantic_failure",
            },
        ),
        ZfEvent(
            type="workflow.operation.settled",
            id="evt-terminal-settled",
            ts="2026-08-12T12:04:00+00:00",
            payload={"operation_id": "op-terminal"},
        ),
    ]

    metrics = build_orchestrator_agent_metrics(
        events,
        observed_at="2026-08-12T12:06:00+00:00",
    )
    rows = {row["operation_id"]: row for row in metrics["operations"]}

    assert rows["op-pending"]["age_seconds"] == 360.0
    assert rows["op-pending"]["sla_breached"] is True
    assert rows["op-terminal"]["latency_seconds"] == 240.0
    assert rows["op-terminal"]["sla_threshold_seconds"] == 180
    assert rows["op-terminal"]["sla_breached"] is True
    assert metrics["summary"]["sla_breach_count"] == 2
    assert metrics["summary"]["pending_sla_breach_count"] == 1
