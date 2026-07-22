from __future__ import annotations

import os
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


def test_discover_source_root_still_finds_checkout() -> None:
    assert discover_source_root() is not None
