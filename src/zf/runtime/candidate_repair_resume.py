"""Retire old checkpoint ownership after an exact authorized Reader repair starts."""
from typing import TYPE_CHECKING

from zf.core.events.model import ZfEvent

if TYPE_CHECKING:
    from zf.runtime.workflow_resume import WorkflowBatchResumeCheckpoint


def candidate_repair_supersedes(
    events: list[ZfEvent], checkpoint: "WorkflowBatchResumeCheckpoint",
) -> str:
    if not all((checkpoint.workflow_run_id, checkpoint.stage_id, checkpoint.fanout_id,
                checkpoint.candidate_head_commit, checkpoint.candidate_ref, checkpoint.freeze_id)):
        return ""
    source = next((i for i, e in enumerate(events) if e.id == checkpoint.source_event_id), -1)
    if source < 0:
        return ""
    def same_run(event: ZfEvent) -> bool:
        return (event.payload.get("workflow_run_id") or event.payload.get("run_id")
                or event.correlation_id) == checkpoint.workflow_run_id

    failures = {e.id for e in events[source:] if same_run(e)
                and e.payload.get("fanout_id") == checkpoint.fanout_id
                and e.payload.get("stage_id") == checkpoint.stage_id
                and (e.type.endswith(".failed") or e.id == checkpoint.source_event_id)}
    repairs = set()
    for event in events[source + 1:]:
        body = event.payload
        if not same_run(event):
            continue
        if (event.type == "candidate.ready"
                and body.get("repaired_failure_event_id") in failures
                and body.get("operator_authorized") is True
                and body.get("operator_authorization_ref")
                and all(body.get(key) == getattr(checkpoint, key) for key in (
                    "candidate_head_commit", "candidate_ref", "freeze_id"
                ))):
            repairs.add(event.id)
        if (event.type == "fanout.started" and body.get("trigger_event_id") in repairs
                and body.get("stage_id") == checkpoint.stage_id):
            return f"stale batch checkpoint superseded by authorized reader repair {event.id}"
    return ""
