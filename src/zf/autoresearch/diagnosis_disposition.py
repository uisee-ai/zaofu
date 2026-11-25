"""Bound lifecycle evidence for a resident diagnosis, independent of watcher shutdown."""
from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent


def controlled_cancel(events: list[ZfEvent], event: ZfEvent, run_id: str) -> bool:
    """Recognize authoritative cancels and previously emitted controlled CLI/Web receipts."""
    if event.type != "run.cancelled" or event.payload.get("run_id") != run_id:
        return False
    if event.origin == "kernel":
        return True
    if event.origin or event.actor not in {"zf-cli", "web"}:
        return False
    cause_id = event.causation_id
    if not cause_id or cause_id != event.payload.get("source_event_id"):
        return False
    request = next((e for e in events if e.id == cause_id), None)
    if (not request or request.actor != event.actor or request.type not in {
        "control.action.requested", "web.action.requested", "runtime.action.requested",
    } or request.payload.get("action") != "run-cancel"
        or (request.payload.get("request") or {}).get("run_id") != run_id):
        return False
    return any(e.type == "runtime.action.completed" and e.actor == event.actor
        and e.causation_id == event.id and e.payload.get("action") == "run-cancel"
        and e.payload.get("run_id") == run_id and e.payload.get("status") == "cancelled"
        for e in events)


def parent_disposition(events: list[ZfEvent], *, run_id: str, request_id: str, policy: str) -> dict:
    if not run_id or not request_id:
        return {}
    request_position = None
    for index, event in enumerate(events):
        body = event.payload
        if (event.type == "autoresearch.loop.requested" and body.get("loop_request_id") == request_id
                and (body.get("workflow_run_id") or body.get("run_id")) == run_id):
            request_position = index
            break
    if request_position is None:
        return {}
    for index, event in enumerate(events):
        if (event.payload.get("workflow_run_id") or event.payload.get("run_id")) != run_id:
            continue
        cancelled = controlled_cancel(events, event, run_id)
        if not cancelled and (event.origin != "kernel" or event.type not in {"run.goal.completed", "run.failed"}):
            continue
        if policy == "continue" and event.type == "run.goal.completed" and request_position < index:
            continue
        disposition = "preserved" if policy == "preserve" and not cancelled else "cancelled"
        return {
            "status": disposition, "failure_class": "parent_run_terminal",
            "reproduction_status": "incomplete", "parent_workflow_run_id": run_id,
            "parent_event_id": event.id, "parent_event_type": event.type,
            "loop_request_id": request_id, "on_parent_terminal": policy,
            "reason": f"{event.type}: {disposition}", "evidence_refs": ["event:" + event.id],
        }
    return {}


def bound_parent_stop(state: Path, run_id: str, request_id: str):
    """Resolve only the controlling project; independent loops have no parent authority."""
    if not run_id or not request_id:
        return None
    from zf.core.config.project_context import resolve_project_context
    from zf.core.events.factory import event_log_from_project
    context = resolve_project_context(cwd=state, explicit_state_dir=state, load_config_with_explicit=True)
    if not context.config or (context.project_root / context.config.project.state_dir).resolve() != state.resolve():
        return None
    policy = context.config.runtime.autoresearch_resident.on_parent_terminal

    def observe():
        log = event_log_from_project(state, config=context.config)
        try:
            return parent_disposition(log.read_all(), run_id=run_id, request_id=request_id, policy=policy)
        finally:
            log.close()
    return observe


def resident_loop_stop(state: Path, worktree: Path, seed: str):
    """The resident seed identifies a request; authoritative lifecycle comes from its ledger."""
    import json
    from zf.autoresearch.child_supervision import child_wait

    try:
        data = json.loads(seed)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("source_state_dir") != str(state.resolve()):
        return None
    observe = bound_parent_stop(state, data.get("workflow_run_id") or data.get("run_id"),
                                data.get("diagnostic_request_id"))
    if observe is None:
        return None

    def disposition():
        current = observe() or child_wait(worktree)
        status = current.get("status")
        return status if status in {"cancelled", "preserved"} else ""
    return disposition
