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

_CANONICAL_RUN_ANCHOR_EVENTS = frozenset({
    "run.started",
    "run.goal.started",
    "workflow.invoke.requested",
})


def run_aliases(events: Iterable[ZfEvent]) -> dict[str, str]:
    """Return every known run/workflow alias mapped to its canonical run id."""

    rows = list(events)
    canonical_roots: dict[str, int] = {}
    for event in rows:
        if event.type not in _CANONICAL_RUN_ANCHOR_EVENTS:
            continue
        payload = _payload(event)
        root = str(
            payload.get("run_id")
            or payload.get("workflow_run_id")
            or getattr(event, "correlation_id", "")
            or ""
        ).strip()
        if not root:
            continue
        priority = 0 if event.type in {"run.started", "run.goal.started"} else 1
        canonical_roots[root] = min(priority, canonical_roots.get(root, priority))
    aliases: dict[str, str] = {}
    for event in rows:
        payload = _payload(event)
        correlation_id = str(getattr(event, "correlation_id", "") or "").strip()
        if not (payload.get("run_id") or payload.get("workflow_run_id") or payload.get("trace_id")):
            # Most ledger records have no explicit run identity. They can
            # contribute only an already-known correlation or an anchor.
            if not correlation_id or (
                correlation_id not in aliases
                and event.type not in _CORRELATION_RUN_ANCHOR_EVENTS
            ):
                continue
            identities = (correlation_id,)
        else:
            explicit = tuple(
                str(value or "").strip()
                for value in (
                    payload.get("run_id"),
                    payload.get("workflow_run_id"),
                    payload.get("trace_id"),
                )
                if str(value or "").strip()
            )
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
        # An event may be malformed or stale and carry identities from two
        # already-established runs.  Such an event is evidence of a conflict,
        # not authority to union the two run namespaces.  Keep both canonical
        # roots intact so admission can reject the event in its original
        # identity domain.
        established_roots = {
            (
                identity
                if identity in canonical_roots
                else aliases.get(identity, "")
            )
            for identity in identities
        }
        established_roots = {
            root for root in established_roots if root in canonical_roots
        }
        if len(established_roots) > 1:
            for root in established_roots:
                aliases[root] = root
            continue
        # An approved run anchor outranks a pre-run operation namespace. This
        # keeps request synthesis operations scoped to the eventual run without
        # letting an identity such as ``workflow-request:<id>:r<n>`` replace
        # the public run id merely because its event was appended first.
        root_candidates = [
            (canonical_roots[identity], index, identity)
            for index, identity in enumerate(identities)
            if identity in canonical_roots
        ]
        canonical = min(root_candidates, default=(0, 0, ""))[2]
        if not canonical:
            canonical = next(
                (
                    aliases[identity]
                    for identity in identities
                    if identity in aliases
                    and aliases[identity] in canonical_roots
                ),
                "",
            )
        if not canonical:
            # Prefer an already-known alias so later events may enrich a run
            # with trace/correlation aliases without splitting it into another
            # run. Do not fall back to event ids: an unscoped legacy terminal
            # must not manufacture a second run in a shared state directory.
            canonical = next(
                (
                    aliases[identity]
                    for identity in identities
                    if identity in aliases
                ),
                identities[0],
            )
        replaced = {
            aliases[identity]
            for identity in identities
            if identity in aliases and aliases[identity] != canonical
        }
        if replaced:
            aliases = {
                alias: canonical if target in replaced else target
                for alias, target in aliases.items()
            }
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
    _aliases: dict[str, str] | None = None,
) -> list[ZfEvent]:
    """Return a replay-stable run view, excluding ambiguous unscoped facts."""

    rows = list(events)
    aliases = _aliases if _aliases is not None else run_aliases(rows)
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


def resolve_run_for_completion_cause(
    events: Iterable[ZfEvent],
    event: ZfEvent,
) -> str:
    """Resolve completion evidence without singleton-guessing explicit ids."""

    rows = list(events)
    aliases = run_aliases(rows)
    payload = _payload(event)
    direct_values = [
        str(value or "").strip()
        for value in (
            payload.get("run_id"),
            payload.get("workflow_run_id"),
            _nested_run_id(payload.get("goal_closure_result")),
            _nested_run_id(payload.get("artifact_delivery_result")),
        )
        if str(value or "").strip()
    ]
    if direct_values:
        resolved = [aliases.get(value, "") for value in direct_values]
        if any(not value for value in resolved):
            return ""
        canonical = set(resolved)
        if len(canonical) != 1:
            return ""
        correlation_id = str(event.correlation_id or "").strip()
        correlation_run = aliases.get(correlation_id, "")
        if correlation_run and correlation_run not in canonical:
            return ""
        return next(iter(canonical))

    correlation_id = str(event.correlation_id or "").strip()
    if correlation_id:
        # A historical correlation that is no longer resolvable must not be
        # rebound to the only currently active run.
        return aliases.get(correlation_id, "")
    canonical_ids = set(aliases.values())
    return next(iter(canonical_ids)) if len(canonical_ids) == 1 else ""


def _nested_run_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("run_id") or value.get("workflow_run_id") or "")


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = [
    "event_run_id",
    "events_for_run",
    "known_run_ids",
    "resolve_run_for_event",
    "resolve_run_for_completion_cause",
    "resolve_run_id",
    "run_aliases",
]
