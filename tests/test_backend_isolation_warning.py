"""EVAL-BACKEND-ISOLATION-001 — backend isolation warning."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.cli.validate import _print_backend_isolation_check
from zf.core.config.schema import RoleConfig, ZfConfig


def _make_role(name: str, backend: str) -> RoleConfig:
    role = RoleConfig(name=name, backend=backend, role_kind="auto")
    role.instance_id = name
    return role


def test_no_builder_role_skips_check(capsys) -> None:
    config = ZfConfig(roles=[_make_role("orchestrator", "claude-code")])
    _print_backend_isolation_check(config)
    out = capsys.readouterr().out
    assert "no dev/builder role found" in out


def test_all_different_backend_clean_pass(capsys) -> None:
    config = ZfConfig(roles=[
        _make_role("dev", "claude-code"),
        _make_role("review", "codex"),
        _make_role("test", "codex"),
        _make_role("judge", "claude-code"),  # 这个会撞
    ])
    _print_backend_isolation_check(config)
    out = capsys.readouterr().out
    assert "⚠ judge and dev/builder use same backend" in out


def test_clean_full_isolation(capsys) -> None:
    config = ZfConfig(roles=[
        _make_role("dev", "claude-code"),
        _make_role("review", "codex"),
        _make_role("test", "codex"),
        _make_role("judge", "codex"),
    ])
    _print_backend_isolation_check(config)
    out = capsys.readouterr().out
    assert "✓ adversarial roles use different backend" in out
    assert "⚠" not in out


def test_all_same_backend_warns_for_each_adversary(capsys) -> None:
    config = ZfConfig(roles=[
        _make_role("dev", "claude-code"),
        _make_role("review", "claude-code"),
        _make_role("test", "claude-code"),
        _make_role("judge", "claude-code"),
        _make_role("critic", "claude-code"),
    ])
    _print_backend_isolation_check(config)
    out = capsys.readouterr().out
    assert out.count("⚠") == 4  # review + test + judge + critic
    assert "self-confirmation bias" in out


def test_builder_alias_recognized(capsys) -> None:
    """The role named 'builder' (Ralph-style) also counts as builder."""
    config = ZfConfig(roles=[
        _make_role("builder", "claude-code"),
        _make_role("review", "claude-code"),
    ])
    _print_backend_isolation_check(config)
    out = capsys.readouterr().out
    assert "⚠ review and dev/builder use same backend" in out


def test_case_insensitive_backend_compare(capsys) -> None:
    """Backend comparison is case-insensitive."""
    config = ZfConfig(roles=[
        _make_role("dev", "Claude-Code"),
        _make_role("review", "claude-code"),
    ])
    _print_backend_isolation_check(config)
    out = capsys.readouterr().out
    assert "⚠ review and dev/builder use same backend" in out


def test_warning_only_does_not_set_exit_code() -> None:
    """The check prints warnings but never raises — it's diagnostic
    output only, doesn't fail validate."""
    config = ZfConfig(roles=[
        _make_role("dev", "claude-code"),
        _make_role("review", "claude-code"),
    ])
    # Should not raise
    _print_backend_isolation_check(config)
