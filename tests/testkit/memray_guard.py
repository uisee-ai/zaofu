"""Observe a runner-owned pytest process independently of allocation hooks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import shutil
import signal
import subprocess
import time

from tests.testkit.cleanup import terminate_process_group


@dataclass(frozen=True)
class CaptureBudget:
    capture: Path
    max_capture_bytes: int = 2 * 1024**3
    min_free_bytes: int = 4 * 1024**3
    poll_seconds: float = 0.25
    cleanup_seconds: float = 60

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0
               for value in (self.max_capture_bytes, self.min_free_bytes)):
            raise ValueError("capture and free-space budgets must be positive")
        if not 0 < self.poll_seconds <= 5 or not 0 < self.cleanup_seconds <= 60:
            raise ValueError("poll/cleanup intervals must be bounded")

    def policy(self) -> dict:
        return {**asdict(self), "capture": str(self.capture.resolve()),
                "scope": "sampled external capture/disk guard, not a filesystem hard quota"}


def inspect_budget(budget: CaptureBudget) -> dict:
    root = budget.capture.parent
    while not root.exists():
        root = root.parent
    if budget.capture.is_symlink():
        raise ValueError("capture must not be a symlink")
    size = budget.capture.stat().st_size if budget.capture.exists() else 0
    free = shutil.disk_usage(root).free
    errors = []
    if size > budget.max_capture_bytes:
        errors.append("capture_size")
    if free < budget.min_free_bytes:
        errors.append("disk_free")
    return {"capture_bytes": size, "free_bytes": free, "errors": errors}


def preflight_budget(budget: CaptureBudget) -> None:
    if budget.capture.exists() or budget.capture.is_symlink():
        raise ValueError("guard requires a fresh capture")
    if inspect_budget(budget)["errors"]:
        raise ValueError("capture filesystem fails guard preflight")


def wait_for_capture(
    process: subprocess.Popen, *, budget: CaptureBudget, timeout_seconds: float,
) -> dict:
    """Poll the direct unreaped child; request cleanup before escalation."""
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 3600:
        raise ValueError("guard timeout must be finite and bounded")
    started = time.monotonic()
    checks = 0
    last = {}
    peak_capture_bytes = 0
    minimum_free_bytes = None
    violation = None
    interrupt_sent = False
    escalated = False
    try:
        while True:
            exit_code = process.poll()
            try:
                last = inspect_budget(budget)
                checks += 1
                peak_capture_bytes = max(peak_capture_bytes, last["capture_bytes"])
                minimum_free_bytes = min(
                    last["free_bytes"], minimum_free_bytes
                    if minimum_free_bytes is not None else last["free_bytes"]
                )
                if last["errors"]:
                    violation = {"kind": "budget", "errors": last["errors"]}
            except (OSError, ValueError) as exc:
                violation = {"kind": "sensor", "error": f"{type(exc).__name__}: {exc}"}
            if violation is None and time.monotonic() - started > timeout_seconds:
                violation = {"kind": "timeout"}
            if violation is not None or exit_code is not None:
                break
            time.sleep(budget.poll_seconds)
        if violation is not None and process.poll() is None:
            # Popen is the sole reaper. Do not signal a PID read from a file.
            process.send_signal(signal.SIGINT)
            interrupt_sent = True
            try:
                process.wait(timeout=budget.cleanup_seconds)
            except subprocess.TimeoutExpired:
                escalated = True
                terminate_process_group(process, timeout=1)
        if violation is None and last["capture_bytes"] == 0:
            violation = {"kind": "missing_capture"}
    finally:
        if process.poll() is None:
            terminate_process_group(process, timeout=1)
    return {
        "schema_version": "zf-testkit-capture-guard.v1", "policy": budget.policy(),
        "pid": process.pid, "timeout_seconds": timeout_seconds, "checks": checks,
        "peak_capture_bytes": peak_capture_bytes, "minimum_free_bytes": minimum_free_bytes,
        "last": last, "elapsed_seconds": time.monotonic() - started,
        "child_returncode": process.returncode, "violation": violation,
        "interrupt_sent": interrupt_sent, "escalated": escalated,
    }
