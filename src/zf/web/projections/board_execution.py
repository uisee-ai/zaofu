"""Compact Task execution facts from a single verified board snapshot."""

from collections import defaultdict
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.task.pipeline_event_scope import task_pipeline_event_scope
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.run_scope import event_run_id
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_projection import read_task_pipeline_projection
from zf.runtime.task_run_binding import accepted_request_runs
from zf.web.projections.board_fanout_execution import board_fanout_executions
from zf.web.projections.task_properties import admitted_output, latest_milestone


def board_executions(
    state_dir: Path, project_root: Path, config: ZfConfig | None, tasks: list[Task],
    rows: list[tuple[int, ZfEvent]], aliases: dict[str, str],
) -> dict[str, dict]:
    """Fold the ledger once. Card reads never open a per-task operation or detail view."""
    events = []
    own_events = defaultdict(list)
    requests_by_run = defaultdict(set)
    invocation_runs = defaultdict(set)
    managed = False
    for seq, event in rows:
        events.append(event)
        if event.task_id:
            own_events[event.task_id].append((seq, event))
        if event.type == "workflow.invoke.accepted" and event.origin == "kernel":
            run = event.payload.get("workflow_run_id") or event_run_id(event, aliases=aliases)
            request = event.payload.get("request_id")
            if event.task_id and run:
                invocation_runs[event.task_id].add(run)
            if run and request:
                requests_by_run[run].add(request)
        if event.type == "task.pipeline.generation.admitted":
            managed = True
    accepted = accepted_request_runs((e for e in events if e.origin == "kernel"), aliases)
    try:
        contexts = task_pipeline_generation_contexts(events) if managed else {}
    except ValueError:
        from zf.web.projections.projection_failures import record_projection_failure
        record_projection_failure("board_task_generation")
        contexts = {}

    pipeline = {"tasks": [], "operations": []}
    terminal = {"done", "cancelled", "archived", "superseded"}
    needs_pipeline = any(t.id in contexts and t.status not in terminal for t in tasks)
    dependencies = {dep for task in tasks if task.status in {"backlog", "ready", "blocked"} for dep in task.blocked_by}
    current_tasks = {}
    if needs_pipeline or dependencies:
        store = TaskStore(state_dir / "kanban.json")
        current_tasks = {t.id: t for t in store.list_all()}
        missing = (dependencies | (set(contexts) if needs_pipeline else set())) - current_tasks.keys()
        if missing:
            archived = defaultdict(list)
            for t in store.list_all_with_archive():
                if t.id in missing:
                    archived[t.id].append(t)
            for task_id, matches in archived.items():
                current_tasks[task_id] = matches[0] if len(matches) == 1 else (store.get(task_id) or matches[-1])
    if needs_pipeline:
        try:
            pipeline = read_task_pipeline_projection(
                state_dir, project_root=project_root, config=config, events=events, tasks=list(current_tasks.values()),
            )
        except (ValueError, OSError, KeyError):
            from zf.web.projections.projection_failures import record_projection_failure
            record_projection_failure("board_task_execution")
    views = {row["task_id"]: row for row in pipeline["tasks"]}
    operations = defaultdict(list)
    for operation in pipeline["operations"]:
        operations[operation["task_id"]].append(operation)
    outstanding = {t.id for t in current_tasks.values() if t.status != "done"}
    bound_runs = {}
    for task in tasks:
        binding = task.execution_binding
        run = binding.workflow_run_id
        if not run and binding.request_id:
            matches = accepted.get((binding.request_id, binding.request_revision), set())
            if len(matches) == 1:
                run = next(iter(matches))
        bound_runs[task.id] = run
    fanout = board_fanout_executions(events, [t for t in tasks if t.id not in contexts], bound_runs, aliases)
    result = {}
    for task in tasks:
        binding = task.execution_binding
        run = bound_runs[task.id] or fanout.get(task.id, {}).get("workflow_run_id", "")
        view = views.get(task.id, {})
        if (run or binding.request_id) and view.get("workflow_run_id") != run:
            view = {}
        if not binding.request_id and not run:
            run = contexts.get(task.id, {}).get("workflow_run_id", "")
        task_events = own_events.get(task.id, ())
        if run or binding.request_id:
            task_events = [(seq, e) for seq, e in task_events if run and
                           (e.payload.get("workflow_run_id") or event_run_id(e, aliases=aliases)) == run]
        elif len(invocation_runs[task.id]) > 1:
            task_events = [(seq, e) for seq, e in task_events if not event_run_id(e, aliases=aliases)]
        generation = contexts.get(task.id, {}).get("task_map_generation", "")
        result[task.id] = _execution(task, view, operations[task.id], task_events, run, generation)
        result[task.id].update(fanout.get(task.id, {}))
        requests = requests_by_run.get(run, set())
        if not binding.request_id and len(requests) == 1:
            result[task.id]["request_id"] = next(iter(requests))
        if not result[task.id]["waiting_for"] and task.status in {"blocked", "backlog", "ready"}:
            result[task.id]["waiting_for"] = ", ".join(dep for dep in task.blocked_by if dep in outstanding)
    return result


def _execution(task: Task, view: dict, operations: list[dict],
               events: list[tuple[int, ZfEvent]], run: str, generation: str) -> dict:
    current = next((op for op in operations if op["operation_id"] == view.get("current_operation_id")), {})
    blockers = view.get("blockers", [])
    state = current.get("status") or view.get("pipeline_stage") or task.status
    stage = current.get("stage") or view.get("pipeline_stage") or ""
    if task.status in {"done", "cancelled", "archived", "superseded", "blocked"}:
        state = task.status
    elif "run_paused" in blockers:
        state = "suspended"
    workers = []
    if state == "running" and task.active_dispatch_id:
        receipts = {
            (e.payload.get("operation_id"), e.payload.get("attempt_id")): e.payload for _, e in events
            if e.type == "task.pipeline.stage.dispatched" and e.origin == "kernel"
            and e.payload.get("task_map_generation") == view.get("task_map_generation")
        }
        running = []
        for op in operations:
            if (op["status"] != "running" or op.get("stage") != stage
                    or op.get("operation_generation") != current.get("operation_generation")):
                continue
            attempt = op.get("active_attempt_id")
            worker = op.get("role_instance")
            receipt = receipts.get((op["operation_id"], attempt), {})
            if attempt and worker and receipt.get("dispatch_id") and receipt.get("role_instance") == worker:
                running.append((receipt["dispatch_id"], {
                    "instance_id": worker, "role": stage, "attempt_id": attempt,
                    "operation_id": op["operation_id"],
                }))
        if any(dispatch == task.active_dispatch_id for dispatch, _ in running):
            workers = list({member["instance_id"]: member for _, member in running}.values())
    return {
        "stage": stage, "state": state, "active_instances": workers,
        "workflow_run_id": run, "request_id": task.execution_binding.request_id,
        "milestone": latest_milestone(task, events),
        "last_output": _last_output(task, events, generation, operations),
        "waiting_for": (task.blocked_reason if state == "blocked" else "") or ", ".join(str(b) for b in blockers),
    }


def _last_output(
    task: Task, events: list[tuple[int, ZfEvent]], generation: str, operations: list[dict],
) -> dict | None:
    current_attempts = {op["operation_id"]: op["active_attempt_id"] for op in operations if op.get("active_attempt_id")}
    scoped = task_pipeline_event_scope(task, (event for _, event in events))
    for event in reversed(scoped):
        p = event.payload
        if event.type != "workflow.call.result.admitted" or event.origin != "kernel":
            continue
        if generation and p.get("task_map_generation") != generation:
            continue
        attempt = current_attempts.get(p.get("operation_id"))
        if attempt and p.get("attempt_id") and p["attempt_id"] != attempt:
            continue
        if task.active_dispatch_id and p.get("dispatch_id") != task.active_dispatch_id:
            if not generation or not p.get("operation_id"):
                continue
        if receipt := admitted_output(event):
            return receipt
    return None
