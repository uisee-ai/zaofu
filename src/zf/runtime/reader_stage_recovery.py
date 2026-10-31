"""Explicit infrastructure repair retries for configured reader stages."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.failure_kind import FAILURE_KIND_INFRA, failure_kind_from_payload
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.stage_failure_replan import (
    _event_run_id, plan_reader_stage_replan, reader_stage_for_failure,
)


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
    if failure is None or failure_kind_from_payload(failure.payload) != FAILURE_KIND_INFRA:
        raise ValueError("reader repair retry requires an infrastructure failure")
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
    if not starts or not fanout_id or starts[-1].payload.get("fanout_id") != fanout_id:
        raise ValueError("reader failure has been superseded or has no causal fanout")
    index = events.index(failure)
    if any(e.payload.get("repaired_failure_event_id") == failure_id
           and e.type == stage.trigger for e in events):
        raise ValueError("reader failure has already been retried")
    if any(e.payload.get("fanout_id") == fanout_id and e.type in {
        "fanout.child.dispatched", "fanout.synth.dispatched", "fanout.aggregate.completed",
    } for e in events[index + 1:]):
        raise ValueError("reader failure has a newer dispatch or aggregate outcome")
    trigger, reason = plan_reader_stage_replan(
        config, events, failure, authorized_generation=f"repair-{requested.id}",
        generation_source_event_id=failure.id, authorization_event_id=requested.id,
        authorization_ref=approval_ref,
    )
    if trigger is None:
        raise ValueError(f"reader repair retry rejected: {reason}")
    from zf.runtime.plan_requirement_amendment import amendment_binding
    trigger.payload.update(amendment_binding(state_dir, run_id, events=events))
    trigger.payload.update(repaired_failure_event_id=failure.id,
                           repair_reason=str(payload["reason"]))
    return trigger
