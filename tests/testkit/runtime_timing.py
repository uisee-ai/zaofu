"""Opt-in timing of real CLI calls; diagnostic only, never a verdict source."""

from __future__ import annotations

from contextlib import ExitStack
from functools import wraps
from itertools import count
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from unittest.mock import patch


class CallTimer:
    def __init__(self, stream) -> None:
        self.stream = stream
        self.ids = count(1)
        self.local = threading.local()
        self.lock = threading.Lock()

    def wrap(self, name, function):
        @wraps(function)
        def timed(*args, **kwargs):
            parent = getattr(self.local, "call_id", None)
            call_id = next(self.ids)
            self.local.call_id = call_id
            started, cpu = time.perf_counter(), time.thread_time()
            error = None
            result = None
            try:
                result = function(*args, **kwargs)
                return result
            except BaseException as exc:
                error = type(exc).__name__
                raise
            finally:
                row = {
                    "schema_version": "testkit-runtime-timing.v1",
                    "pid": os.getpid(), "thread": threading.get_ident(),
                    "call_id": call_id, "parent_id": parent, "name": name,
                    "end_unix_seconds": time.time(),
                    "wall_seconds": time.perf_counter() - started,
                    "thread_cpu_seconds": time.thread_time() - cpu, "error": error,
                }
                self.local.call_id = parent
                if name == "_safe_housekeeping":
                    row["phase"] = args[1]
                if name == "run_once":
                    events = kwargs.get("events", args[1] if len(args) > 1 else None)
                    row["event_types"] = None if events is None else [e.type for e in events]
                # Record only fixed probe nonces and diagnostic scalars, never
                # commands, screen bodies, or the process-probe argv.
                if name == "send_keys":
                    text = kwargs.get("text", args[2] if len(args) > 2 else "")
                    probe = re.fullmatch(
                        r"printf '%s%s%s\\n' __ZF_SHELL_READY_ ([0-9]{1,20}) '__'", text,
                    )
                    row["shell_nonce"] = probe.group(1) if probe else None
                elif name == "capture_pane" and isinstance(result, str):
                    row["screen_chars"] = len(result)
                    row["shell_markers"] = re.findall(r"__ZF_SHELL_READY_([0-9]{1,20})__", result)
                elif name == "_record_readiness_failure" and isinstance(result, dict):
                    row["readiness"] = {key: result[key] for key in (
                        "failure_class", "pane_alive", "shell_submit_attempts", "launch_attempts",
                    ) if key in result}
                with self.lock:
                    self.stream.write(json.dumps(row) + "\n")
                    self.stream.flush()
        return timed


def main() -> None:
    output = Path(sys.argv[1])
    sys.argv = ["zf", *sys.argv[2:]]
    from zf.cli.main import main as cli_main
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.watcher import EventWatcher
    from zf.runtime.transport import TmuxTransport
    from zf.runtime.tmux import TmuxSession

    with output.open("x") as stream, ExitStack() as stack:
        timer = CallTimer(stream)
        for owner, methods in (
            (Orchestrator, ("run_once", "_capture_logs", "_react_to_events",
                            "_reconcile_pending_handoffs", "_handle_worker_action_requests",
                            "_autoscale_workers", "_dispatch_ready", "_sweep_feature_liveness",
                            "_safe_housekeeping", "_runtime_active_role_configs",
                            "_active_task_for_instance", "_active_fanout_child_for_instance",
                            "_latest_dispatched_per_task")),
            (EventWatcher, ("poll_once",)),
            # `_capture_logs` delegates to these provider/host probes.  Keep
            # this opt-in and diagnostic-only so the normal runtime has no
            # wrapper or timing side effect.
            (TmuxTransport, ("is_alive", "capture_log", "_pane_process_probe",
                             "_wait_for_shell_handshake", "_record_readiness_failure")),
            (TmuxSession, ("_run", "pane_alive", "capture_pane", "send_keys",
                           "pane_current_command")),
        ):
            for name in methods:
                stack.enter_context(patch.object(owner, name, timer.wrap(name, getattr(owner, name))))
        raise SystemExit(cli_main())


if __name__ == "__main__":
    main()
