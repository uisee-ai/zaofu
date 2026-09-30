"""Role-local hook trust stays wired through the public spawn entrypoint."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from unittest.mock import Mock

from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.codex_runtime_hook_trust import _write_codex_runtime_hook_trust
from zf.runtime.spawn_coordinator import SpawnCoordinator
from zf.runtime.transport import TransportAdapter


def test_spawn_rewrites_only_role_local_hook_trust_and_repeats_idempotently(
    tmp_path: Path, monkeypatch,
) -> None:
    provider_home = tmp_path / "provider-home"
    global_codex = provider_home / ".codex"
    global_codex.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: provider_home))
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda value: str(provider_home / value[2:]) if value.startswith("~/") else value,
    )
    project_root = tmp_path / 'project "quoted"'
    project_root.mkdir()
    state_dir = tmp_path / "custom-state"
    hook_key = f"{project_root}/.codex/hooks.json:stop:0:0"
    global_config = global_codex / "config.toml"
    global_config.write_text(
        'model = "test-model"\n'
        f'[hooks.state.{json.dumps(hook_key)}]\n'
        'trusted_hash = "old-hash"\n'
        '[hooks.state."unrelated-hook"]\n'
        'trusted_hash = "keep-hash"\n',
        encoding="utf-8",
    )
    original_global = global_config.read_bytes()
    # Hash compatibility is a separate provider sensor; this test checks the
    # writer's actual spawn caller with a controlled, non-secret hash input.
    monkeypatch.setattr(
        "zf.runtime.spawn_coordinator.codex_hook_trust_states",
        lambda *_: [(hook_key, "new-hash")],
    )
    transport = Mock(spec=TransportAdapter)
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(project_root),
    )
    coordinator = SpawnCoordinator(
        state_dir=state_dir, registry=registry, transport=transport,
        project_root=str(project_root),
    )
    role = RoleConfig(name="dev", backend="codex")
    coordinator.spawn(role, cwd=project_root)

    role_home = state_dir / "workdirs" / "dev" / "codex-home"
    runtime_config = role_home / "config.toml"
    first = runtime_config.read_bytes()
    parsed = tomllib.loads(first.decode())
    assert parsed["model"] == "test-model"
    assert parsed["projects"][str(project_root)]["trust_level"] == "trusted"
    assert parsed["hooks"]["state"] == {
        hook_key: {"trusted_hash": "new-hash"},
        "unrelated-hook": {"trusted_hash": "keep-hash"},
    }
    assert f"CODEX_HOME={role_home}" in transport.spawn.call_args.args[1]
    assert transport.spawn.call_args.kwargs["cwd"] == project_root
    coordinator.spawn(role, cwd=project_root)
    assert runtime_config.read_bytes() == first
    assert global_config.read_bytes() == original_global
    assert transport.spawn.call_count == 2


def test_writer_replaces_managed_hash_without_duplicate_toml_tables(tmp_path: Path) -> None:
    config_path = tmp_path / "runtime" / "config.toml"
    project_root = tmp_path / "project"
    hook_key = f"{project_root}/.codex/hooks.json:stop:0:0"
    for trusted_hash in ("first", "second"):
        _write_codex_runtime_hook_trust(
            config_path, project_root=project_root,
            hook_states=[(hook_key, trusted_hash)],
        )
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
        assert parsed["hooks"]["state"] == {hook_key: {"trusted_hash": trusted_hash}}
        assert parsed["projects"] == {str(project_root): {"trust_level": "trusted"}}
