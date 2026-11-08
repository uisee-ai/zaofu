"""Bind repeated pre-dispatch diagnosis to live, admitted Task Pipeline work."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.event_problem_registry import spec_for_event
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts

DEFERRED = 'task.pipeline.stage.dispatch_deferred'
FAILURE_CLASS = 'task_pipeline_dispatch_deferred'


def pending_dispatch_failures(events: list[ZfEvent]) -> dict[str, tuple[ZfEvent, ZfEvent]]:
    """A causal Kernel cooldown proves repetition; activity alone proves no repair."""
    kernel = [e for e in events if e.origin == 'kernel']
    contexts = task_pipeline_generation_contexts(kernel)
    seen: dict[str, ZfEvent] = {}
    pending: dict[str, tuple[ZfEvent, ZfEvent]] = {}
    latest: dict[tuple[str, ...], ZfEvent] = {}
    for event in kernel:
        payload = event.payload
        if event.type == DEFERRED:
            scope = _scope(event)
            context = contexts.get(str(event.task_id or '')) or {}
            if (all(scope) and payload.get('task_id') == event.task_id
                    and context.get('workflow_run_id') == scope[0]
                    and context.get('task_map_generation') == scope[2]
                    and event.causation_id == context.get('generation_admitted_event_id')):
                previous = latest.get(scope)
                if previous:
                    pending.pop(previous.id, None)
                latest[scope] = event
                seen[event.id] = event
        elif event.type == 'orchestrator.dispatch_cooldown':
            source = seen.get(str(event.causation_id or ''))
            if (source and event.task_id == source.task_id
                    and event.correlation_id == source.payload.get('workflow_run_id')
                    and _positive_int(payload.get('failures_in_window')) > 1
                    and latest.get(_scope(source)) is source):
                pending.setdefault(source.id, (source, event))
        elif event.type == 'task.pipeline.stage.dispatched':
            for event_id, source in list(seen.items()):
                if (_scope(source)[:4] == _scope(event)[:4]
                        and _positive_int(payload.get('operation_generation')) >= _positive_int(source.payload.get('operation_generation'))
                        and all(payload.get(k) for k in ('operation_id', 'dispatch_id', 'attempt_id'))):
                    pending.pop(event_id, None)
                    # A late old cooldown cannot revive a successful dispatch.
                    seen.pop(event_id, None)
        elif event.type in {'task.done', 'task.superseded'}:
            for event_id, source in list(seen.items()):
                if (event.task_id == source.task_id
                        and (payload.get('workflow_run_id') or event.correlation_id) == source.payload.get('workflow_run_id')):
                    pending.pop(event_id, None)
                    seen.pop(event_id, None)
    return pending


def dispatch_failure_attention_items(
    events: list[ZfEvent], *, make_item: Callable[..., dict[str, Any]],
) -> list[dict[str, Any]]:
    spec = spec_for_event(DEFERRED)
    if spec is None or spec.supervisor_attention != 'on_repeated':
        return []
    items = []
    for source, cooldown in pending_dispatch_failures(events).values():
        payload = source.payload
        action = {k: payload[k] for k in (
            'workflow_run_id', 'task_id', 'task_map_generation',
            'task_pipeline_stage', 'operation_generation',
        )}
        action.update(kind=spec.suggested_action_kind, source_event_id=source.id,
                      cooldown_event_id=cooldown.id)
        items.append(make_item(
            source=spec.source, fingerprint=f'{FAILURE_CLASS}:{source.id}',
            severity=spec.severity, title=spec.title, summary=str(payload.get('reason') or ''),
            task_id=str(source.task_id), source_event_ids=[source.id, cooldown.id],
            source_ref=f'events.jsonl#{source.id}', suggested_route=spec.suggested_route,
            suggested_action=action, failure_class=spec.failure_class,
            owner_route=spec.owner_route, action_policy=spec.action_policy,
            intervention_class=spec.intervention_class,
            notification_policy=spec.effective_notification_policy,
            recovery_policy=spec.effective_recovery_policy,
        ))
    return items


def dispatch_attention_current(item: dict[str, Any], events: list[ZfEvent]) -> bool | None:
    """None means another attention kind; false fences stale Supervisor snapshots."""
    ids = set(item.get('source_event_ids') or [])
    sources = {e.id for e in events if e.id in ids and e.type == DEFERRED}
    if not sources:
        return None
    return bool(sources.intersection(pending_dispatch_failures(events)))


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _scope(event: ZfEvent) -> tuple[str, ...]:
    p = event.payload
    return (str(p.get('workflow_run_id') or ''), str(event.task_id or ''),
            str(p.get('task_map_generation') or ''), str(p.get('task_pipeline_stage') or ''),
            str(_positive_int(p.get('operation_generation'))) if _positive_int(p.get('operation_generation')) else '')
