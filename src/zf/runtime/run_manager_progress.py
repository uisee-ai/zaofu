"""Read-only task-scoped activity and result signals for Run Manager."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from zf.core.events.model import ZfEvent


_SIGNAL_TYPES = {
    "dispatch": frozenset({"task.dispatched", "task.pipeline.stage.dispatched"}),
    "activity": frozenset({"worker.heartbeat", "task.attempt.heartbeat", "agent.usage"}),
    "result_signal": frozenset({
        "dev.build.done", "dev.blocked", "task.ref.updated", "task.done",
        "test.passed", "test.failed", "verify.passed", "verify.failed",
        "workflow.operation.settled", "workflow.operation.failed",
        "workflow.operation.blocked",
    }),
}
_TYPE_TO_SIGNAL = {
    event_type: signal
    for signal, event_types in _SIGNAL_TYPES.items()
    for event_type in event_types
}


def task_evidence_signals(
    events: Iterable[ZfEvent], task_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Report ledger signals, without interpreting them as accepted progress.

    Attribution is deliberately task-scoped, not attempt-scoped. An event
    without a task_id cannot establish evidence for any task.
    """
    result: dict[str, dict[str, Any]] = {
        task_id: {signal: None for signal in _SIGNAL_TYPES}
        for task_id in task_ids if task_id
    }
    for event in events:
        signal = _TYPE_TO_SIGNAL.get(event.type)
        if not signal or event.task_id not in result:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        result[event.task_id][signal] = {
            "event_id": event.id, "type": event.type, "ts": event.ts,
            "workflow_run_id": str(payload.get("workflow_run_id") or ""),
            "attempt_id": str(payload.get("attempt_id") or ""),
        }
    return result
