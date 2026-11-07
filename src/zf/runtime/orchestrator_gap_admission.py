"""Causal ownership of a reader's rejected gap proposal."""
from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent


def _explicit_run(event: ZfEvent) -> str:
    return str(event.payload.get("workflow_run_id") or event.payload.get("run_id") or "")


def _conflicting_run(event: ZfEvent, gap: ZfEvent) -> bool:
    # Legacy amend failures carry a child trace, not the canonical Run. Their
    # exact source_event_id establishes lineage; an explicit conflict still fails.
    return bool(_explicit_run(event) and _explicit_run(gap)
                and _explicit_run(event) != _explicit_run(gap))


def rejected_gap_admission(
    events: list[ZfEvent], gap: ZfEvent,
) -> dict[str, Any] | None:
    """Return repair refs only for an unsettled, explicitly linked rejection.

    A pending/successful amendment still owns recovery. An explicit later
    planner handoff or admission of a successor package transfers ownership;
    an unrelated busy planner does not.
    """
    gap_index = next((i for i, event in enumerate(events) if event.id == gap.id), -1)
    if gap_index < 0:
        return None
    rejection: ZfEvent | None = None
    rejection_index = -1
    for index, event in enumerate(events[gap_index + 1:], start=gap_index + 1):
        if _conflicting_run(event, gap):
            continue
        if str(event.payload.get("source_event_id") or "") != gap.id:
            continue
        if event.type == "task_map.amended":
            return None
        if event.type == "task_map.amend.failed":
            rejection, rejection_index = event, index
    if rejection is None:
        return None
    causal_ids = {gap.id, rejection.id, str(gap.payload.get("source_event_id") or "")}
    causal_ids.discard("")
    for event in events[rejection_index + 1:]:
        if _conflicting_run(event, gap):
            continue
        body = event.payload
        if event.type == "orchestrator.replan_requested":
            summary = body.get("rework_summary")
            summary = summary if isinstance(summary, dict) else {}
            sources = summary.get("source_event_ids")
            sources = sources if isinstance(sources, list) else []
            refs = {str(body.get("rework_of") or ""), str(body.get("source_event_id") or ""),
                    *(str(value) for value in sources)}
            if causal_ids & refs:
                return None
        if event.type == "plan.artifact_package.admitted" and (
            str(body.get("source_event_id") or "") in causal_ids
            or str(event.causation_id or "") in causal_ids
        ):
            return None
    return {
        "gap_event_id": gap.id,
        "gap_event_ref": f"event:{gap.id}",
        "gap_plan_ref": str(gap.payload.get("gap_plan_ref") or ""),
        "rejection_event_id": rejection.id,
        "rejection_event_ref": f"event:{rejection.id}",
        "reason": str(rejection.payload.get("reason") or "task_map amendment rejected"),
    }


def reader_failure_for_gap_rejection(
    events: list[ZfEvent], rejection: ZfEvent,
) -> ZfEvent | None:
    """Resolve the explicit rejection -> gap -> reader failure chain."""
    if rejection.type != "task_map.amend.failed":
        return None
    by_id = {event.id: event for event in events}
    gap = by_id.get(str(rejection.payload.get("source_event_id") or ""))
    if gap is None or gap.type not in {"gap_plan.ready", "flow.gap_plan.ready", "goal.gap_plan.ready"}:
        return None
    repair = rejected_gap_admission(events, gap)
    if repair is None or repair["rejection_event_id"] != rejection.id:
        return None
    source_id = str(gap.payload.get("source_event_id") or gap.causation_id or "")
    if gap.causation_id and gap.causation_id != source_id:
        return None
    failure = by_id.get(source_id)
    if failure is None or _conflicting_run(failure, gap) or _conflicting_run(failure, rejection):
        return None
    return failure


class GapAdmissionRecoveryMixin:
    """Wire the admission result into the existing bounded reader handler."""

    def _on_gap_admission_failed(self, event: ZfEvent):
        from zf.runtime.event_window import read_runtime_events

        failure = reader_failure_for_gap_rejection(
            read_runtime_events(self.event_log, self.state_dir), event,
        )
        if failure is not None:
            return self._on_reader_stage_failure_replan(failure)
        return None

