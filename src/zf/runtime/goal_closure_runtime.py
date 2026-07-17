"""Semantic routing for admitted Thin Judge Goal-closure results."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.goal_closure_result import (
    GoalClosureResultError,
    validate_goal_closure_result,
)


GOAL_CLOSURE_SYNTHESIZED = "goal.closure.synthesized"


def process_goal_closure_result(runtime: Any, event: ZfEvent) -> None:
    """Route one admitted result; never infer product semantics in Kernel."""

    if event.type != GOAL_CLOSURE_SYNTHESIZED:
        return
    payload = event.payload if isinstance(event.payload, dict) else {}
    result = payload.get("goal_closure_result")
    if not isinstance(result, dict):
        return
    try:
        validate_goal_closure_result(result)
    except GoalClosureResultError:
        return
    envelope_ref = payload.get("admitted_call_result_ref")
    if not isinstance(envelope_ref, dict) or not str(envelope_ref.get("ref") or ""):
        return
    events = runtime.event_log.read_all()
    if any(
        existing.type == "goal.closure.compat.projected"
        and isinstance(existing.payload, dict)
        and str(existing.payload.get("source_event_id") or "") == event.id
        for existing in events
    ):
        return

    verdict = str(result.get("verdict") or "")
    common = {
        "workflow_run_id": str(result.get("workflow_run_id") or ""),
        "run_id": str(result.get("workflow_run_id") or ""),
        "goal_id": str(result.get("goal_id") or ""),
        "pdd_id": str(
            payload.get("pdd_id") or payload.get("goal_id")
            or result.get("goal_id") or ""
        ),
        "feature_id": str(
            payload.get("feature_id") or payload.get("pdd_id")
            or payload.get("goal_id") or result.get("goal_id") or ""
        ),
        "flow_kind": str(result.get("flow_kind") or ""),
        "task_map_generation": str(result.get("task_map_generation") or ""),
        "target_commit": str(result.get("target_commit") or ""),
        "candidate_head_commit": str(result.get("target_commit") or ""),
        "candidate_ref": str(result.get("candidate_ref") or ""),
        "goal_claim_set_ref": str(result.get("goal_claim_set_ref") or ""),
        "goal_claim_set_digest": str(result.get("goal_claim_set_digest") or ""),
        "closure_fact_ref": str(result.get("closure_fact_ref") or ""),
        "closure_fact_digest": str(result.get("closure_fact_digest") or ""),
        "admitted_call_result_ref": dict(envelope_ref),
        "control_result_ref": dict(payload.get("control_result_ref") or {})
        if isinstance(payload.get("control_result_ref"), dict) else {},
        "operation_id": str(payload.get("operation_id") or ""),
        "goal_closure_result": dict(result),
        "source_event_id": event.id,
        "summary": str(result.get("summary") or ""),
    }
    if verdict == "passed":
        from zf.runtime.goal_completion_gate import maybe_complete_run_goal

        maybe_complete_run_goal(runtime, event)
        compat_type = "judge.passed"
    elif verdict == "rejected":
        rejected = runtime.event_writer.append(ZfEvent(
            type="goal.closure.rejected",
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=event.correlation_id or common["workflow_run_id"],
            payload={
                **common,
                "recommended_action": str(result.get("recommended_action") or ""),
                "open_gap_refs": list(result.get("open_gap_refs") or []),
                "findings": [
                    {"message": str(ref), "gap_ref": str(ref)}
                    for ref in result.get("open_gap_refs") or []
                ],
                "reason": "Thin Judge found unresolved Goal claims",
            },
        ))
        _route_rejected(runtime, rejected, result=result)
        compat_type = "judge.failed"
    else:
        blocked = runtime.event_writer.append(ZfEvent(
            type="goal.closure.blocked",
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=event.correlation_id or common["workflow_run_id"],
            payload={
                **common,
                "recommended_action": str(result.get("recommended_action") or "hold"),
                "open_gap_refs": list(result.get("open_gap_refs") or []),
                "reason": "Thin Judge requires an external decision or input",
            },
        ))
        runtime.event_writer.append(ZfEvent(
            type="runtime.attention.needed",
            actor="zf-cli",
            causation_id=blocked.id,
            correlation_id=event.correlation_id or common["workflow_run_id"],
            payload={
                **common,
                "failure_class": "goal_closure_blocked",
                "owner_route": "run_manager",
                "human_action_required": str(result.get("recommended_action") or "") == "human",
                "reason": "admitted Goal closure result is blocked",
            },
        ))
        compat_type = "judge.failed"

    compat = runtime.event_writer.append(ZfEvent(
        type=compat_type,
        actor="zf-cli",
        causation_id=event.id,
        correlation_id=event.correlation_id or common["workflow_run_id"],
        payload={
            **common,
            "authority": "compat_projection",
            "fanout_id": str(payload.get("fanout_id") or ""),
            "stage_id": str(payload.get("stage_id") or "goal-closure"),
            "status": "completed" if verdict == "passed" else "failed",
            "target_ref": str(result.get("candidate_ref") or result.get("target_commit") or ""),
            "evidence_refs": list(result.get("input_result_refs") or []),
            "verdict": verdict,
            "reason": "compatibility projection of admitted Goal closure result",
        },
    ))
    runtime.event_writer.append(ZfEvent(
        type="goal.closure.compat.projected",
        actor="zf-cli",
        causation_id=compat.id,
        correlation_id=event.correlation_id or common["workflow_run_id"],
        payload={
            **common,
            "compat_event_id": compat.id,
            "compat_event_type": compat_type,
        },
    ))


def _route_rejected(runtime: Any, event: ZfEvent, *, result: dict) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    action = str(result.get("recommended_action") or "replan")
    flow_kind = str(result.get("flow_kind") or "")
    if action == "candidate_verify":
        event_type = (
            "verify.parity_scan.requested"
            if flow_kind == "refactor"
            else "flow.discovery.requested"
        )
        runtime.event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            causation_id=event.id,
            correlation_id=event.correlation_id,
            payload={
                **payload,
                "source": "goal_closure_semantic_router",
                "reason": "Thin Judge requested fresh candidate-level verification",
            },
        ))
        return
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.replan_requested",
        actor="zf-cli",
        causation_id=event.id,
        correlation_id=event.correlation_id,
        payload={
            **payload,
            "source": "goal_closure_semantic_router",
            "replan_scope": "goal_gap",
            "rework_of": event.id,
            "reason": "Thin Judge Goal gaps require planner/synth recovery",
        },
    ))


__all__ = ["GOAL_CLOSURE_SYNTHESIZED", "process_goal_closure_result"]
