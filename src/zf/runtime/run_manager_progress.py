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
    usage_samples: dict[str, tuple[str, str]] = {}
    for event in events:
        signal = _TYPE_TO_SIGNAL.get(event.type)
        if not signal or event.task_id not in result:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        sample_ts = payload.get("usage_timestamp")
        if event.type == "agent.usage" and isinstance(sample_ts, str) and sample_ts:
            usage_samples[event.id] = (event.task_id, sample_ts)
        evidence: dict[str, Any] = {
            "event_id": event.id, "type": event.type, "ts": event.ts,
            "workflow_run_id": str(payload.get("workflow_run_id") or ""),
            "attempt_id": str(payload.get("attempt_id") or ""),
        }
        source_id = (
            event.id if event.type == "agent.usage"
            else str(payload.get("source_event_id") or "")
        )
        source_usage = usage_samples.get(source_id)
        if signal == "activity" and source_usage and source_usage[0] == event.task_id:
            evidence["usage_sample_ts"] = source_usage[1]
            evidence["source_usage_event_id"] = source_id
        result[event.task_id][signal] = evidence
    return result
