from __future__ import annotations

import os

from zf.runtime.cli_command import (
    default_zf_cli_cmd,
    discover_source_root,
    set_default_zf_cli_cmd,
    zf_cli_cmd,
)


def test_zf_cli_cmd_uses_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("ZF_CLI_CMD", "uv --project /repo run zf")

    assert zf_cli_cmd() == "uv --project /repo run zf"


def test_set_default_zf_cli_cmd_prefers_source_checkout(monkeypatch) -> None:
    monkeypatch.delenv("ZF_CLI_CMD", raising=False)
    monkeypatch.setattr("zf.runtime.cli_command.shutil.which", lambda name: "/usr/bin/uv")

    try:
        command = set_default_zf_cli_cmd()

        assert command == zf_cli_cmd()
        assert command.startswith("uv --project ")
        assert command.endswith(" run zf")
        assert discover_source_root() is not None
    finally:
        os.environ.pop("ZF_CLI_CMD", None)


def test_default_zf_cli_cmd_falls_back_without_uv(monkeypatch) -> None:
    monkeypatch.setattr("zf.runtime.cli_command.shutil.which", lambda name: None)

    assert default_zf_cli_cmd() == "zf"
