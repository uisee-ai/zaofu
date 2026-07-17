"""Run-scoped event views for shared workflow state directories.

The runtime can host more than one request/run in one ``state_dir``.  This
module intentionally derives scope from canonical event identity instead of
creating another mutable run registry.  A legacy unscoped event is included
only when exactly one run is known, which preserves older single-run logs while
failing closed for concurrent runs.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from zf.core.events.model import ZfEvent


_CORRELATION_RUN_ANCHOR_EVENTS = frozenset({
    "loop.started",
    "run.started",
    "run.completed",
    "run.failed",
    "run.goal.started",
    "run.goal.updated",
    "run.goal.completed",
    "run.goal.blocked",
    "ship.completed",
    "ship.done",
    "workflow.invoke.requested",
})


def run_aliases(events: Iterable[ZfEvent]) -> dict[str, str]:
    """Return every known run/workflow alias mapped to its canonical run id."""

    aliases: dict[str, str] = {}
    for event in events:
        payload = _payload(event)
        explicit = tuple(
            str(value or "").strip()
            for value in (
                payload.get("run_id"),
                payload.get("workflow_run_id"),
                payload.get("trace_id"),
            )
            if str(value or "").strip()
        )
        correlation_id = str(getattr(event, "correlation_id", "") or "").strip()
        correlation_is_run_alias = bool(
            correlation_id
            and (
                explicit
                or correlation_id in aliases
                or event.type in _CORRELATION_RUN_ANCHOR_EVENTS
            )
        )
        identities = explicit + (
            (correlation_id,) if correlation_is_run_alias else ()
        )
        if not identities:
            continue
        # Prefer an already-known alias so later events may enrich a run with
        # trace/correlation aliases without splitting it into another run.
        # Do not fall back to event ids: an unscoped legacy terminal must not
        # manufacture a second run in a shared state directory.
        canonical = next(
            (aliases[identity] for identity in identities if identity in aliases),
            identities[0],
        )
        for alias in identities:
            if alias:
                aliases[alias] = canonical
    return aliases


def known_run_ids(events: Iterable[ZfEvent]) -> set[str]:
    return set(run_aliases(events).values())


def resolve_run_id(events: Iterable[ZfEvent], value: str) -> str:
    """Resolve a run/workflow alias to a canonical run id, or return empty."""

    candidate = str(value or "").strip()
    if not candidate:
        return ""
    return run_aliases(events).get(candidate, "")


def event_run_id(event: ZfEvent, *, aliases: dict[str, str]) -> str:
    """Resolve one event to a known run without guessing from arbitrary ids."""

    payload = _payload(event)
    for candidate in (
        payload.get("run_id"),
        getattr(event, "correlation_id", ""),
        payload.get("workflow_run_id"),
        payload.get("trace_id"),
    ):
        resolved = aliases.get(str(candidate or "").strip(), "")
        if resolved:
            return resolved
    return ""


def events_for_run(
    events: Iterable[ZfEvent],
    *,
    run_id: str,
    include_legacy_single_run: bool = True,
) -> list[ZfEvent]:
    """Return a replay-stable run view, excluding ambiguous unscoped facts."""

    rows = list(events)
    aliases = run_aliases(rows)
    canonical = aliases.get(str(run_id or "").strip(), "")
    if not canonical:
        return []
    singleton = len(set(aliases.values())) == 1
    scoped: list[ZfEvent] = []
    for event in rows:
        event_run = event_run_id(event, aliases=aliases)
        if event_run == canonical:
            scoped.append(event)
        elif not event_run and include_legacy_single_run and singleton:
            scoped.append(event)
    return scoped


def resolve_run_for_event(events: Iterable[ZfEvent], event: ZfEvent) -> str:
    """Resolve an event's run; legacy fallback is safe only for one run."""

    rows = list(events)
    aliases = run_aliases(rows)
    resolved = event_run_id(event, aliases=aliases)
    if resolved:
        return resolved
    canonical_ids = set(aliases.values())
    return next(iter(canonical_ids)) if len(canonical_ids) == 1 else ""


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = [
    "event_run_id",
    "events_for_run",
    "known_run_ids",
    "resolve_run_for_event",
    "resolve_run_id",
    "run_aliases",
]
