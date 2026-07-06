"""Sprint 3 T3 — verdict-less workflow invokes get replayed by the tick."""

from __future__ import annotations

import time
from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.tick_services import (
    INVOKE_REPLAY_GRACE_SECONDS,
    _replay_unconsumed_invokes,
)


class _RecordingOrchestrator:
    def __init__(self) -> None:
        self.handled: list[str] = []

    def _on_workflow_invoke_requested(self, event) -> None:
        self.handled.append(event.id)


def _log(tmp_path: Path) -> tuple[EventLog, EventWriter]:
    log = EventLog(tmp_path / ".zf" / "events.jsonl")
    return log, EventWriter(log)


def test_backlog_invoke_replays_after_grace(tmp_path: Path) -> None:
    log, writer = _log(tmp_path)
    invoke = writer.emit(
        "workflow.invoke.requested", actor="operator",
        payload={"task_id": "TASK-1", "pattern_id": "prd-refine"},
    )
    orch = _RecordingOrchestrator()
    future = time.time() + INVOKE_REPLAY_GRACE_SECONDS + 5
    assert _replay_unconsumed_invokes(orch, event_log=log, now=future) == 1
    assert orch.handled == [invoke.id]


def test_invoke_with_verdict_not_replayed(tmp_path: Path) -> None:
    log, writer = _log(tmp_path)
    invoke = writer.emit(
        "workflow.invoke.requested", actor="operator",
        payload={"task_id": "TASK-1", "pattern_id": "prd-refine"},
    )
    writer.emit(
        "workflow.invoke.accepted", actor="zf-cli",
        payload={"task_id": "TASK-1", "source_event_id": invoke.id},
    )
    orch = _RecordingOrchestrator()
    future = time.time() + INVOKE_REPLAY_GRACE_SECONDS + 5
    assert _replay_unconsumed_invokes(orch, event_log=log, now=future) == 0
    assert orch.handled == []


def test_fresh_invoke_left_for_live_watcher(tmp_path: Path) -> None:
    log, writer = _log(tmp_path)
    writer.emit(
        "workflow.invoke.requested", actor="operator",
        payload={"task_id": "TASK-1", "pattern_id": "prd-refine"},
    )
    orch = _RecordingOrchestrator()
    assert _replay_unconsumed_invokes(orch, event_log=log, now=time.time()) == 0
    assert orch.handled == []
