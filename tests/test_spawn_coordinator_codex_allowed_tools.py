"""1231-T2: when a codex role is configured with allowed_tools (which
Codex's permission model does not support — no tool allowlist), the
SpawnCoordinator should emit `worker.spawn_warning` so the user learns
the setting is silently ignored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import RoleConfig
from zf.core.events.log import EventLog
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.spawn_coordinator import SpawnCoordinator


class _StubTransport:
    def __init__(self) -> None:
        self.spawns: list[tuple[str, list[str]]] = []

    def spawn(self, role: RoleConfig, argv: list[str], *, cwd=None) -> None:
        self.spawns.append((role.instance_id, argv))


def _make_coordinator(tmp_path: Path) -> tuple[SpawnCoordinator, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    coord = SpawnCoordinator(
        state_dir=state_dir,
        registry=registry,
        transport=_StubTransport(),
        project_root=str(tmp_path),
        event_log=log,
    )
    return coord, log


def test_codex_with_allowed_tools_emits_warning(tmp_path: Path):
    coord, log = _make_coordinator(tmp_path)
    role = RoleConfig(
        name="dev",
        backend="codex",
        permission_mode="bypass",
        allowed_tools=["Bash(pytest *)", "Read"],
    )
    coord.spawn(role)

    warnings = [
        e for e in log.read_all()
        if e.type == "worker.spawn_warning"
        and (e.payload or {}).get("code") == "codex_ignores_allowed_tools"
    ]
    assert warnings, (
        "codex role with allowed_tools must emit codex_ignores_allowed_tools "
        "warning — Codex has no tool allowlist concept"
    )


def test_codex_without_allowed_tools_does_not_warn(tmp_path: Path):
    coord, log = _make_coordinator(tmp_path)
    role = RoleConfig(
        name="dev",
        backend="codex",
        permission_mode="bypass",
        allowed_tools=[],
    )
    coord.spawn(role)

    warnings = [
        e for e in log.read_all()
        if e.type == "worker.spawn_warning"
        and (e.payload or {}).get("code") == "codex_ignores_allowed_tools"
    ]
    assert not warnings, (
        f"unexpected warning when allowed_tools is empty: {warnings}"
    )


def test_claude_with_allowed_tools_does_not_warn(tmp_path: Path):
    """Guard: Claude uses allowed_tools correctly, must not warn."""
    coord, log = _make_coordinator(tmp_path)
    role = RoleConfig(
        name="orchestrator",
        backend="claude-code",
        permission_mode="allowlist",
        allowed_tools=["Bash(zf *)", "Read"],
    )
    coord.spawn(role)

    warnings = [
        e for e in log.read_all()
        if e.type == "worker.spawn_warning"
        and (e.payload or {}).get("code") == "codex_ignores_allowed_tools"
    ]
    assert not warnings
