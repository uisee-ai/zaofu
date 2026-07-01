"""Tests for F-WEB-MVP-01: zf web CLI subcommand."""

from __future__ import annotations

import argparse
import os
import sys
from unittest import mock

import pytest

from zf.cli import web as cli_web


@pytest.fixture(autouse=True)
def _isolate_environ():
    # cli_web.run() -> _load_env_file() 直接写 os.environ;不隔离会把
    # ZF_WEB_ACTION_TOKEN 等泄漏进后续 test_web_server,翻转 token-gating
    # 断言(2026-06-12 triage:mutation_enabled True!=False)。快照即还原。
    with mock.patch.dict(os.environ):
        yield


def test_register_adds_subcommand():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    cli_web.register(sub)
    args = parser.parse_args(["web", "--help"]) if False else None
    # parse a normal invocation (not --help, which would sys.exit)
    args = parser.parse_args(["web"])
    assert args.command == "web"
    assert args.host == "127.0.0.1"  # default
    assert args.port == 8001          # default
    assert args.reload is False
    assert args.state_dir is None
    assert args.workspace_only is False


def test_register_accepts_overrides():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    cli_web.register(sub)
    args = parser.parse_args([
        "web", "--host", "0.0.0.0", "--port", "9999", "--reload",
        "--state-dir", "/tmp/foo", "--workspace-only",
    ])
    assert args.host == "0.0.0.0"
    assert args.port == 9999
    assert args.reload is True
    assert args.state_dir == "/tmp/foo"
    assert args.workspace_only is True


def test_load_env_file_sets_missing_keys_without_overriding(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "# local secrets",
            "ZF_WEB_ACTION_TOKEN=stable-token",
            "ZF_WEB_PASSCODE='local pass'",
            "export ZF_KANBAN_AGENT_BACKEND=codex-headless",
        ]),
        encoding="utf-8",
    )
    # _load_env_file writes os.environ directly; monkeypatch.delenv on a
    # missing key registers no undo, so without an explicit snapshot the
    # written ZF_WEB_ACTION_TOKEN leaks process-wide and flips
    # token-gating assertions in test_web_server.py (2026-06-12 triage).
    with mock.patch.dict(os.environ):
        os.environ.pop("ZF_WEB_ACTION_TOKEN", None)
        os.environ.pop("ZF_KANBAN_AGENT_BACKEND", None)
        os.environ["ZF_WEB_PASSCODE"] = "already-set"

        loaded = cli_web._load_env_file(env_file)

        assert loaded == {
            "ZF_WEB_ACTION_TOKEN": "stable-token",
            "ZF_KANBAN_AGENT_BACKEND": "codex-headless",
        }
        assert os.environ["ZF_WEB_ACTION_TOKEN"] == "stable-token"
        assert os.environ["ZF_WEB_PASSCODE"] == "already-set"
        assert os.environ["ZF_KANBAN_AGENT_BACKEND"] == "codex-headless"


def test_run_missing_state_dir_returns_2(tmp_path, capsys):
    args = argparse.Namespace(
        host="127.0.0.1", port=8001, state_dir=str(tmp_path / "nope"),
        reload=False,
    )
    rc = cli_web.run(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "not found" in captured.err


def test_run_default_state_dir_uses_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        host="127.0.0.1", port=8001, state_dir=None, reload=False,
        workspace_only=False,
    )
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def blocked(name, *a, **kw):
        if name == "uvicorn":
            raise ImportError("blocked for test")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", blocked)
    sys.modules.pop("uvicorn", None)
    rc = cli_web.run(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "web dependencies not installed" in captured.err
    assert "not found" not in captured.err


def test_run_uvicorn_missing_returns_2(tmp_path, monkeypatch, capsys):
    """If `uvicorn` import fails, surface a clear install hint."""
    sd = tmp_path / ".zf"
    sd.mkdir()
    args = argparse.Namespace(
        host="127.0.0.1", port=8001, state_dir=str(sd), reload=False,
        workspace_only=False,
    )
    # Force ImportError by removing uvicorn from sys.modules and
    # blocking re-import.
    import sys
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def blocked(name, *a, **kw):
        if name == "uvicorn":
            raise ImportError("blocked for test")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", blocked)
    sys.modules.pop("uvicorn", None)
    rc = cli_web.run(args)
    assert rc == 2
    assert "web dependencies not installed" in capsys.readouterr().err


def test_run_default_state_dir_uses_project_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: test\n  state_dir: runtime-state\n'
    )
    (tmp_path / "runtime-state").mkdir()
    args = argparse.Namespace(
        host="127.0.0.1", port=8001, state_dir=None, reload=False,
        workspace_only=False,
    )

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def blocked(name, *a, **kw):
        if name == "uvicorn":
            raise ImportError("blocked for test")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", blocked)
    sys.modules.pop("uvicorn", None)
    rc = cli_web.run(args)

    assert rc == 2
    err = capsys.readouterr().err
    assert "web dependencies not installed" in err
    assert "not found" not in err


def test_resolve_web_context_loads_config_for_explicit_state_dir(tmp_path, monkeypatch):
    project = tmp_path / "target"
    state_dir = project / "runtime-state"
    state_dir.mkdir(parents=True)
    (project / "zf.yaml").write_text(
        'version: "1.0"\n'
        'project:\n'
        '  name: target\n'
        '  state_dir: runtime-state\n'
        'roles:\n'
        '  - name: orchestrator\n'
        '    backend: codex\n',
        encoding="utf-8",
    )
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    monkeypatch.chdir(launcher)
    args = argparse.Namespace(
        host="127.0.0.1",
        port=8001,
        state_dir=str(state_dir),
        reload=False,
        workspace_only=False,
    )

    context = cli_web._resolve_web_context(args)

    assert context.project_root == project
    assert context.state_dir == state_dir
    assert context.config is not None
    assert context.config.roles[0].instance_id == "orchestrator"
