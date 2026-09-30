"""Panic probe verdict and private QMP protocol; never trigger a real panic."""

import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest
import yaml

from tests.testkit.isolated_vm_panic_probe import (
    _QmpRecorder, _seed_user_data, panic_evidence, run_panic_probe,
)


def evidence():
    identity = {"nonce": "test", "boot_id": "guest-boot", "interfaces": ["lo"],
                "panic_timeout": 0, "crash_loaded": False}
    serial = "ZF_K590_PANIC_ARMED " + json.dumps(identity) + "\n"
    serial += "Kernel panic - not syncing: sysrq triggered crash\n"
    rows = [{"QMP": {"version": {}}}, {"return": {}, "id": "caps"},
            {"event": "GUEST_PANICKED", "data": {"action": "pause"}},
            {"event": "SHUTDOWN", "data": {"reason": "guest-panic"}}]
    return serial, rows


def test_complete_panic_evidence_is_not_just_command_exit():
    serial, rows = evidence()
    assert panic_evidence(serial, "\n".join(map(json.dumps, rows)), "test")


@pytest.mark.parametrize("damage", ["no_qmp", "no_caps", "no_panic", "no_shutdown",
                                   "wrong_nonce", "only_cmdline", "malformed", "wrong_shape"])
def test_incomplete_panic_proof_fails_closed(damage):
    serial, rows = evidence()
    qmp = "\n".join(map(json.dumps, rows))
    if damage == "no_qmp":
        qmp = ""
    elif damage in {"no_caps", "no_panic", "no_shutdown"}:
        rows.pop({"no_caps": 1, "no_panic": 2, "no_shutdown": 3}[damage])
        qmp = "\n".join(map(json.dumps, rows))
    elif damage == "wrong_nonce":
        serial = serial.replace('"test"', '"foreign"')
    elif damage == "only_cmdline":
        serial = serial.replace("Kernel panic - not syncing: sysrq triggered crash", "panic=-1")
    elif damage == "malformed":
        qmp += '\n{"event":'
    elif damage == "wrong_shape":
        qmp += "\n[]"
    assert not panic_evidence(serial, qmp, "test")


def test_qmp_path_handshake_fragmented_event_and_archive(tmp_path):
    path = tmp_path / "qmp.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        server.listen(1)
        recorder = _QmpRecorder(path, tmp_path / "qmp.jsonl")
        recorder.start()
        connection, _ = server.accept()
        with connection:
            connection.settimeout(1)
            connection.sendall(b'{"QMP": {}}\r\n')
            recorder.poll()
            assert json.loads(connection.recv(4096)) == {"execute": "qmp_capabilities", "id": "caps"}
            connection.sendall(b'{"return":{},"id":"caps"}\r\n{"event":"GUEST_')
            recorder.poll()
            connection.sendall(b'PANICKED"}\r\n')
            recorder.poll()
        assert not recorder.poll()
        recorder.stop()
    assert [json.loads(line) for line in recorder.output.read_text().splitlines()] == [
        {"QMP": {}}, {"return": {}, "id": "caps"}, {"event": "GUEST_PANICKED"},
    ]


def test_guest_script_refuses_foreign_machine_before_any_write(monkeypatch):
    script = yaml.safe_load(_seed_user_data("a" * 32))["bootcmd"][0][-1]
    compile(script, "<guest-only>", "exec")
    reads = []

    def fake_path(path):
        reads.append(path)
        assert path == "/sys/class/dmi/id/product_uuid"
        return SimpleNamespace(read_text=lambda: "foreign-machine")

    monkeypatch.setitem(sys.modules, "pathlib", SimpleNamespace(Path=fake_path))
    with pytest.raises(AssertionError):
        exec(script, {})
    assert reads == ["/sys/class/dmi/id/product_uuid"]


def test_probe_rejects_non_private_root_before_creating_it(tmp_path):
    root = tmp_path / "foreign"
    with pytest.raises(ValueError, match="unique /tmp"):
        run_panic_probe(root, Path("/missing-tools"))
    assert not root.exists()


def test_host_caller_is_opt_in_and_rejects_failed_probe(monkeypatch):
    from tests.conformance.test_zaofu_testkit_s4_isolated_vm_recovery_host import (
        test_private_guest_panic_capability_only as caller,
    )
    from tests.testkit import isolated_vm_panic_probe as module
    monkeypatch.delenv('ZF_RUN_VM_PANIC_PROBE', raising=False)
    with pytest.raises(pytest.skip.Exception):
        caller(lambda *_: None)
    monkeypatch.setenv('ZF_RUN_VM_PANIC_PROBE', '1')
    monkeypatch.setenv('ZF_VM_TOOL_ROOT', '/private-tools')
    calls = []
    def probe(root, tools):
        assert root.parent == Path('/tmp') and root.name.startswith('zf-k590-panic-')
        assert tools == Path('/private-tools')
        calls.append(root)
        return {'verdict': 'harness_failure', 'cleanup_ok': True}
    monkeypatch.setattr(module, 'run_panic_probe', probe)
    with pytest.raises(AssertionError):
        caller(lambda *_: None)
    assert len(calls) == 1
