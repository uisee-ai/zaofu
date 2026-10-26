"""Linked experiment workers keep evidence hooks out of the shared checkout."""
from __future__ import annotations
import json
import subprocess
import tomllib
from pathlib import Path

from zf.runtime.codex_hooks import write_codex_hook_settings
from zf.runtime.codex_worktree_hooks import configure_linked_worktree_hooks


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    shared = tmp_path / "shared"
    shared.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=shared, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-qm", "baseline"], cwd=shared, check=True)
    linked = tmp_path / "linked"
    subprocess.run(["git", "worktree", "add", "--detach", str(linked)],
                   cwd=shared, check=True, capture_output=True)
    return shared, linked


def test_linked_home_hooks_and_trust_are_private_and_repeatable(tmp_path: Path) -> None:
    shared, linked = _repo(tmp_path)
    state = linked / "custom-state"
    home = state / "workdirs/dev/codex-home"
    home.mkdir(parents=True)
    (home / "config.toml").write_text('model = "test-model"\n')
    write_codex_hook_settings(state, project_root=linked)
    for _ in range(2):
        configure_linked_worktree_hooks(state, project_root=linked, worker_root=linked, codex_home=home)
    config = tomllib.loads((home / "config.toml").read_text())
    hooks = json.loads((home / "hooks.json").read_text())["hooks"]
    assert len(hooks) == 5
    assert len(config["hooks"]["state"]) == 5
    assert config["model"] == "test-model"
    assert all(str(state) in rows[0]["hooks"][0]["command"] for rows in hooks.values())
    assert not (shared / ".codex").exists()


def test_inherited_bridge_disabled_only_in_private_home(tmp_path: Path) -> None:
    shared, linked = _repo(tmp_path)
    write_codex_hook_settings(shared / "other-state", project_root=shared)
    inherited = shared / ".codex/hooks.json"
    data = json.loads(inherited.read_text())
    data["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": "echo user-hook"})
    inherited.write_text(json.dumps(data))
    original = inherited.read_bytes()
    home = linked / "private-home"
    home.mkdir()
    (home / "config.toml").write_text("")
    write_codex_hook_settings(linked / ".state", project_root=linked)
    configure_linked_worktree_hooks(linked / ".state", project_root=linked, worker_root=linked, codex_home=home)
    states = tomllib.loads((home / "config.toml").read_text())["hooks"]["state"]
    assert sum(v.get("enabled") is False for v in states.values()) == 5
    assert f"{inherited}:stop:0:1" not in states
    assert inherited.read_bytes() == original


def test_actual_spawn_preparation_wires_linked_home(tmp_path: Path, monkeypatch) -> None:
    from zf.core.config.schema import RoleConfig
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime.spawn_coordinator import SpawnCoordinator
    shared, linked = _repo(tmp_path)
    state = linked / "custom-state"
    state.mkdir()
    coordinator = SpawnCoordinator(state_dir=state, registry=RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(linked)),
                                   transport=None, project_root=str(linked))
    home = coordinator._prepare_codex_home(RoleConfig(name="reader", backend="codex"), hook_project_root=linked)
    assert (home / "hooks.json").is_file()
    assert not (shared / ".codex").exists()
