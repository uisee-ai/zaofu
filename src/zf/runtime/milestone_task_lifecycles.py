"""Freeze Task Pipeline scheduling evidence for the exact candidate roster."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from zf.core.events import ZfEvent

SOURCE = "milestone-task-lifecycles"
_TERMINAL = {"workflow.operation." + state for state in ("settled", "failed", "cancelled", "superseded")}


def _stamp(event: ZfEvent) -> dict:
    return {"event_id": event.id, "at": event.ts, "type": event.type}


def _entry_summary(generation: str, package: str, entries: dict) -> dict:
    # The first observed entry per member has a single interval. Keep retries
    # independently inspectable in the task rows, never pool Plan generations.
    changes = defaultdict(lambda: defaultdict(int))
    starts, ends = [], []
    for row in entries.values():
        if not row["start"] or not row["terminal"]:
            continue
        try:
            start = datetime.fromisoformat(row["start"]["at"])
            end = datetime.fromisoformat(row["terminal"]["at"])
            if not start.tzinfo or not end.tzinfo or end < start:
                continue
        except ValueError:
            continue
        starts.append((start, row["start"]["event_id"]))
        ends.append((end, row["terminal"]["event_id"]))
        changes[row["stage"]][start] += 1
        changes[row["stage"]][end] -= 1
    peaks = {}
    for stage, ticks in changes.items():
        active = peak = 0
        for timestamp in sorted(ticks):
            active += ticks[timestamp]
            peak = max(peak, active)
        peaks[stage] = peak
    overlap = None
    bounds = {}
    if len(peaks) > 1 and len(starts) == len(entries):
        last_start, start_event = max(starts)
        first_end, end_event = min(ends)
        overlap = max(0., (first_end - last_start).total_seconds())
        bounds = {"latest_start_event": start_event, "earliest_terminal_event": end_event}
    return {"task_map_generation": generation, "plan_artifact_package_digest": package,
            "observed_entry_tasks": sorted(entries), "closed_entry_count": len(starts),
            "peak_by_entry_stage": peaks, "first_entry_overlap_seconds": overlap, "overlap_bounds": bounds}


def task_lifecycle_evidence(events: list[ZfEvent], *, run: str, generation: str, target: str,
                            freeze_id: str, members: list[dict]) -> dict | None:
    freeze_index = next((i for i, e in enumerate(events) if e.id == freeze_id), None)
    if freeze_index is None:
        return None
    freeze = events[freeze_index]
    p = freeze.payload
    targets = {p[k] for k in ("target_commit", "candidate_head_commit") if p.get(k)}
    if (freeze.origin != "kernel" or freeze.type != "candidate.ready" or p.get("workflow_run_id") != run
            or p.get("task_map_generation") != generation or targets != {target}):
        return None
    tasks = {m["task_id"]: [] for m in members}
    generations = {generation, *(m.get("task_map_generation") for m in members)} - {None, ""}
    starts, pending, entries, seen = {}, {}, {}, set()
    for event in events[:freeze_index]:
        q = event.payload
        task = q.get("task_id") or event.task_id
        if event.origin != "kernel" or q.get("workflow_run_id") != run or task not in tasks:
            continue
        operation, request = q.get("operation_id"), q.get("request_hash")
        if not (operation and request) or (event.task_id and event.task_id != task):
            continue
        key = task, operation, request
        if event.type == "workflow.operation.started":
            starts[key] = event
            pending.pop(key, None)
        elif event.type in _TERMINAL:
            starts.pop(key, None)
            row = pending.pop(key, None)
            if (row is not None and q.get("active_attempt_id", row["attempt_id"]) == row["attempt_id"]
                    and q.get("dispatch_id", row["dispatch_id"]) == row["dispatch_id"]):
                row["terminal"] = _stamp(event)
        elif event.type == "task.pipeline.stage.dispatched":
            gen, pkg = q.get("task_map_generation"), q.get("plan_artifact_package_digest")
            stage, mode = q.get("task_pipeline_stage"), q.get("task_pipeline_entry_mode")
            attempt, dispatch = q.get("attempt_id"), q.get("dispatch_id")
            if (gen not in generations or not pkg or stage not in {"impl", "verify"} or q.get("stage_id") != stage
                    or not attempt or not dispatch or (*key, attempt, dispatch) in seen):
                continue
            seen.add((*key, attempt, dispatch))
            start = starts.get(key)
            if start and (start.payload.get("active_attempt_id"), start.payload.get("dispatch_id")) != (attempt, dispatch):
                start = None
            first_stage = (mode, stage) in {("standard", "impl"), ("verify_only", "verify")}
            row = {"operation_id": operation, "request_hash": request, "attempt_id": attempt, "dispatch_id": dispatch,
                   "task_map_generation": gen, "plan_artifact_package_digest": pkg,
                   "stage": stage, "entry_mode": mode, "entry_stage": first_stage,
                   "dispatch_event": event.id, "dispatched_at": event.ts, "causation_event": event.causation_id,
                   "start": _stamp(start) if start else None, "terminal": None}
            pending[key] = row
            tasks[task].append(row)
            if first_stage:
                entries.setdefault((gen, pkg), {}).setdefault(task, row)
    if not any(tasks.values()):
        return None
    ids = sorted(tasks)
    return {"schema_version": "milestone-task-lifecycles.v1",
            "summary": {"workflow_run_id": run, "candidate_event_id": freeze_id, "target_commit": target,
                        "candidate_generation": generation, "task_ids": ids,
                        "measurement": "kernel Task Pipeline operations; first observed entry per task and Plan package",
                        "generations": [_entry_summary(gen, pkg, rows) for (gen, pkg), rows in entries.items()]},
            "tasks": [{"task_id": task, "operations": tasks[task]} for task in ids]}


def aggregate_recovery_lifecycles(state: Path, events: list[ZfEvent], run: str, candidate: dict) -> dict | None:
    """Supply the OA with the same scheduler facts as its bound aggregate Reader."""
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_operation import reduce_workflow_operations

    op = reduce_workflow_operations(events, workflow_run_id=run).get(candidate["operation_id"], {})
    if not op.get("request_ref"):
        return None
    request = hydrate_sidecar_ref(state, op["request_ref"]).payload["request"]
    ref = request.get("milestone_handoff_ref")
    if not ref:
        return None
    handoff = hydrate_sidecar_ref(state, ref).payload
    binding = handoff["binding"]
    if (handoff["consumer"]["operation_id"] != candidate["operation_id"]
            or binding["workflow_run_id"] != run or binding["target_commit"] != candidate["source_commit"]
            or binding["task_map_generation"] != candidate["task_map_generation"]
            or handoff["candidate_event_id"] != candidate["candidate_event_id"]):
        return None
    if lifecycle_ref := handoff.get("task_pipeline_lifecycles_ref"):
        evidence = hydrate_sidecar_ref(state, lifecycle_ref).payload
    else:
        # A fresh OA checkpoint may review a pre-upgrade Reader. Keep its old
        # manifest immutable and bind the evidence into this new decision input.
        evidence = task_lifecycle_evidence(events, run=run, generation=binding["task_map_generation"],
            target=binding["target_commit"], freeze_id=handoff["candidate_event_id"], members=handoff["members"])
    if evidence:
        return {"source_handoff_digest": ref["sha256"], "summary": evidence["summary"]}
    return None
