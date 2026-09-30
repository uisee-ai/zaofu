"""Level-triggered Goal completion and two-phase delivery runtime caller."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


_CLAIM_CAUSES = frozenset({
    "goal.closure.synthesized",
    "judge.passed",
    "artifact.delivery.verified",
})
_GATE_TRUTH_DELTAS = frozenset({
    "goal.closure.synthesized",
    "artifact.delivery.verified",
    "run.goal.completion.claimed",
    "workflow.operation.settled",
    "workflow.operation.failed",
    "workflow.operation.blocked",
    "rework.feedback.verified_closed",
    "rework.feedback.residual",
    "attempt.handoff.acknowledged",
    "attempt.handoff.closed",
    "human.decision.resolved",
    "run.manager.human_decision.applied",
    "run.manager.action.applied",
    "run.manager.action.failed",
    "run.manager.action.effect.pending",
    "run.manager.action.effect.passed",
    "run.manager.action.effect.failed",
    "run.manager.tick.completed",
    "fanout.started",
    "fanout.aggregate.completed",
    "fanout.cancelled",
    "fanout.timed_out",
    "task.done",
    "verify.passed",
    "test.passed",
    "review.approved",
    "lane.stage.completed",
    "candidate.ready",
    "candidate.integration.completed",
    "ship.completed",
    "ship.failed",
    "ship.blocked",
    "ship.conflict",
    "run.delivery.settled",
    "run.delivery.failed",
    "run.delivery.blocked",
})


def maybe_complete_run_goal(runtime: Any, event: ZfEvent) -> None:
    """Create/re-evaluate durable completion claims after one truth delta."""

    if not getattr(getattr(runtime.config, "goal", None), "enabled", False):
        return
    if event.type not in _CLAIM_CAUSES | _GATE_TRUTH_DELTAS:
        return
    if (
        event.type == "judge.passed"
        and isinstance(event.payload, dict)
        and str(event.payload.get("authority") or "") == "compat_projection"
    ):
        return
    try:
        from zf.runtime.event_window import read_runtime_events
        from zf.runtime.run_manager import run_goal_completion_claim_event

        events = list(read_runtime_events(runtime.event_log, runtime.state_dir))
        if event.type in _CLAIM_CAUSES:
            if not _pre_closeout_allows_claim(runtime, event, events):
                return
            claim = run_goal_completion_claim_event(events, cause=event)
            if claim is not None:
                runtime.event_writer.append(claim)
                events = list(read_runtime_events(runtime.event_log, runtime.state_dir))
        _evaluate_active_claims(runtime, events)
    except Exception:
        # Fail closed. Supervisor/Run Manager observe a missing Goal terminal;
        # no exception may manufacture a completion event.
        return


def reconcile_run_goal_completion(runtime: Any) -> None:
    """Recover a completion claim lost between evidence admission and append."""

    if not getattr(getattr(runtime.config, "goal", None), "enabled", False):
        return
    from zf.runtime.event_window import read_runtime_events
    from zf.runtime.run_manager import run_goal_completion_claim_event
    from zf.runtime.run_scope import resolve_run_for_completion_cause

    events = list(read_runtime_events(runtime.event_log, runtime.state_dir))
    _evaluate_active_claims(runtime, events)
    events = list(read_runtime_events(runtime.event_log, runtime.state_dir))

    seen_run_ids: set[str] = set()
    for cause in reversed(events):
        if cause.type not in _CLAIM_CAUSES:
            continue
        if (
            cause.type == "judge.passed"
            and isinstance(cause.payload, dict)
            and str(cause.payload.get("authority") or "")
            == "compat_projection"
        ):
            continue
        run_id = resolve_run_for_completion_cause(events, cause)
        if not run_id or run_id in seen_run_ids:
            continue
        seen_run_ids.add(run_id)
        if not _pre_closeout_allows_claim(runtime, cause, events):
            continue
        claim = run_goal_completion_claim_event(events, cause=cause)
        if claim is None:
            continue
        appended = runtime.event_writer.append(claim)
        events.append(appended)
        _evaluate_active_claims(runtime, events)
        events = list(read_runtime_events(runtime.event_log, runtime.state_dir))


def _pre_closeout_allows_claim(
    runtime: Any,
    cause: ZfEvent,
    events: list[ZfEvent],
) -> bool:
    from zf.runtime.orchestrator_agent_aggregation import (
        pre_closeout_checkpoint_state,
    )

    state = pre_closeout_checkpoint_state(runtime, cause, events=events)
    return not state.enabled or not state.blocking or state.satisfied


def _evaluate_active_claims(runtime: Any, events: list[ZfEvent]) -> None:
    from zf.runtime.artifact_delivery_result import (
        ArtifactDeliveryResultError,
        load_bound_run_contract,
    )
    from zf.runtime.run_contract import load_run_contract
    from zf.runtime.run_manager import (
        RUN_GOAL_COMPLETION_CLAIMED,
        RUN_GOAL_COMPLETION_REJECTED,
        run_goal_completion_gate_event,
    )

    terminals = {
        str((event.payload or {}).get("claim_id") or "")
        for event in events
        if event.type in {"run.goal.completed", RUN_GOAL_COMPLETION_REJECTED}
        and isinstance(event.payload, dict)
    }
    claims: dict[str, ZfEvent] = {}
    for candidate in events:
        if candidate.type != RUN_GOAL_COMPLETION_CLAIMED:
            continue
        body = candidate.payload if isinstance(candidate.payload, dict) else {}
        claim_id = str(body.get("claim_id") or candidate.id)
        if claim_id not in terminals:
            claims[claim_id] = candidate
    for claim in claims.values():
        from zf.core.workflow.flow_metadata import flow_metadata_for

        body = claim.payload
        if body.get("run_contract_ref") or body.get("run_contract_digest"):
            try:
                contract = load_bound_run_contract(runtime.state_dir, body)
            except ArtifactDeliveryResultError:
                # A broken pinned binding must never fall back to another Run.
                continue
        else:
            contract = load_run_contract(runtime.state_dir) or {}
        protocols = contract.get("protocols") if isinstance(contract.get("protocols"), dict) else {}
        operation = protocols.get("workflow_operation") if isinstance(protocols.get("workflow_operation"), dict) else {}
        required_operation_ids = [
            str(item)
            for item in operation.get("required_operation_ids", [])
            if str(item).strip()
        ]
        goal_protocol = protocols.get("goal_closure") if isinstance(protocols.get("goal_closure"), dict) else {}
        metadata = flow_metadata_for(runtime.config, payload=claim.payload)
        delivery_policy = str(
            goal_protocol.get("delivery_policy")
            or metadata.get("delivery_policy")
            or "report_only"
        )
        outcome = run_goal_completion_gate_event(
            events,
            claim=claim,
            required_operation_ids=required_operation_ids,
            delivery_policy=delivery_policy,
            run_contract=contract,
            state_dir=runtime.state_dir,
        )
        if outcome is None:
            continue
        appended = runtime.event_writer.append(outcome)
        events.append(appended)
        if appended.type == "run.delivery.requested":
            _apply_delivery_request(runtime, appended)


def _apply_delivery_request(runtime: Any, request: ZfEvent) -> None:
    payload = request.payload if isinstance(request.payload, dict) else {}
    claim_id = str(payload.get("claim_id") or "")
    operation_id = str(payload.get("delivery_operation_id") or "")
    candidate_ref = str(payload.get("candidate_ref") or "")
    target_commit = str(payload.get("target_commit") or "")
    if candidate_ref == target_commit:
        from zf.runtime.goal_closure_bridge import _resolve_candidate_ref

        candidate_ref = _resolve_candidate_ref(
            list(runtime.event_log.read_all()),
            workflow_run_id=str(payload.get("run_id") or request.correlation_id or ""),
            target_commit=target_commit,
            payload={},
        )
    run_id = str(payload.get("run_id") or request.correlation_id or "")
    result_payload = {
        "run_id": run_id,
        "workflow_run_id": run_id,
        "goal_id": str(payload.get("goal_id") or ""),
        "claim_id": claim_id,
        "delivery_operation_id": operation_id,
        "candidate_ref": candidate_ref,
        "target_commit": target_commit,
    }
    try:
        git_config = getattr(runtime.config.runtime, "git", None)
        if git_config is None or not candidate_ref:
            runtime.event_writer.append(ZfEvent(
                type="run.delivery.blocked",
                actor="zf-cli",
                causation_id=request.id,
                correlation_id=run_id,
                payload={
                    **result_payload,
                    "reason": "delivery policy requires a configured candidate ref",
                },
            ))
            return
        from zf.runtime.ship import ShipService

        result = ShipService(
            state_dir=runtime.state_dir,
            project_root=runtime.project_root,
            config=runtime.config,
            event_log=runtime.event_log,
        ).ship(
            target_ref=candidate_ref,
            pdd_id=str(
                payload.get("pdd_id")
                or payload.get("feature_id")
                or payload.get("goal_id")
                or ""
            ),
            event_writer=runtime.event_writer,
            causation_id=request.id,
            correlation_id=run_id,
        )
        runtime.event_writer.append(ZfEvent(
            type="run.delivery.settled" if result.ok else "run.delivery.failed",
            actor="zf-cli",
            causation_id=request.id,
            correlation_id=run_id,
            payload={
                **result_payload,
                "ship_event_type": result.event_type,
                "ship_status": result.status,
                "ship_result": dict(result.payload or {}),
                "reason": "delivery operation settled" if result.ok else "delivery operation failed",
            },
        ))
    except Exception as exc:
        runtime.event_writer.append(ZfEvent(
            type="run.delivery.failed",
            actor="zf-cli",
            causation_id=request.id,
            correlation_id=run_id,
            payload={
                **result_payload,
                "reason": f"{type(exc).__name__}: {exc}",
            },
        ))


__all__ = [
    "maybe_complete_run_goal",
    "reconcile_run_goal_completion",
]
