from pathlib import Path
import base64
import hashlib
import json
import os
import subprocess
import sys

import pytest

from tests.testkit import isolated_vm_guest
from tests.testkit.isolated_vm_scenario import restore_runtime
from tests.testkit.general_route import GeneralRoute
from tests.testkit.live_general_runtime import LiveGeneralRuntime
from tests.testkit.evidence import sha256_file


@pytest.mark.parametrize("value", [b"partial output", "partial output", None])
def test_admission_timeout_retains_output_and_original_failure(tmp_path, monkeypatch, capsys, value):
    from tests.testkit.isolated_vm_scenario import _run_admission
    failure = subprocess.TimeoutExpired(["cli"], 30, output=value, stderr=b"stack trace")
    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 30
        raise failure
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        _run_admission(["cli"], project=tmp_path, env={}, output=tmp_path)
    assert raised.value is failure
    expected = ("partial output" if value else "") + "stack trace"
    assert (tmp_path / "admission.log").read_text() == expected
    assert expected in capsys.readouterr().err


@pytest.mark.parametrize("exit_code", [0, 7])
def test_vm_cli_wrapper_preserves_real_module_argv_output_and_exit(tmp_path, exit_code):
    from tests.testkit import isolated_vm_cli
    package = tmp_path / "zf/cli"
    package.mkdir(parents=True)
    (tmp_path / "zf/__init__.py").touch()
    (package / "__init__.py").touch()
    (package / "main.py").write_text(
        "import json, sys\nprint(json.dumps(sys.argv[1:]))\n"
        f"print('diagnostic', file=sys.stderr)\nraise SystemExit({exit_code})\n"
    )
    result = subprocess.run(
        [sys.executable, str(Path(isolated_vm_cli.__file__).resolve()),
         "workflow", "start", "--apply", "argument with spaces"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == exit_code
    assert json.loads(result.stdout) == ["workflow", "start", "--apply", "argument with spaces"]
    assert result.stderr == "diagnostic\n"


def test_vm_cli_timer_is_diagnostic_only_and_cancelled_on_failure(monkeypatch):
    from tests.testkit import isolated_vm_cli
    calls = []
    monkeypatch.setattr(isolated_vm_cli.faulthandler, "dump_traceback_later",
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(isolated_vm_cli.faulthandler, "cancel_dump_traceback_later",
                        lambda: calls.append("cancel"))
    def run(module, **kwargs):
        assert module == "zf.cli.main" and kwargs == {"run_name": "__main__", "alter_sys": True}
        raise SystemExit(7)
    monkeypatch.setattr(isolated_vm_cli.runpy, "run_module", run)
    with pytest.raises(SystemExit) as raised:
        isolated_vm_cli.main()
    assert raised.value.code == 7
    assert calls == [((10,), {"repeat": True, "exit": False}), "cancel"]


def _command(tmp_path: Path) -> list[str]:
    script = tmp_path / "guest-command.py"
    script.write_text(
        "import os, pathlib; pathlib.Path('command-env').write_text(os.environ['ZF_K571_GUEST_MODE'])\n",
        encoding="utf-8",
    )
    return ["/usr/bin/env", "python3", str(script)]


def test_firstboot_records_identity_and_runs_explicit_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(isolated_vm_guest, "_boot_id", lambda: "boot-a")
    result = isolated_vm_guest.run_guest(
        mode="firstboot", root=tmp_path, nonce="n-1", command=_command(tmp_path),
    )
    state = isolated_vm_guest._read_json(tmp_path / "guest-observation/state.json")
    assert result.returncode == 0
    assert state["phase"] == "firstboot_complete"
    assert state["first_boot_id"] == "boot-a"
    assert (tmp_path / "command-env").read_text() == "firstboot"


def test_restore_requires_firstboot_and_new_boot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(isolated_vm_guest, "_boot_id", lambda: "boot-a")
    with pytest.raises(RuntimeError, match="valid firstboot marker"):
        isolated_vm_guest.run_guest(mode="restore", root=tmp_path, nonce="n-1", command=_command(tmp_path))
    isolated_vm_guest.run_guest(mode="firstboot", root=tmp_path, nonce="n-1", command=_command(tmp_path))
    with pytest.raises(RuntimeError, match="new guest boot_id"):
        isolated_vm_guest.run_guest(mode="restore", root=tmp_path, nonce="n-1", command=_command(tmp_path))


def test_restore_preserves_nonce_and_rejects_control_plane_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(isolated_vm_guest, "_boot_id", lambda: "boot-a")
    isolated_vm_guest.run_guest(mode="firstboot", root=tmp_path, nonce="n-1", command=_command(tmp_path))
    monkeypatch.setattr(isolated_vm_guest, "_boot_id", lambda: "boot-b")
    with pytest.raises(RuntimeError, match="nonce"):
        isolated_vm_guest.run_guest(mode="restore", root=tmp_path, nonce="wrong", command=_command(tmp_path))
    with pytest.raises(ValueError, match="full startup"):
        isolated_vm_guest.run_guest(
            mode="restore", root=tmp_path, nonce="n-1",
            command=["/usr/bin/python3", "-m", "zf.cli.main", "start", "--control-plane-only"],
        )


def test_failed_firstboot_cannot_become_restore_eligible(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(isolated_vm_guest, "_boot_id", lambda: "boot-a")
    result = isolated_vm_guest.run_guest(
        mode="firstboot", root=tmp_path, nonce="n-1", command=["/usr/bin/false"],
    )
    assert result.returncode != 0
    with pytest.raises(RuntimeError, match="completed firstboot"):
        monkeypatch.setattr(isolated_vm_guest, "_boot_id", lambda: "boot-b")
        isolated_vm_guest.run_guest(mode="restore", root=tmp_path, nonce="n-1", command=_command(tmp_path))


def test_restore_succeeds_once_without_repeating_firstboot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(isolated_vm_guest, "_boot_id", lambda: "boot-a")
    command = _command(tmp_path)
    isolated_vm_guest.run_guest(mode="firstboot", root=tmp_path, nonce="n-1", command=command)
    with pytest.raises(RuntimeError, match="bootstrap replay"):
        isolated_vm_guest.run_guest(mode="firstboot", root=tmp_path, nonce="n-1", command=command)
    monkeypatch.setattr(isolated_vm_guest, "_boot_id", lambda: "boot-b")
    result = isolated_vm_guest.run_guest(mode="restore", root=tmp_path, nonce="n-1", command=command)
    assert result.returncode == 0 and (tmp_path / "command-env").read_text() == "restore"
    state = isolated_vm_guest._read_json(tmp_path / "guest-observation/state.json")
    assert state["first_boot_id"] == "boot-a" and state["restore_boot_id"] == "boot-b"
    with pytest.raises(RuntimeError, match="completed firstboot"):
        isolated_vm_guest.run_guest(mode="restore", root=tmp_path, nonce="n-1", command=command)


def test_runtime_restore_does_not_bootstrap_or_modify_canonical_bytes(tmp_path: Path, monkeypatch) -> None:
    project, output = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    output.mkdir()
    route = GeneralRoute(project, monkeypatch)
    runtime = LiveGeneralRuntime(route, output, Path(__file__).resolve().parents[2])
    ledger = route.state / "events.jsonl"
    if not ledger.exists():
        ledger.touch()
    prefix = ledger.read_bytes()
    checkpoint = {
        "state_dir": str(route.state), "session": runtime.session,
        "config_sha256": sha256_file(project / "zf.yaml"),
        "worker_sha256": sha256_file(output / "mock-bin/cat"),
        "ledger_bytes": len(prefix), "ledger_sha256": hashlib.sha256(prefix).hexdigest(),
        "ledger_base64": base64.b64encode(prefix).decode(),
    }
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    def forbidden(*_a, **_kw):
        pytest.fail("restore called a bootstrap constructor")
    monkeypatch.setattr(GeneralRoute, "__init__", forbidden)
    monkeypatch.setattr(LiveGeneralRuntime, "__init__", forbidden)
    restored = restore_runtime(tmp_path, checkpoint, Path(__file__).resolve().parents[2])
    assert restored.route.state == route.state and restored.process is None
    assert restored.pids == set(), "old boot PIDs must not authorize cleanup"
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    checkpoint["ledger_sha256"] = "0" * 64
    with pytest.raises(AssertionError, match="prefix lost"):
        restore_runtime(tmp_path, checkpoint, Path(__file__).resolve().parents[2])
