"""Tests for per-role transport routing via make_transport."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    OrchestratorConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.runtime.transport import (
    CompositeTransport,
    TmuxTransport,
    make_transport,
)
from zf.runtime.transport_stream_json import StreamJsonTransport


def _config(roles: list[RoleConfig]) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=".zf"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=roles,
    )


def test_default_transport_is_tmux_for_all_roles(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    cfg = _config([RoleConfig(name="dev"), RoleConfig(name="review")])
    transport = make_transport(cfg, dry_run=True)
    assert isinstance(transport, CompositeTransport)
    assert isinstance(transport.for_role("dev"), TmuxTransport)
    assert isinstance(transport.for_role("review"), TmuxTransport)


def test_per_role_stream_json_opt_in(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    cfg = _config([
        RoleConfig(name="dev", backend="claude-code", transport="tmux"),
        RoleConfig(name="critic", backend="claude-code", transport="stream-json"),
    ])
    transport = make_transport(cfg, dry_run=True)
    assert isinstance(transport.for_role("dev"), TmuxTransport)
    assert isinstance(transport.for_role("critic"), StreamJsonTransport)


def test_make_transport_registers_stream_json_role_config(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    cfg = ZfConfig(
        project=ProjectConfig(name="test", state_dir=".zf"),
        session=SessionConfig(tmux_session="test-zf"),
        orchestrator=OrchestratorConfig(max_turns=23, transport_timeout_s=45.0),
        roles=[
            RoleConfig(
                name="orchestrator",
                backend="claude-code",
                transport="stream-json",
                permission_mode="allowlist",
                allowed_tools=["Read", "Bash(zf events *)"],
                model="claude-sonnet-4-6",
            ),
        ],
    )

    transport = make_transport(cfg, dry_run=True)
    stream_json = transport.for_role("orchestrator")

    assert isinstance(stream_json, StreamJsonTransport)
    registered = stream_json._roles["orchestrator"]
    assert registered.allowed_tools == ["Read", "Bash(zf events *)"]
    assert registered.permission_mode == "allowlist"
    assert registered.model == "claude-sonnet-4-6"
    assert stream_json.is_alive("orchestrator") is True


def test_send_task_routes_to_correct_underlying(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    cfg = _config([
        RoleConfig(name="dev", backend="claude-code", transport="tmux"),
        RoleConfig(name="critic", backend="claude-code", transport="stream-json"),
    ])
    transport = make_transport(cfg, dry_run=True)
    # Send to a tmux role: the tmux backend's command_log records send-keys.
    # Send to a stream-json role: would invoke claude_code_sdk — but in this
    # test we just verify routing dispatched to the right concrete class.
    tmux = transport.for_role("dev")
    sj = transport.for_role("critic")
    assert isinstance(tmux, TmuxTransport)
    assert isinstance(sj, StreamJsonTransport)
    # Different concrete instances per role
    assert tmux is not sj


def test_init_initializes_each_underlying_transport(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    cfg = _config([
        RoleConfig(name="dev", backend="claude-code", transport="tmux"),
        RoleConfig(name="critic", backend="claude-code", transport="stream-json"),
    ])
    transport = make_transport(cfg, dry_run=True)
    transport.init()  # must not raise
    assert (tmp_path / ".zf" / "locks" / "sessions").exists()  # stream-json init


def test_attach_handle_uses_role_specific_transport(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    cfg = _config([
        RoleConfig(name="dev", backend="claude-code", transport="tmux"),
        RoleConfig(name="critic", backend="claude-code", transport="stream-json"),
    ])
    transport = make_transport(cfg, dry_run=True)
    # tmux role: tmux attach argv
    tmux_handle = transport.attach_handle("dev")
    assert "tmux" in tmux_handle.argv[0]
    # stream-json role: log tail argv
    sj_handle = transport.attach_handle("critic")
    assert sj_handle.argv[0] in ("less", "tail")
