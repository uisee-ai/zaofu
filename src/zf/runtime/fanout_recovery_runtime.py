"""Recovery helpers shared by reader and writer fanout coordination."""

from __future__ import annotations

import json

from zf.core.events.model import ZfEvent


def recover_unrecorded_reader_fanout_results(runtime) -> None:
    """Reconcile reader results in restart-safe, cheapest-first order."""

    from zf.runtime.reader_fanout_recovery_snapshot import (
        build_reader_fanout_recovery_snapshot,
        event_log_snapshot_token,
    )

    try:
        from zf.runtime.event_window import read_runtime_events

        events = read_runtime_events(runtime.event_log, runtime.state_dir)
    except Exception:
        return
    from zf.runtime.reader_terminal_recovery import reconcile_exhausted_reader_operations

    if reconcile_exhausted_reader_operations(runtime, events):
        return
