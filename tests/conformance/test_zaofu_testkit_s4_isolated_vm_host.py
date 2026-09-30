"""Opt-in diskless paused-QEMU caller; not guest/OS recovery acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from tests.testkit.evidence import sha256_file, write_json_atomic


pytestmark = [pytest.mark.host, pytest.mark.serial]
REPO = Path(__file__).resolve().parents[2]


def test_paused_diskless_qemu_is_cleaned_when_supervisor_receives_sigterm(record_property):
    if os.environ.get("ZF_RUN_VM_SUPERVISOR") != "1":
        pytest.skip("set ZF_RUN_VM_SUPERVISOR=1 and ZF_VM_TOOL_ROOT for isolated QEMU")
    base = Path(os.environ["ZF_VM_TOOL_ROOT"]).resolve()
    assert base.parent == Path("/tmp") and base.name.startswith("zf-k571-vm-")
    assert os.access("/dev/kvm", os.R_OK | os.W_OK), "KVM unavailable to current user"
    qemu = base / "root/usr/bin/qemu-system-x86_64"
    libs = base / "root/usr/lib/x86_64-linux-gnu"
    firmware = base / "root/usr/share/seabios/bios-256k.bin"
    preflight = base / "preflight-report.json"
    assert json.loads(preflight.read_text())["verdict"] == "pass"
    root = Path(tempfile.mkdtemp(prefix="zf-k571-cleanup-20260925-"))
    qmp = root / "qmp.sock"
    evidence = {
        "scope": "diskless-paused-QEMU-supervisor-SIGTERM-only", "verdict": "fail",
        "qemu_sha256": sha256_file(qemu), "firmware_sha256": sha256_file(firmware),
        "preflight_sha256": sha256_file(preflight), "root": str(root),
    }
    argv = [
        "/lib64/ld-linux-x86-64.so.2", "--library-path", str(libs), str(qemu),
        "-machine", "q35", "-accel", "kvm", "-cpu", "host", "-smp", "1", "-m", "1536",
        "-S", "-nodefaults", "-display", "none", "-vga", "none", "-monitor", "none",
        "-nic", "none", "-L", str(base / "root/usr/share/qemu"), "-bios", str(firmware),
        "-qmp", f"unix:{qmp},server=on,wait=off",
        "-sandbox", "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny",
    ]
    witness = subprocess.Popen(["/bin/sleep", "60"], start_new_session=True)
    with (root / "supervisor.log").open("w") as log:
        supervisor = subprocess.Popen(
            [sys.executable, "-m", "tests.testkit.isolated_vm_supervisor",
             "--root", str(root / "owned"), "--timeout", "25", "--cleanup-timeout", "3",
             "--qemu-module-dir", str(libs / "qemu"), "--", *argv],
            cwd=REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 15
        while not qmp.exists():
            assert supervisor.poll() is None, (root / "supervisor.log").read_text()
            assert time.monotonic() < deadline, "QMP startup timeout"
            time.sleep(0.02)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(qmp))
            with connection.makefile("rwb") as channel:
                evidence["greeting"] = json.loads(channel.readline())
                for command in ("qmp_capabilities", "query-status", "query-kvm", "query-block"):
                    channel.write((json.dumps({"execute": command, "id": command}) + "\n").encode())
                    channel.flush()
                    while True:
                        reply = json.loads(channel.readline())
                        if reply.get("id") == command:
                            assert "return" in reply, reply
                            evidence[command] = reply["return"]
                            break
        assert evidence["query-status"]["running"] is False
        assert evidence["query-status"]["status"] == "prelaunch"
        assert evidence["query-kvm"]["enabled"] is True
        assert evidence["query-block"] == []
        supervisor.send_signal(signal.SIGTERM)
        assert supervisor.wait(timeout=10) == 1
        report_path = root / "owned/supervisor-report.json"
        report = json.loads(report_path.read_text())
        evidence["supervisor"] = report
        evidence["supervisor_report_sha256"] = sha256_file(report_path)
        assert report["final"] and report["cleanup_ok"] and report["owned_child_reaped"]
        assert report["interrupted"] and report["reason"] == "signal 15"
        assert report["verdict"] == "harness_failure", "cleaned interruption is not normal success"
        assert report["actions"] == ["terminate"]
        assert not Path(f'/proc/{report["pid"]}').exists()
        assert witness.poll() is None, "unrelated process affected"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as check:
            check.settimeout(1)
            with pytest.raises((FileNotFoundError, ConnectionRefusedError)):
                check.connect(str(qmp))
        evidence["verdict"] = "pass"
    finally:
        if supervisor.poll() is None:
            supervisor.terminate()
            try:
                supervisor.wait(timeout=6)
            except subprocess.TimeoutExpired:
                supervisor.kill()
                supervisor.wait(timeout=3)
        witness.terminate()
        witness.wait(timeout=3)
        evidence["supervisor_exit"] = supervisor.returncode
        evidence["witness_exit"] = witness.returncode
        path = root / "host-cleanup.json"
        digest = write_json_atomic(path, evidence)
        record_property("vm_supervisor_evidence", json.dumps(evidence, sort_keys=True))
        record_property("vm_supervisor_report_path", str(path))
        record_property("vm_supervisor_report_sha256", digest)
