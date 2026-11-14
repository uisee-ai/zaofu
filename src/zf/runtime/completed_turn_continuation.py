"""Reconcile closed task turns after result replay, without restarting workers."""
from datetime import datetime, timezone
from typing import Any

from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.provider_turn_error import bind_completed_task_pipeline_turn
from zf.runtime.provider_turn_liveness import _parse_event_ts
from zf.runtime.task_attempt_runtime import task_attempt_store


def reconcile_completed_turns(runtime: Any) -> int:
    """Reuse the existing durable nudge frontier; no new attempt or verdict.

    Reactive notification is optional: the idle sweep re-reads closed turns.
    A later prompt, pending tool, assistance request or pause holds this path.
    Further reminders require new bound work/read evidence, not just chatter.
    """
    events = runtime.event_log.read_all()
    if is_dispatch_paused(runtime.state_dir, events=events):
        return 0
    actors = {row.get("instance_id") for row in task_attempt_store(runtime).current_rows()
              if row.get("identity_version") == "operation-v2" and row.get("status") == "sent"}
    latest = {e.actor: e for e in events if e.type == "provider.turn.closed" and e.actor in actors}
    count = 0
    for actor, event in latest.items():
        bound = bind_completed_task_pipeline_turn(runtime, event)
        if bound is None:
            continue
        task = runtime.task_store.get(str(bound.task_id or ""))
        if (task is None or task.status != "in_progress" or task.assigned_to != actor
                or task.active_dispatch_id != bound.payload["dispatch_id"]):
            continue
        attempt = task_attempt_store(runtime).get(bound.payload["attempt_id"]) or {}
        expiry = _parse_event_ts(attempt.get("lease_expires_at"))
        if not expiry or expiry <= datetime.now(timezone.utc):
            continue
        if _waiting(events, actor, bound.payload["attempt_id"], task_id=task.id, created_at=attempt.get("created_at")):
            continue
        role = next((r for r in runtime.config.roles if r.instance_id == actor), None)
        if role is None:
            continue
        decision = runtime._request_terminal_completion_nudge(
            role=role, task=task, dispatch_id=task.active_dispatch_id,
            reason="provider turn completed without an admitted task result",
            causation_id=event.id, correlation_id=bound.correlation_id,
        )
        count += decision is not None
    return count


def _waiting(events: list, actor: str, attempt_id: str, *, task_id: str, created_at: str) -> bool:
    prompts = [i for i, e in enumerate(events)
               if e.actor == actor and e.type == "codex.hook.user_prompt_submit"]
    if not prompts:
        return True
    created = _parse_event_ts(created_at)
    if created is None:
        return True
    tools: set[str] = set()
    for event in events:
        timestamp = _parse_event_ts(event.ts)
        if event.actor != actor or timestamp is None or timestamp < created:
            continue
        tool_id = str(event.payload.get("tool_use_id") or "")
        if event.type == "agent.tool.use":
            if not tool_id:
                return True
            tools.add(tool_id)
        elif event.type == "agent.tool.result":
            tools.discard(tool_id)
    if tools:
        return True
    resolved = {e.payload.get("approval_ref") for e in events if e.type == "approval.resolved"}
    if any(e.type == "approval.requested" and (e.task_id or e.payload.get("task_id")) == task_id
           and e.payload.get("approval_ref") not in resolved for e in events):
        return True
    closed = {e.payload.get("request_id") for e in events if e.type == "worker.assistance.closed"}
    return any(e.type == "worker.assistance.requested" and e.actor == actor
               and e.payload.get("active_attempt_id") == attempt_id
               and e.payload.get("request_id") not in closed for e in events)
