"""External guard sensors, direct-child interruption and cleanup boundaries."""

import shutil
import subprocess
import sys

import pytest

from tests.testkit.memray_guard import CaptureBudget, inspect_budget, preflight_budget, wait_for_capture


@pytest.mark.parametrize("field,value", [
    ("max_capture_bytes", 0), ("min_free_bytes", 0),
    ("max_capture_bytes", float("nan")), ("min_free_bytes", True),
    ("poll_seconds", float("nan")), ("cleanup_seconds", float("inf")),
])
def test_guard_rejects_invalid_policy_before_execution(tmp_path, field, value):
    with pytest.raises(ValueError):
        CaptureBudget(tmp_path / "memray.bin", **{field: value})


@pytest.mark.parametrize("damage", ["large", "disk", "symlink"])
def test_guard_sensor_rejects_bounds_and_symlink(tmp_path, monkeypatch, damage):
    capture = tmp_path / "capture.bin"
    capture.write_bytes(b"1234")
    if damage == "disk":
        real = shutil.disk_usage(tmp_path)
        monkeypatch.setattr(shutil, "disk_usage", lambda _: real._replace(free=0))
    elif damage == "symlink":
        link = tmp_path / "link"
        link.symlink_to(capture)
        with pytest.raises(ValueError, match="symlink"):
            inspect_budget(CaptureBudget(link, min_free_bytes=1))
        return
    result = inspect_budget(CaptureBudget(capture, max_capture_bytes=1 if damage == "large" else 8,
                                         min_free_bytes=1))
    assert result["errors"] == ["capture_size" if damage == "large" else "disk_free"]


def test_guard_preflight_refuses_existing_capture(tmp_path):
    capture = tmp_path / "memray.bin"
    capture.write_bytes(b"old evidence")
    with pytest.raises(ValueError, match="fresh capture"):
        preflight_budget(CaptureBudget(capture))
    assert capture.read_bytes() == b"old evidence"


@pytest.mark.parametrize("fault", ["disk_free", "sensor", "missing_capture"])
def test_guard_missing_evidence_or_sensor_failure_cannot_pass(tmp_path, monkeypatch, fault):
    from tests.testkit import memray_guard

    if fault != "missing_capture":
        def inspect(_budget):
            if fault == "sensor":
                raise OSError("sensor unavailable")
            return {"capture_bytes": 0, "free_bytes": 0, "errors": ["disk_free"]}
        monkeypatch.setattr(memray_guard, "inspect_budget", inspect)
    child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    try:
        child.wait(timeout=5)
        result = wait_for_capture(child, budget=CaptureBudget(tmp_path / "absent.bin",
                                  min_free_bytes=1), timeout_seconds=5)
        assert result["violation"]["kind"] == (
            "budget" if fault == "disk_free" else fault
        )
        assert not result["interrupt_sent"]  # Never signal an already reaped PID.
        assert child.returncode == 0
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_guard_timeout_escalates_when_owned_child_ignores_interrupt(tmp_path):
    ready = tmp_path / "ready"
    child = subprocess.Popen([
        sys.executable, "-c",
        "import signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        f"Path({str(ready)!r}).write_bytes(b'ready'); time.sleep(30)",
    ], start_new_session=True)
    try:
        # The capture's appearance is the child's signal-handler readiness marker.
        import time
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            assert child.poll() is None
            time.sleep(.01)
        assert ready.exists()
        result = wait_for_capture(
            child, budget=CaptureBudget(ready, min_free_bytes=1, poll_seconds=.01,
                                         cleanup_seconds=.05), timeout_seconds=.05,
        )
        assert result["violation"] == {"kind": "timeout"}
        assert result["interrupt_sent"] and result["escalated"]
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
