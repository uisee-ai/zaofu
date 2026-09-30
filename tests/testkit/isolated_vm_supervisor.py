"""Bounded single-child execution for the explicit isolated-VM host tier.

Callers must use non-daemonizing commands without descendants; this is not a
replacement for the runner's process-tree cleanup. Only the creating main
thread may poll, wait or signal this child.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Mapping, Sequence

from tests.testkit.evidence import sha256_file, write_json_atomic


def process_identity(pid: int) -> tuple[str, str] | None:
    """Return (starttime, state), not an authority to signal a numeric PID."""
    try:
        suffix = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return suffix[19], suffix[0]
    except FileNotFoundError:
        return None


@dataclass
class OwnedProcessSupervisor:
    root: Path
    command: Sequence[str]
    cleanup_timeout: float = 2.0
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root).absolute()
        self.command = tuple(self.command)
        self.process: subprocess.Popen[str] | None = None
        self.log_path = self.root / "owned-process.log"
        self.report_path = self.root / "supervisor-report.json"
        self.stopping: int | None = None
        self.signal_count = 0
        self._cleaned = False
        self._cleaning = False
        self._report: dict = {}

    def note_signal(self, signum: int, _frame: object = None) -> None:
        # Never raise or perform I/O: a signal may arrive inside Popen/cleanup.
        self.stopping = self.stopping or signum
        self.signal_count += 1

    def start(self) -> subprocess.Popen[str]:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("single creating main thread required")
        if self.process is not None or self._report:
            raise RuntimeError("supervisor already started")
        if not self.command or not Path(self.command[0]).is_absolute():
            raise ValueError("explicit absolute executable required")
        if any(not isinstance(part, str) or not part for part in self.command):
            raise ValueError("non-empty command arguments required")
        if "-daemonize" in self.command:
            raise ValueError("daemonizing QEMU is not owned by this supervisor")
        if not math.isfinite(self.cleanup_timeout) or self.cleanup_timeout <= 0:
            raise ValueError("finite positive cleanup timeout required")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        wrapper = Path(__file__).with_name("isolated_vm_exec.py").resolve()
        self._report = {
            "schema_version": "zf-testkit-owned-process-supervisor.v2",
            "status": "starting", "verdict": "harness_failure", "final": False,
            "command": list(self.command),
            "command_sha256": hashlib.sha256("\0".join(self.command).encode()).hexdigest(),
            "wrapper_sha256": sha256_file(wrapper),
            "supervisor_sha256": sha256_file(Path(__file__)),
            "expected_parent": os.getpid(),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "log": str(self.log_path),
        }
        self._write_report()
        try:
            with self.log_path.open("x", encoding="utf-8") as log:
                self.process = subprocess.Popen(
                    [sys.executable, "-I", str(wrapper), str(os.getpid()), *self.command],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    cwd=self.root,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C", **self.environment},
                    start_new_session=True, text=True,
                )
            identity = process_identity(self.process.pid)
            self._report.update(status="started", pid=self.process.pid,
                                proc_starttime=identity[0] if identity else None)
            self._write_report()
            return self.process
        except BaseException:
            if self.process is not None:
                self.cleanup(reason="startup_failed")
            raise

    def cleanup(self, *, reason: str) -> dict:
        if self._cleaned:
            return dict(self._report)
        if self._cleaning:
            raise RuntimeError("cleanup re-entered")
        if self.process is None:
            raise RuntimeError("supervisor has not started")
        process = self.process
        self._cleaning = True
        actions: list[str] = []
        try:
            if process.poll() is None:
                # Same unreaped direct child: never restore a handle from a PID file.
                identity = process_identity(process.pid)
                expected = self._report.get("proc_starttime")
                if expected is not None and (identity is None or identity[0] != expected):
                    raise RuntimeError("owned child identity mismatch")
                process.terminate()
                actions.append("terminate")
                try:
                    process.wait(timeout=self.cleanup_timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    actions.append("kill")
                    process.wait(timeout=self.cleanup_timeout)
            else:
                process.wait()
            self._report.update(
                status="cleaned", final=True, reason=reason,
                interrupted=bool(self.stopping), signal_count=self.signal_count,
                returncode=process.returncode, actions=actions, cleanup_ok=True,
                owned_child_reaped=True, log_sha256=sha256_file(self.log_path),
                verdict=("pass" if reason == "normal_exit" and process.returncode == 0
                         and self.stopping is None else "harness_failure"),
            )
            self._write_report()
            self._cleaned = True
            return dict(self._report)
        finally:
            self._cleaning = False

    def _write_report(self) -> None:
        write_json_atomic(self.report_path, self._report)

    def run(self, *, timeout: float) -> dict:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("finite positive run timeout required")
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        for sig in previous:
            signal.signal(sig, self.note_signal)
        reason = "startup_failed"
        deadline = time.monotonic() + timeout
        try:
            process = self.start()
            while True:
                if self.stopping is not None:
                    reason = f"signal {self.stopping}"
                    break
                if time.monotonic() >= deadline:
                    reason = "timeout"
                    break
                if process.poll() is not None:
                    reason = "normal_exit"
                    break
                time.sleep(0.02)
        finally:
            try:
                if self.process is not None:
                    self.cleanup(reason=reason)
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        return dict(self._report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--cleanup-timeout", type=float, default=2)
    parser.add_argument("--qemu-module-dir")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    environment = {"QEMU_MODULE_DIR": args.qemu_module_dir} if args.qemu_module_dir else {}
    report = OwnedProcessSupervisor(args.root, command, args.cleanup_timeout, environment).run(
        timeout=args.timeout,
    )
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
