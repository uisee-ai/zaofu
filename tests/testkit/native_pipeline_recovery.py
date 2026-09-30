"""One owned native Provider interruption; no synthetic recovery/terminal events."""

from __future__ import annotations

import json
import ctypes
import os
from pathlib import Path
import signal
import time

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.session_tailer import claude_session_path


def transcript_rows(path: Path) -> list[dict]:
    """A live transcript may end with an incomplete line; never treat it as proof."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _owned_claude_pid(tmux, instance: str, workdir: Path) -> int:
    probe = tmux.pane_process_probe(instance, max_entries=64)
    assert probe["available"], "owned pane process tree unavailable"
    pids = {str(probe["pane_pid"]), *(str(row["pid"]) for row in probe["processes"])}
    matches = []
    for raw in pids:
        if not raw.isdecimal() or int(raw) <= 1:
            continue
        proc = Path("/proc") / raw
        try:
            command = (proc / "comm").read_text().strip()
            cwd = (proc / "cwd").resolve(strict=True)
        except FileNotFoundError:
            continue
        if command in {"claude", "claude-code"} and cwd == workdir.resolve():
            matches.append(int(raw))
    assert len(matches) == 1, f"expected one Claude in owned pane/workdir; found {matches}"
    return matches[0]


def _kill_owned_provider(pid: int) -> None:
    _signal_owned_provider(pid, signal.SIGKILL)


def _stop_owned_provider(pid: int) -> None:
    _signal_owned_provider(pid, signal.SIGSTOP)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        state = (Path("/proc") / str(pid) / "status").read_text()
        if any(line.startswith("State:") and line.split()[1] in {"T", "t"}
               for line in state.splitlines()):
            return
        time.sleep(0.01)
    raise AssertionError("owned Provider did not enter stopped state")


def _signal_owned_provider(pid: int, signum: int) -> None:
    # Only the two explicit fault modes used by owned native test workers.
    assert pid > 1 and signum in {signal.SIGKILL, signal.SIGSTOP}
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        descriptor = os.pidfd_open(pid)
        try:
            signal.pidfd_send_signal(descriptor, signum)
        finally:
            os.close(descriptor)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    libc.pidfd_open.argtypes = [ctypes.c_int, ctypes.c_uint]
    libc.pidfd_open.restype = ctypes.c_int
    libc.pidfd_send_signal.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    libc.pidfd_send_signal.restype = ctypes.c_int
    descriptor = libc.pidfd_open(pid, 0)
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), "pidfd_open failed")
    try:
        if libc.pidfd_send_signal(descriptor, signum, None, 0) != 0:
            raise OSError(ctypes.get_errno(), "pidfd_send_signal failed")
    finally:
        os.close(descriptor)


class NativePipelineRecovery:
    """Fault injection plus oracle around the real CLI watcher and Run Manager."""

    def __init__(self, runtime, tmux, stage: str, record_property):
        assert stage in {"impl", "verify"}
        libc = ctypes.CDLL(None, use_errno=True)
        if not (hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")):
            assert hasattr(libc, "pidfd_open") and hasattr(libc, "pidfd_send_signal"), (
                "native fault injection requires Linux pidfd support"
            )
        self.runtime, self.tmux, self.stage = runtime, tmux, stage
        self.record = record_property
        self.old = None
        self.old_pids: set[str] = set()
        self.fault: dict = {}

    def poll(self, contexts, events) -> None:
        if self.old is not None:
            # Buffered pre-kill usage can renew the lease on the next tick.
            # Keep the clock input expired only for this killed, still-sent attempt.
            store = TaskAttemptStore(self.runtime.state_dir / "task_attempts.json")
            attempt = store.get(self.old.attempt_id)
            if (attempt and attempt["status"] == "sent"
                    and attempt["lease_expires_at"] != "2000-01-01T00:00:00+00:00"):
                store.renew_lease(
                    self.old.attempt_id, updated_at="2000-01-01T00:00:00+00:00",
                    lease_expires_at="2000-01-01T00:00:00+00:00",
                )
            return
        selected = [c for c in contexts if c.task_pipeline_stage == self.stage]
        if not selected:
            return
        assert len(selected) == 1, "replacement dispatched before fault injection"
        context = selected[0]
        store = TaskAttemptStore(self.runtime.state_dir / "task_attempts.json")
        attempt = store.get(context.attempt_id)
        assert attempt and attempt["status"] == "sent", "missed active interruption window"
        dispatches = [e for e in events if e.type == "task.pipeline.stage.dispatched"
                      and e.payload.get("attempt_id") == context.attempt_id]
        if not dispatches:
            return  # send_task precedes the durable stage-dispatched event.
        [dispatch] = dispatches
        workdir = Path(dispatch.payload["workdir"])
        registry = RoleSessionRegistry(
            self.runtime.state_dir / "role_sessions.yaml",
            project_root=str(self.runtime.project_root),
        )
        binding = registry.task_stage_bindings()[context.task_stage_session_binding]
        transcript = claude_session_path(str(workdir), binding["session_id"])
        assistants = [r for r in transcript_rows(transcript) if r.get("type") == "assistant"]
        if not assistants:
            return
        # Proof of a real in-flight provider response, not just a spawned CLI PID.
        assert any(r.get("sessionId") == binding["session_id"] for r in assistants)
        assert not any(e.type == "workflow.operation.settled"
                       and e.payload.get("operation_id") == context.operation_id for e in events)
        pid = _owned_claude_pid(self.tmux, context.instance_id, workdir)
        probe = self.tmux.pane_process_probe(context.instance_id, max_entries=64)
        self.old_pids = {str(probe["pane_pid"]), *(str(r["pid"]) for r in probe["processes"])}
        # pidfd pins this exact process, so PID reuse cannot target another worker.
        _kill_owned_provider(pid)
        # Only the clock input is accelerated. The production expiry sweep,
        # Run Manager decision, respawn consumer and redrive admission stay real.
        store.renew_lease(
            context.attempt_id, updated_at="2000-01-01T00:00:00+00:00",
            lease_expires_at="2000-01-01T00:00:00+00:00",
        )
        self.old = context
        self.fault = {
            "stage": self.stage, "provider_pid": pid,
            "owned_pids": sorted(self.old_pids), "session_id": binding["session_id"],
            "transcript": str(transcript), "assistant_count": len(assistants),
            "attempt_id": context.attempt_id, "operation_id": context.operation_id,
            "dispatch_id": context.dispatch_id, "fault": "owned-provider-SIGKILL",
            "lease_expiry": "injected-clock-input; production recovery unchanged",
        }
        self.record("native_interruption", json.dumps(self.fault))

    def verify(self, contexts, events) -> list:
        assert self.old is not None, "no native interruption was injected"
        old = self.old
        same_stage = [c for c in contexts if c.task_pipeline_stage == self.stage]
        assert len(same_stage) == 2
        first, second = same_stage
        assert first == old
        assert second.operation_id == old.operation_id
        assert second.attempt_id != old.attempt_id
        assert second.dispatch_id != old.dispatch_id
        assert second.lease_id != old.lease_id
        assert second.placement_epoch > old.placement_epoch
        rows = TaskAttemptStore(self.runtime.state_dir / "task_attempts.json")
        prior, current = rows.get(old.attempt_id), rows.get(second.attempt_id)
        assert prior["status"] == "superseded" and prior["superseded_by"] == second.attempt_id
        assert current["status"] == "succeeded"
        requests = [e for e in events if e.type == "worker.respawn.requested"
                    and e.payload.get("attempt_id") == old.attempt_id]
        assert len(requests) == 1
        [request] = requests
        assert request.actor == "run-manager"
        [completed] = [e for e in events if e.type == "worker.respawn.completed"
                       and e.causation_id == request.id]
        [redrive] = [e for e in events if e.type == "workflow.operation.redrive_admitted"
                     and e.payload.get("source_attempt_id") == old.attempt_id]
        assert redrive.payload["recovery_decision_event_id"] == request.id
        [dispatch] = [e for e in events if e.type == "task.pipeline.stage.dispatched"
                      and e.payload.get("attempt_id") == second.attempt_id]
        ids = [e.id for e in events]
        assert ids.index(request.id) < ids.index(completed.id) < ids.index(redrive.id) < ids.index(dispatch.id)
        assert not any(e.type in {"dev.build.done", "task.pipeline.verify.completed"}
                       and e.payload.get("attempt_id") == old.attempt_id for e in events)
        remaining = [pid for pid in self.old_pids if Path(f"/proc/{pid}").exists()]
        assert not remaining, f"old owned processes survived recovery: {remaining}"
        self.record("native_recovery", json.dumps({
            **self.fault, "new_attempt_id": second.attempt_id,
            "request_id": request.id, "completed_id": completed.id, "redrive_id": redrive.id,
            "old_attempt_status": prior["status"], "new_attempt_status": current["status"],
        }))
        return [c for c in contexts if c.attempt_id != old.attempt_id]
