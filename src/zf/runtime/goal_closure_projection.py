"""Read-only Goal closure lifecycle projection for delivery traces."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent


EventSlice = Sequence[tuple[int, ZfEvent]]


def build_goal_closure_loop(
    module_parity_loop: dict[str, Any],
    *,
    events: EventSlice,
    feature_id: str,
) -> dict[str, Any]:
    """Overlay Thin Judge, Completion Gate, and delivery lifecycle facts."""

    loop = dict(module_parity_loop)
    lifecycle_types = {
        "flow.goal.closed",
        "module.parity.closed",
        "workflow.call.result.admitted",
        "goal.closure.synthesized",
        "goal.closure.rejected",
        "goal.closure.blocked",
        "run.goal.completion.claimed",
        "run.goal.completion.blocked",
        "run.goal.completion.rejected",
        "run.delivery.requested",
        "run.delivery.settled",
        "run.delivery.failed",
        "run.delivery.blocked",
        "run.goal.completed",
    }
    linked_run_ids = {
        _run_id(event)
        for _seq, event in events
        if event.type in lifecycle_types
        and _feature_id(event) == feature_id
        and _run_id(event)
    }
    rows: list[dict[str, Any]] = []
    status = str(loop.get("status") or "idle")
    for seq, event in events:
        if event.type not in lifecycle_types:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        payload_feature = str(
            payload.get("feature_id")
            or payload.get("pdd_id")
            or payload.get("goal_id")
            or ""
        )
        if feature_id and payload_feature and payload_feature != feature_id:
            continue
        event_run_id = _run_id(event)
        if (
            feature_id
            and not payload_feature
            and linked_run_ids
            and event_run_id
            and event_run_id not in linked_run_ids
        ):
            continue
        if event.type == "workflow.call.result.admitted" and str(
            payload.get("control_result_schema") or ""
        ) != "goal-closure-result.v1":
            continue
        verdict = str(
            (payload.get("goal_closure_result") or {}).get("verdict")
            if isinstance(payload.get("goal_closure_result"), dict)
            else payload.get("verdict") or ""
        )
        rows.append({
            "seq": seq,
            "event_id": event.id,
            "event_type": event.type,
            "workflow_run_id": event_run_id,
            "goal_id": str(payload.get("goal_id") or ""),
            "claim_id": str(payload.get("claim_id") or ""),
            "operation_id": str(
                payload.get("operation_id")
                or payload.get("delivery_operation_id")
                or ""
            ),
            "status": str(payload.get("status") or ""),
            "verdict": verdict,
            "reason": str(payload.get("reason") or ""),
            "target_commit": str(payload.get("target_commit") or ""),
            "admitted_call_result_ref": dict(
                payload.get("admitted_call_result_ref") or {}
            ) if isinstance(payload.get("admitted_call_result_ref"), dict) else {},
        })
        status = {
            "flow.goal.closed": "execution_completed",
            "module.parity.closed": "execution_completed",
            "workflow.call.result.admitted": "result_admitted",
            "goal.closure.synthesized": "judge_synthesized",
            "goal.closure.rejected": "semantic_rejected",
            "goal.closure.blocked": "semantic_blocked",
            "run.goal.completion.claimed": "completion_claimed",
            "run.goal.completion.blocked": "completion_blocked",
            "run.goal.completion.rejected": "completion_rejected",
            "run.delivery.requested": "delivery_requested",
            "run.delivery.settled": "delivery_settled",
            "run.delivery.failed": "delivery_failed",
            "run.delivery.blocked": "delivery_blocked",
            "run.goal.completed": "goal_completed",
        }[event.type]
    loop["schema_version"] = "goal-closure-loop.v2"
    loop["compatibility_projection"] = "module_parity_loop"
    loop["status"] = status
    loop["lifecycle"] = rows[-40:]
    loop["lifecycle_count"] = len(rows)
    loop["completion_event_id"] = next(
        (
            row["event_id"]
            for row in reversed(rows)
            if row["event_type"] == "run.goal.completed"
        ),
        "",
    )
    return loop


def _feature_id(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    result = (
        payload.get("goal_closure_result")
        if isinstance(payload.get("goal_closure_result"), dict)
        else {}
    )
    return str(
        payload.get("feature_id")
        or payload.get("pdd_id")
        or payload.get("goal_id")
        or result.get("goal_id")
        or ""
    )


def _run_id(event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    result = (
        payload.get("goal_closure_result")
        if isinstance(payload.get("goal_closure_result"), dict)
        else {}
    )
    return str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or result.get("workflow_run_id")
        or event.correlation_id
        or ""
    )


__all__ = ["build_goal_closure_loop"]
