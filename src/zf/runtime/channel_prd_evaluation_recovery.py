"""Recover lost PRD evaluations using an execution-lifetime process lock."""

from pathlib import Path

from zf.core.events import EventWriter, ZfEvent
from zf.core.state.locks import FileLock, lock_path_for


TERMINAL_EVALUATION_EVENTS = {"output.evaluation.completed", "output.evaluation.failed"}


def evaluation_execution_lock(state_dir: Path, request_id: str) -> FileLock:
    path = Path(state_dir) / "locks" / f"channel-prd-evaluation-{request_id}.lock"
    return FileLock(lock_path_for(path), timeout_seconds=0)


def evaluation_failure(writer: EventWriter, request: ZfEvent, *, reason: str,
                       causation_id: str) -> None:
    payload = request.payload or {}
    writer.emit(
        "output.evaluation.failed", actor="output-evaluator",
        causation_id=causation_id, correlation_id=request.correlation_id,
        payload={"request_id": payload.get("request_id"), "verdict": "INDETERMINATE",
                 "reason": reason[:500], "manifest": {
                     "context": {"channel_id": payload.get("channel_id"),
                                 "thread_id": payload.get("thread_id")},
                     "target": {"ref": payload.get("artifact_ref"),
                                "revision": payload.get("prd_revision")},
                     "role": payload.get("role")}})


def fail_interrupted_evaluation(state_dir: Path, writer: EventWriter, request: ZfEvent) -> None:
    """Never supersede a live owner or rerun an already dispatched request."""
    request_id = str((request.payload or {}).get("request_id") or "")
    if not request_id:
        return
    try:
        with evaluation_execution_lock(state_dir, request_id):
            events = [row for row in writer.event_log.read_all()
                      if (row.payload or {}).get("request_id") == request_id]
            if any(row.type in TERMINAL_EVALUATION_EVENTS for row in events):
                return
            dispatch = next((row for row in reversed(events) if row.type in {
                "channel.prd_evaluation.dispatched", "output.evaluation.started"}), None)
            if dispatch is not None:
                evaluation_failure(writer, request, reason="evaluation_execution_interrupted; explicitly retry",
                                   causation_id=dispatch.id)
    except TimeoutError:
        # The owning process/thread still holds the execution lock.
        return
