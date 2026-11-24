"""Explicit infrastructure repair retries for configured reader stages."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.failure_kind import FAILURE_KIND_INFRA
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.stage_failure_replan import (
    _event_run_id, plan_reader_stage_replan, reader_stage_for_failure,
)


SUPERSEDED_FANOUT_REASON = "reader failure has been superseded or has no causal fanout"


def _candidate_fanout_is_current(
    events: list[ZfEvent], run_id: str, start: ZfEvent, positions: dict[str, int],
) -> bool | None:
    """None means the causal freeze is unknown and cannot release a blocked retry."""
    from zf.runtime.candidate_freeze_recovery_identity import native_freeze_for_recovery, superseding_reader_freeze
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload

    at = positions.get(start.payload.get("trigger_event_id"))
    if start.origin != "kernel" or at is None or at >= positions[start.id]:
        return None
    source = events[at]
    body = source.payload
    freeze = body.get("freeze_id")
    head = body.get("candidate_head_commit")
    if (source.type != "candidate.ready" or source.origin != "kernel" or _event_run_id(source) != run_id
            or not freeze or not head):
        return None
    try:
        # A stale repair wrapper may still identify the older native receipt it actually dispatched.
        # Never infer that receipt by matching only its mutable branch name or target SHA.
        if source.actor == "zf-stall-redispatch":
            native = native_freeze_for_recovery(events[:at + 1], source)
        else:
            parent = body.get("candidate_snapshot_event_id") or body.get("candidate_event_id") or source.id
            native = frozen_candidate_for_payload(
                events[:at + 1], run_id, authority_events=events,
                payload={"candidate_event_id": parent, "freeze_id": freeze},
            )
    except ValueError:
        return None
    if (not native or native.origin != "kernel" or native.payload.get("freeze_id") != freeze
            or native.payload.get("candidate_head_commit") != head):
        return None
    return superseding_reader_freeze(reversed(events), run_id, freeze, head) is None


def repaired_reader_stage_trigger(
    state_dir: Path,
    config: ZfConfig,
    events: list[ZfEvent],
    payload: dict[str, Any],
    requested: ZfEvent,
) -> ZfEvent:
    failure_id = str(payload.get("repaired_failure_event_id") or "")
    approval_ref = str(payload.get("approval_ref") or "").strip()
    if not approval_ref or not str(payload.get("reason") or "").strip():
        raise ValueError("reader repair retry requires approval_ref and repair reason")
    failure = next((e for e in events if e.id == failure_id), None)
    from zf.runtime.reader_failure_repair import repaired_failure_kind
    semantic_recovery = payload.get("orchestrator_decision_event_id")
    if semantic_recovery:
        from zf.runtime.candidate_verification_recovery import validate_recovery

        validate_recovery(state_dir, config, events, payload)
    elif failure is None or repaired_failure_kind(state_dir, events, failure) != FAILURE_KIND_INFRA:
        raise ValueError("reader repair retry requires an infrastructure failure")
    if failure is None:
        raise ValueError("reader repair failure missing")
    stage, reason = reader_stage_for_failure(config, events, failure)
    if stage is None:
        raise ValueError(reason)
    run_id = _event_run_id(failure)
    entry = build_run_admission_projection(events).runs.get(run_id)
    if entry is None or entry.status != "running":
        raise ValueError("reader repair retry requires a running admitted Run")
    starts = [e for e in events if e.type == "fanout.started"
              and _event_run_id(e) == run_id and e.payload.get("stage_id") == stage.id]
    fanout_id = str(failure.payload.get("fanout_id") or "")
    index = events.index(failure)
    matches = [i for i, start in enumerate(starts) if fanout_id and start.payload.get("fanout_id") == fanout_id]
    if len(matches) != 1 or events.index(starts[matches[0]]) >= index:
        raise ValueError(SUPERSEDED_FANOUT_REASON)
    newer = starts[matches[0] + 1:]
    if newer:
        positions = {event.id: i for i, event in enumerate(events)}
        if (stage.trigger != "candidate.ready" or failure.origin != "kernel"
                or _candidate_fanout_is_current(events, run_id, starts[matches[0]], positions) is not True
                or any(_candidate_fanout_is_current(events, run_id, start, positions) is not False
                       for start in newer)):
            raise ValueError(SUPERSEDED_FANOUT_REASON)
    if any(e.payload.get("repaired_failure_event_id") == failure_id
           and e.type == stage.trigger for e in events):
        raise ValueError("reader failure has already been retried")
    if any(e.payload.get("fanout_id") == fanout_id and e.type in {
        "fanout.child.dispatched", "fanout.synth.dispatched", "fanout.aggregate.completed",
    } for e in events[index + 1:]):
        raise ValueError("reader failure has a newer dispatch or aggregate outcome")
    trigger, reason = plan_reader_stage_replan(
        config, events, failure, state_dir=state_dir, authorized_generation=f"repair-{requested.id}",
        generation_source_event_id=failure.id, authorization_event_id=requested.id,
        authorization_ref=approval_ref,
    )
    if trigger is None:
        raise ValueError(f"reader repair retry rejected: {reason}")
    from zf.runtime.plan_requirement_amendment import amendment_binding
    from zf.runtime.reader_replan_feedback import bind_operator_feedback, operator_feedback
    trigger.payload.update(amendment_binding(state_dir, run_id, events=events))
    bind_operator_feedback(trigger, operator_feedback(payload), request_id=requested.id)
    trigger.payload.update(repaired_failure_event_id=failure.id,
                           repair_reason=str(payload["reason"]))
    if semantic_recovery:
        trigger.payload['candidate_verification_recovery_key'] = payload['candidate_verification_recovery_key']
        trigger.payload['orchestrator_decision_event_id'] = semantic_recovery
    if trigger.type == 'candidate.ready':
        from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
        native = frozen_candidate_for_payload(events, run_id, payload=trigger.payload)
        if native is None:
            raise ValueError('Candidate Reader repair requires its exact frozen source')
        trigger.payload['candidate_snapshot_event_id'] = native.id
        trigger.payload.pop('schema_version', None)
    from zf.runtime.candidate_media_review import KEY, validate_owner_review
    trigger.payload.pop(KEY, None)
    if KEY in payload:
        if trigger.type != 'candidate.ready' or semantic_recovery:
            raise ValueError('Candidate media review requires an operator Candidate Reader recovery')
        trigger.payload[KEY] = validate_owner_review(state_dir, events, requested, payload, failure, native)
    return trigger
