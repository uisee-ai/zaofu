"""Read-only Board membership of Task-bound workflow fanouts."""

from collections import defaultdict

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.fanout_identity import fanout_current_statuses
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.run_scope import event_run_id
from zf.runtime.workflow_operation import reduce_workflow_operations


_INACTIVE_TASK_STATES = {"done", "blocked", "archived", "cancelled", "superseded"}


def board_fanout_executions(
    events: list[ZfEvent], tasks: list[Task], bound_runs: dict[str, str], aliases: dict[str, str],
) -> dict[str, dict]:
    """Prefer explicit Task bindings; an accepted invocation can identify its own root."""
    if not tasks:
        return {}
    invocation_by_run = {}
    by_run = defaultdict(list)
    for event in events:
        if event.origin != "kernel":
            continue
        p = event.payload
        run = p.get("workflow_run_id") or event_run_id(event, aliases=aliases)
        if not run:
            continue
        by_run[run].append(event)
        if event.type == "workflow.invoke.accepted":
            invocation_by_run[run] = event
    invocations = defaultdict(set)
    for run, event in invocation_by_run.items():
        owner = event.task_id or event.payload.get("task_id")
        if owner and event.payload.get("task_id", owner) == owner:
            invocations[owner].add(run)
    groups = defaultdict(dict)
    for task in tasks:
        run = bound_runs.get(task.id) or task.execution_binding.workflow_run_id
        if not run and not task.execution_binding.request_id and len(invocations[task.id]) == 1:
            run = next(iter(invocations[task.id]))
        if run and run in by_run:
            groups[run][task.id] = task
    result = {}
    for run, group in groups.items():
        rows = by_run[run]
        invocation = invocation_by_run.get(run)
        admission = build_run_admission_projection(rows).runs.get(run)
        if not admission:
            continue
        for task_id, task in group.items():
            result[task_id] = {"workflow_run_id": run, "active_instances": []}
            if invocation and invocation.payload.get("request_id") and (
                (invocation.task_id or invocation.payload.get("task_id")) == task_id
                or task.execution_binding.request_id == invocation.payload["request_id"]
            ):
                result[task_id]["request_id"] = invocation.payload["request_id"]
            if task.status not in _INACTIVE_TASK_STATES and admission.status in {"paused", "queued"}:
                result[task_id]["state"] = admission.status
        if admission.status == "running":
            _add_current_members(rows, run, invocation, group, result)
    return result


def _add_current_members(
    rows: list[ZfEvent], run: str, invocation: ZfEvent | None, group: dict[str, Task], result: dict[str, dict],
) -> None:
    current_invocation = invocation.payload if invocation else {}
    revision = current_invocation.get("request_revision")
    generation = current_invocation.get("workflow_generation")
    floor = 0
    task_map = None
    previous_binding = None
    for index, event in enumerate(rows):
        p = event.payload
        if event.type == "workflow.invoke.accepted":
            binding = (p.get("workflow_generation"), p.get("request_revision"))
            if previous_binding is not None and binding != previous_binding:
                floor, task_map = index, None
            previous_binding = binding
        if event.type in {"task_map.ready", "task.pipeline.generation.admitted"} and p.get("task_map_generation"):
            if p.get("workflow_generation", generation) == generation and p["task_map_generation"] != task_map:
                floor, task_map = index, p["task_map_generation"]
    rows = rows[floor:]
    operations = reduce_workflow_operations(rows, workflow_run_id=run)
    fanouts = fanout_current_statuses(rows)
    starts, dispatches, ended, closed_attempts = {}, {}, set(), set()
    for event in rows:
        p = event.payload
        fid = p.get("fanout_id")
        if not fid:
            continue
        if event.type == "fanout.started":
            starts[fid] = event
            ended.discard(fid)
        elif event.type in {"fanout.aggregate.completed", "fanout.timed_out", "fanout.cancelled"}:
            ended.add(fid)
        elif event.type in {"fanout.child.completed", "fanout.child.failed", "fanout.synth.completed"}:
            closed_attempts.add((fid, p.get("operation_id"), p.get("attempt_id")))
        elif event.type in {"fanout.child.dispatched", "fanout.synth.dispatched"}:
            if fid in starts:
                dispatches[fid, p.get("operation_id"), p.get("attempt_id")] = (event, starts[fid].id)
    members = defaultdict(dict)
    for (fid, oid, attempt), (receipt, start_id) in dispatches.items():
        start, op = starts.get(fid), operations.get(oid)
        if start.id != start_id or not fanouts[fid].current or fid in ended or (fid, oid, attempt) in closed_attempts:
            continue
        if not op or op["status"] != "running" or op.get("admitted_call_result_ref") or op.get("divergent"):
            continue
        p = receipt.payload
        trigger = start.payload.get("trigger_payload") or {}
        stage = start.payload.get("stage_id")
        owner = receipt.task_id or p.get("task_id") or start.task_id or start.payload.get("task_id")
        task = group.get(owner)
        if not task or task.status in _INACTIVE_TASK_STATES:
            continue
        role = p.get("role_instance")
        if (owner != op["task_id"] or p.get("task_id", owner) != owner
                or not stage or p.get("stage_id") != stage or op["parent_stage_id"] != stage
                or not role or role != op["role_instance"] or not attempt or attempt != op["active_attempt_id"]
                or not p.get("request_hash") or p["request_hash"] != op["request_hash"]
                or (p.get("dispatch_id") and p["dispatch_id"] != op["dispatch_id"])
                or (task.active_dispatch_id and task.active_dispatch_id != op["dispatch_id"])):
            continue
        bindings = {"workflow_generation": generation, "request_revision": revision, "task_map_generation": task_map}
        if any(expected and source.get(key) and source[key] != expected
               for key, expected in bindings.items() for source in (trigger, p)):
            continue
        members[owner][role] = (stage, {
            "instance_id": role, "role": p.get("role") or "", "attempt_id": attempt, "operation_id": oid,
        })
    for task_id, active in members.items():
        stages = dict.fromkeys(stage for stage, _ in active.values())
        result[task_id].update(state="running", active_instances=[member for _, member in active.values()])
        if len(stages) == 1:
            result[task_id]["stage"] = next(iter(stages))
