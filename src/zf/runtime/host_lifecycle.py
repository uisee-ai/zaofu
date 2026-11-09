"""Read-only restart eligibility for an explicitly installed host supervisor."""

from pathlib import Path

from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.terminal_events import latest_quiescent_run_terminal


def host_restart_eligibility(state_dir: Path, events) -> dict:
    reason = ""
    if (state_dir / "shutdown-requested").exists():
        reason = "shutdown_in_progress"
    elif any(event.type == "event.malformed" and event.payload.get("ledger_integrity") == "unknown" for event in events):
        reason = "event_ledger_integrity_unknown"
    elif is_dispatch_paused(state_dir, events=events):
        reason = "dispatch_paused"
    elif latest_quiescent_run_terminal(events) is not None:
        reason = "run_quiescent"
    else:
        lifecycle = [event for event in events if event.type in {
            "session.started", "loop.started", "loop.resume_requested", "workflow.invoke",
            "loop.shutdown_requested", "loop.stopped", "runtime.stop.requested", "run.cancelled",
        }]
        if not lifecycle:
            reason = "no_authorized_runtime"
        elif lifecycle[-1].type in {"loop.shutdown_requested", "loop.stopped", "runtime.stop.requested", "run.cancelled"}:
            reason = "stop_intent"
    return {"eligible": not reason, "reason": reason or "unexpected_exit_recovery"}
