"""Canonical Task-to-Run resolution shared by recovery fact projections."""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import event_run_id, run_aliases


def accepted_request_runs(
    events: Iterable[ZfEvent], aliases: dict[str, str],
) -> dict[tuple[str, int], set[str]]:
    """Request revision to all observed Run identities; ambiguity is preserved."""
    result: dict[tuple[str, int], set[str]] = {}
    for event in events:
        if event.type != "workflow.invoke.accepted":
            continue
        run = event_run_id(event, aliases=aliases)
        request = event.payload.get("request_id")
        revision = event.payload.get("request_revision")
        if run and isinstance(request, str) and isinstance(revision, int):
            result.setdefault((request, revision), set()).add(run)
    return result


def canonical_task_run(task: Any, events: Iterable[ZfEvent]) -> tuple[str, bool]:
    """Return (Run, strict binding); ambiguous pending Requests never fall back."""
    binding = getattr(task, "execution_binding", None)
    run_id = str(getattr(binding, "workflow_run_id", "") or "")
    request_id = str(getattr(binding, "request_id", "") or "")
    if not run_id and request_id:
        rows = list(events)
        aliases = run_aliases(rows)
        accepted_runs = accepted_request_runs(rows, aliases).get(
            (request_id, getattr(binding, "request_revision", 0)), set()
        )
        if len(accepted_runs) == 1:
            run_id = accepted_runs.pop()
    return run_id, bool(run_id or request_id)
