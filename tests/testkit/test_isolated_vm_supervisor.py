"""Single owned-child contracts; no QEMU, provider or shared runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from tests.testkit.isolated_vm_supervisor import OwnedProcessSupervisor, process_identity


REPO = Path(__file__).resolve().parents[2]
CHILD = """
import signal, time
from pathlib import Path
def ignored(signum, frame):
    Path('term-seen').write_text(str(signum))
signal.signal(signal.SIGTERM, ignored)
Path('ready').write_text('ready')
time.sleep(20)
"""


def _wait_for(path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline
        time.sleep(0.02)


@pytest.mark.parametrize("mode", ["sigterm", "sigint", "repeated", "timeout", "parent_sigkill"])
def test_interruption_cleans_only_owned_child_without_false_pass(tmp_path: Path, mode: str) -> None:
    witness = subprocess.Popen(
        ["/bin/sleep", "120"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    root = tmp_path / "supervisor"
    supervisor = subprocess.Popen(
        [sys.executable, "-m", "tests.testkit.isolated_vm_supervisor",
         "--root", str(root), "--timeout", "1" if mode == "timeout" else "15",
         "--cleanup-timeout", "0.6", "--", sys.executable, "-I", "-c", CHILD],
        cwd=REPO,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_for(root / "supervisor-report.json")
        _wait_for(root / "ready")
        started = json.loads((root / "supervisor-report.json").read_text())
        owned_pid = started["pid"]
        assert started["verdict"] == "harness_failure" and not started["final"]
        assert process_identity(owned_pid)[0] == started["proc_starttime"]
        if mode == "parent_sigkill":
            supervisor.kill()
        elif mode != "timeout":
            supervisor.send_signal(signal.SIGINT if mode == "sigint" else signal.SIGTERM)
            if mode == "repeated":
                _wait_for(root / "term-seen")
                supervisor.send_signal(signal.SIGTERM)
        assert supervisor.wait(timeout=10) != 0
        report = json.loads((root / "supervisor-report.json").read_text())
        assert report["verdict"] == "harness_failure"
        if mode == "parent_sigkill":
            deadline = time.monotonic() + 3
            while True:
                identity = process_identity(owned_pid)
                if identity is None or identity[0] != started["proc_starttime"] or identity[1] == "Z":
                    break
                assert time.monotonic() < deadline, "guarded child survived parent death"
                time.sleep(0.02)
            # SIGKILL cannot write a final report or claim wait()/reaping.
            assert report == started
            assert report["final"] is False and "cleanup_ok" not in report
            assert witness.poll() is None
            return
        assert report["interrupted"] is (mode != "timeout")
        assert report["reason"] == ("timeout" if mode == "timeout" else
                                    "signal 2" if mode == "sigint" else "signal 15")
        assert report["signal_count"] == (2 if mode == "repeated" else
                                           0 if mode == "timeout" else 1)
        assert report["actions"] == ["terminate", "kill"]
        assert report["returncode"] == -signal.SIGKILL
        assert report["cleanup_ok"] is True
        assert report["owned_child_reaped"] is True and report["final"] is True
        assert not Path(f"/proc/{owned_pid}").exists()
        assert len(report["log_sha256"]) == 64
        assert witness.poll() is None, "unrelated witness was killed"
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait(timeout=5)
        witness.terminate()
        witness.wait(timeout=5)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_normal_exit_and_cleanup_idempotence(tmp_path: Path, exit_code: int) -> None:
    supervisor = OwnedProcessSupervisor(
        tmp_path / "owned", (sys.executable, "-I", "-c", f"raise SystemExit({exit_code})"),
    )
    report = supervisor.run(timeout=5)
    assert report["returncode"] == exit_code
    assert report["actions"] == []
    assert report["verdict"] == ("pass" if exit_code == 0 else "harness_failure")
    before = supervisor.report_path.read_bytes()
    assert supervisor.cleanup(reason="must-not-rewrite") == report
    assert supervisor.report_path.read_bytes() == before


@pytest.mark.parametrize("phase", ["starting", "started"])
def test_signal_at_startup_report_boundary_keeps_handle(tmp_path: Path, phase: str) -> None:
    class InterruptedStart(OwnedProcessSupervisor):
        def _write_report(self) -> None:
            if self._report["status"] == phase:
                os.kill(os.getpid(), signal.SIGTERM)
            super()._write_report()

    previous = signal.getsignal(signal.SIGTERM)
    supervisor = InterruptedStart(tmp_path / "owned", ("/bin/sleep", "20"))
    report = supervisor.run(timeout=5)
    assert signal.getsignal(signal.SIGTERM) == previous
    assert report["cleanup_ok"] and report["verdict"] == "harness_failure"
    assert report["reason"] == "signal 15"
    assert report["signal_count"] == 1


def test_started_report_failure_still_reaps_child(tmp_path: Path) -> None:
    class BrokenReport(OwnedProcessSupervisor):
        def _write_report(self) -> None:
            if self._report["status"] == "started":
                raise OSError("injected started-report failure")
            super()._write_report()

    supervisor = BrokenReport(tmp_path / "owned", ("/bin/sleep", "20"))
    with pytest.raises(OSError, match="injected"):
        supervisor.run(timeout=5)
    report = json.loads(supervisor.report_path.read_text())
    assert report["reason"] == "startup_failed"
    assert report["verdict"] == "harness_failure" and report["cleanup_ok"]
    assert supervisor.process.returncode is not None


def test_wrong_parent_never_executes_target(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    result = subprocess.run(
        [sys.executable, "-I", str(REPO / "tests/testkit/isolated_vm_exec.py"),
         str(os.getpid() + 1000000), sys.executable, "-c",
         f"from pathlib import Path; Path({str(marker)!r}).touch()"],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode != 0 and not marker.exists()
    assert "parent changed" in result.stderr
