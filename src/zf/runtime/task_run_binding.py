"""Canonical Task-to-Run resolution shared by recovery fact projections."""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.run_scope import event_run_id, run_aliases


def canonical_task_run(task: Any, events: Iterable[ZfEvent]) -> tuple[str, bool]:
    """Return (Run, strict binding); ambiguous pending Requests never fall back."""
    binding = getattr(task, "execution_binding", None)
    run_id = str(getattr(binding, "workflow_run_id", "") or "")
    request_id = str(getattr(binding, "request_id", "") or "")
    if not run_id and request_id:
        rows = list(events)
        aliases = run_aliases(rows)
        accepted_runs = {
            event_run_id(event, aliases=aliases)
            for event in rows
            if event.type == "workflow.invoke.accepted"
            and event.payload.get("request_id") == request_id
            and event.payload.get("request_revision")
            == getattr(binding, "request_revision", 0)
            and event_run_id(event, aliases=aliases)
        }
        if len(accepted_runs) == 1:
            run_id = accepted_runs.pop()
    return run_id, bool(run_id or request_id)
