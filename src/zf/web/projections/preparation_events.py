"""Locate preparation events without replaying unrelated provider history."""
from __future__ import annotations

from pathlib import Path
import sqlite3

from zf.core.events.log import EventLog
from zf.runtime.workflow_operation import OPERATION_EVENT_TYPES
from zf.web.projections import metric_facts
from zf.web.projections.source_cache import fingerprint


def read_preparation_events(state_dir: Path, config, event_log: EventLog):
    """Return complete, ordered, canonically decoded operation events.

    SQLite stores offsets only. A failed index falls back to the canonical log;
    a moving source is retried rather than consuming a mixed generation.
    """
    for _ in range(3):
        before = fingerprint(state_dir, [], config)
        try:
            metric_facts.refresh_events(state_dir, config)
            rows = metric_facts.events(state_dir, full=True, config=config,
                types=tuple(sorted(OPERATION_EVENT_TYPES | {"workflow.call.result.admitted"})))
            events = [event for _, event in rows]
        except (sqlite3.Error, OSError, ValueError):
            events = event_log.read_all()
        if before == fingerprint(state_dir, [], config):
            return events
    raise TimeoutError("Workflow preparation source changed during read")
