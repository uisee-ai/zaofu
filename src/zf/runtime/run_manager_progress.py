"""Read-only task-scoped activity and result signals for Run Manager."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from zf.core.events.model import ZfEvent


_SIGNAL_TYPES = {
    "dispatch": frozenset({"task.dispatched", "task.pipeline.stage.dispatched"}),
    "activity": frozenset({"worker.heartbeat", "task.attempt.heartbeat", "agent.usage"}),
    "input": frozenset({"artifact.input.observed"}),
    "work_record": frozenset({"worker.work_record.published"}),
    "verification": frozenset({"verify.passed", "verify.failed", "test.passed", "test.failed"}),
    "accepted_result": frozenset({"workflow.call.result.admitted"}),
    "continuation": frozenset({"worker.continuation.claimed", "worker.stuck.recovered"}),
    "result_signal": frozenset({
        "dev.build.done", "dev.blocked", "task.ref.updated", "task.done",
        "test.passed", "test.failed", "verify.passed", "verify.failed",
        "workflow.operation.settled", "workflow.operation.failed",
        "workflow.operation.blocked",
    }),
}
_IDENTITY_KEYS = ("workflow_run_id", "operation_id", "attempt_id", "dispatch_id")


def _identity(payload: dict[str, Any]) -> dict[str, str]:
    return {key: str(payload.get(key) or (
        payload.get("active_attempt_id") if key == "attempt_id" else ""
    ) or "") for key in _IDENTITY_KEYS}


def task_evidence_signals(
    events: Iterable[ZfEvent], task_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Report ledger signals, without interpreting them as accepted progress.

    Identified dispatches fence later signals to that execution. Legacy events
    remain task-scoped when no execution identity was recorded. Signal presence
    is not a semantic judgement of useful progress or product acceptance.
    """
    result: dict[str, dict[str, Any]] = {
        task_id: {signal: None for signal in _SIGNAL_TYPES}
        for task_id in task_ids if task_id
    }
    usage_samples: dict[str, tuple[str, str]] = {}
    bindings: dict[str, dict[str, str]] = {}
    for event in events:
        signals = [name for name, types in _SIGNAL_TYPES.items() if event.type in types]
        if not signals or event.task_id not in result:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if "continuation" in signals and (event.origin != "kernel"
                or payload.get("recovery_action") != "completion_nudge_requested"):
            continue
        identity = _identity(payload)
        if "dispatch" in signals:
            prior = bindings.get(event.task_id)
            if prior is not None and prior != identity:
                result[event.task_id] = {name: None for name in _SIGNAL_TYPES}
            bindings[event.task_id] = identity
        else:
            bound = bindings.get(event.task_id, {})
            if any(value and identity[key] and identity[key] != value for key, value in bound.items()):
                continue
            # Some sanctioned observers have attempt identity but no dispatch id.
            # Run or operation alone cannot distinguish retries of one operation.
            strongest = [key for key in ("attempt_id", "dispatch_id") if bound.get(key)]
            if strongest and not any(identity[key] == bound[key] for key in strongest):
                continue
            if not strongest and any(value and identity[key] != value for key, value in bound.items()):
                continue
        if "accepted_result" in signals and event.origin != "kernel":
            continue
        sample_ts = payload.get("usage_timestamp")
        if event.type == "agent.usage" and isinstance(sample_ts, str) and sample_ts:
            usage_samples[event.id] = (event.task_id, sample_ts)
        evidence: dict[str, Any] = {
            "event_id": event.id, "type": event.type, "ts": event.ts,
            "workflow_run_id": str(payload.get("workflow_run_id") or ""),
            "attempt_id": str(payload.get("attempt_id") or ""),
        }
        if event.type == "artifact.input.observed":
            evidence["observation_kind"] = str(payload.get("kind") or "unknown")
            evidence["proves_current_memory"] = False
        if "continuation" in signals:
            delivered = event.type == "worker.stuck.recovered" and payload.get("prompt_injected") is True
            evidence.update(delivery_status="delivered" if delivered else "unknown",
                recovery_owner="run_manager", business_completed=False,
                next_action="observe_bound_progress" if delivered else "reconcile_existing_turn_before_retry")
        source_id = (
            event.id if event.type == "agent.usage"
            else str(payload.get("source_event_id") or "")
        )
        source_usage = usage_samples.get(source_id)
        if "activity" in signals and source_usage and source_usage[0] == event.task_id:
            evidence["usage_sample_ts"] = source_usage[1]
            evidence["source_usage_event_id"] = source_id
        for signal in signals:
            result[event.task_id][signal] = evidence
    return result
