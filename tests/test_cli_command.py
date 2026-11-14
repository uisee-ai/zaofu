from __future__ import annotations

import os
import subprocess
from pathlib import Path

from zf.runtime.cli_command import (
    default_zf_cli_cmd,
    discover_source_root,
    set_default_zf_cli_cmd,
    zf_cli_cmd,
)


def test_zf_cli_cmd_uses_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")

    assert zf_cli_cmd() == "uv --project /repo run zf"


def test_set_default_zf_cli_cmd_binds_runtime_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_bin = tmp_path / "frozen-venv" / "bin"
    runtime_bin.mkdir(parents=True)
    python = runtime_bin / "python"
    cli = runtime_bin / "zf"
    python.touch()
    cli.touch()
    monkeypatch.setenv("ZF_CLI_CMD", "")
    monkeypatch.setenv("PATH", "/home/user/.local/bin:/usr/bin")
    monkeypatch.setattr("zf.runtime.cli_command.sys.executable", str(python))

    command = set_default_zf_cli_cmd()

    assert command == str(cli)
    assert command == zf_cli_cmd()
    assert os.environ["PATH"].split(os.pathsep)[0] == str(runtime_bin)


def test_default_zf_cli_cmd_falls_back_to_runtime_python(
    tmp_path: Path,
    monkeypatch,
) -> None:
    python = tmp_path / "isolated" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr("zf.runtime.cli_command.sys.executable", str(python))

    assert default_zf_cli_cmd() == f"{python} -m zf.cli.main"


def test_explicit_cli_still_binds_subprocess_python_to_runtime(tmp_path, monkeypatch):
    runtime_bin = tmp_path / "runtime" / "bin"
    system_bin = tmp_path / "system" / "bin"
    for directory, label in ((runtime_bin, "runtime"), (system_bin, "system")):
        directory.mkdir(parents=True)
        python = directory / "python3"
        python.write_text(f"#!/bin/sh\nprintf '%s' '{label}'\n")
        python.chmod(0o755)
    monkeypatch.setattr("zf.runtime.cli_command.sys.executable", str(runtime_bin / "python"))
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /explicit run zf")
    monkeypatch.setenv("PATH", str(system_bin))

    for _ in range(2):
        assert set_default_zf_cli_cmd() == "uv --project /explicit run zf"
    assert zf_cli_cmd() == "uv --project /explicit run zf"
    assert subprocess.check_output(["python3"], text=True) == "runtime"
    assert os.environ["PATH"].split(os.pathsep) == [str(runtime_bin), str(system_bin)]


def test_discover_source_root_still_finds_checkout() -> None:
    assert discover_source_root() is not None
