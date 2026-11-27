"""Recover the integration receipt that handed a writer candidate to per-task readers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from zf.core.events.model import ZfEvent

if TYPE_CHECKING:
    from zf.runtime.workflow_resume import WorkflowBatchResumeCheckpoint


def writer_candidate_source(events: list[ZfEvent], checkpoint: WorkflowBatchResumeCheckpoint) -> ZfEvent | None:
    if (checkpoint.source_event_type != "fanout.aggregate.completed"
            or checkpoint.freeze_id or not checkpoint.candidate_snapshot_event_id):
        return None
    positions = {event.id: i for i, event in enumerate(events)}
    by_id = {event.id: event for event in events}
    source = by_id.get(checkpoint.candidate_snapshot_event_id)
    if not source or not source.payload.get("integration_attempt_id"):
        return None
    pinned = source
    # A later retry can name the preceding, audited replay. Resolve its original producer.
    if source.payload.get("source") == "workflow_resume_batch" and source.payload.get("candidate_event_id"):
        parent = by_id.get(source.payload["candidate_event_id"])
        if source.origin != "kernel" or not parent or parent.payload.get("source") == "workflow_resume_batch":
            raise ValueError("invalid writer candidate replay lineage")
        source = parent
    body = source.payload
    producer = by_id.get(source.causation_id)
    if (source.type != "candidate.ready" or source.origin != "kernel"
            or body.get("freeze_id") or body.get("schema_version") == "candidate-freeze-receipt.v1"
            or not producer or producer.type != "fanout.aggregate.completed" or producer.origin != "kernel"
            or producer.payload.get("status") != "completed"):
        raise ValueError("writer candidate has no completed producer receipt")
    for key in ("workflow_run_id", "fanout_id", "integration_attempt_id", "task_map_generation",
                "candidate_head_commit", "candidate_ref", "completed_task_ids"):
        if not body.get(key) or producer.payload.get(key) != body[key]:
            raise ValueError(f"writer candidate producer identity mismatch: {key}")
    for key in ("workflow_run_id", "candidate_head_commit", "candidate_base_commit",
                "candidate_ref", "task_map_ref"):
        if not body.get(key) or body[key] != getattr(checkpoint, key):
            raise ValueError(f"writer candidate checkpoint identity mismatch: {key}")
    if set(body["completed_task_ids"]) != set(checkpoint.completed_task_ids):
        raise ValueError("writer candidate checkpoint identity mismatch: completed_task_ids")
    failure = by_id.get(checkpoint.source_event_id)
    if (not failure or failure.origin != "kernel" or failure.type != checkpoint.source_event_type
            or failure.payload.get("status") not in {"failed", "timed_out", "cancelled"}
            or failure.payload.get("fanout_id") != checkpoint.fanout_id
            or failure.payload.get("stage_id") != checkpoint.stage_id
            or (failure.payload.get("workflow_run_id") or failure.correlation_id) != checkpoint.workflow_run_id):
        raise ValueError("failed reader is outside the writer candidate checkpoint")
    starts = [e for e in events[:positions[failure.id]] if e.type == "fanout.started" and e.origin == "kernel"
              and e.payload.get("fanout_id") == checkpoint.fanout_id
              and e.payload.get("stage_id") == checkpoint.stage_id
              and e.payload.get("workflow_run_id") == checkpoint.workflow_run_id]
    trigger = by_id.get(starts[0].payload.get("trigger_event_id")) if len(starts) == 1 else None
    if (not trigger or trigger.type != "candidate.ready" or trigger.origin != "kernel"
            or not positions[producer.id] < positions[source.id] <= positions[trigger.id] < positions[starts[0].id]
            or pinned.id not in {source.id, trigger.id}):
        raise ValueError("writer candidate is not the failed reader's input")
    if trigger.id != source.id:
        if (trigger.payload.get("source") != "workflow_resume_batch" or not trigger.payload.get("resume_checkpoint_ref")
                or trigger.payload.get("candidate_event_id") != source.id or any(
                    trigger.payload.get(key) != body.get(key) for key in (
                        "workflow_run_id", "workflow_generation", "request_revision", "task_map_generation",
                        "task_map_ref", "candidate_ref", "candidate_base_commit", "candidate_head_commit",
                        "completed_task_ids", "integration_attempt_id", "fanout_id",
                    )
                )):
            raise ValueError("reader replay has diverged from its writer candidate")
    for event in events[positions[source.id] + 1:]:
        p = event.payload
        if event.origin != "kernel" or p.get("workflow_run_id") != body["workflow_run_id"]:
            continue
        if event.type in {"task_map.admitted", "task.pipeline.generation.admitted"}:
            if p.get("task_map_generation") and p["task_map_generation"] != body["task_map_generation"]:
                raise ValueError("writer candidate task map was superseded")
        if (event.type == "candidate.ready" and p.get("integration_attempt_id")
                and p["integration_attempt_id"] != body["integration_attempt_id"]):
            raise ValueError("writer candidate integration was superseded")
    return source
