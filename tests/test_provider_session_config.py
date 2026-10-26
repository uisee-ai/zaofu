from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import (
    ProviderSessionConfig,
    RoleConfig,
    RoleLifecycleConfig,
)
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.runtime.backend import ClaudeCodeAdapter, CodexAdapter
from zf.runtime.preflight import run_preflight_checks
from zf.runtime.provider_session_config import resolve_effective_provider_session


def _config(tmp_path: Path, role: dict) -> Path:
    path = tmp_path / "zf.yaml"
    path.write_text(
        yaml.safe_dump({
            "version": "1.0",
            "project": {
                "name": "provider-session-test",
                "state_dir": ".zf-test",
            },
            "session": {"tmux_session": "provider-session-test"},
            "roles": [role],
        }),
        encoding="utf-8",
    )
    return path


def test_provider_session_omitted_keeps_legacy_argv() -> None:
    claude = RoleConfig(name="impl", backend="claude-code", agent="worker")
    codex = RoleConfig(name="verify", backend="codex")

    assert "--effort" not in ClaudeCodeAdapter().build_command(claude)
    assert ClaudeCodeAdapter().build_command(claude)[-3:] == [
        "--agent",
        "worker",
        "--verbose",
    ]
    assert not any(
        "model_reasoning_effort" in item
        or "max_concurrent_threads_per_session" in item
        for item in CodexAdapter().build_command(codex)
    )


@pytest.mark.parametrize("backend", ["codex", "claude-code"])
def test_native_delegation_opt_out_is_applied_to_provider_and_snapshot(tmp_path, backend):
    path = _config(tmp_path, {"name": "executor", "backend": backend,
                              "provider_session": {"native_delegation": False}})
    role = load_config(path).roles[0]
    adapter = CodexAdapter() if backend == "codex" else ClaudeCodeAdapter()
    argv = adapter.build_command(role)
    if backend == "codex":
        assert [argv[i + 1] for i, arg in enumerate(argv) if arg == "--disable"] == [
            "multi_agent", "multi_agent_v2"]
    else:
        offset = argv.index("--disallowedTools")
        assert argv[offset + 1:offset + 3] == ["Agent", "Task"]
    snapshot = resolve_effective_provider_session(state_dir=tmp_path, role=role)
    assert snapshot.snapshot["resolved"]["native_delegation"]["value"] is False


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_native_delegation_rejects_non_boolean_config(tmp_path, value):
    with pytest.raises(ConfigError):
        load_config(_config(tmp_path, {"name": "executor", "backend": "codex",
                                     "provider_session": {"native_delegation": value}}))


def test_unadmitted_native_delegation_is_rejected_by_preflight(tmp_path):
    config = load_config(_config(tmp_path, {"name": "executor", "backend": "codex",
                                          "provider_session": {"native_delegation": True}}))
    results = run_preflight_checks(config)
    result = next(item for item in results if item.name == "provider_session_configs")
    assert not result.ok
    assert "has not been admitted" in result.detail


def test_loader_and_adapters_map_supported_explicit_fields(tmp_path: Path) -> None:
    claude_path = _config(tmp_path, {
        "name": "impl",
        "backend": "claude-code",
        "provider_session": {"effort": "max", "agent": "implementation-worker"},
    })
    claude = load_config(claude_path).roles[0]
    claude_cmd = ClaudeCodeAdapter().build_command(claude)
    assert claude.provider_session == ProviderSessionConfig(
        effort="max",
        agent="implementation-worker",
    )
    assert claude_cmd[claude_cmd.index("--effort") + 1] == "max"
    assert claude_cmd[claude_cmd.index("--agent") + 1] == "implementation-worker"

    codex_path = _config(tmp_path, {
        "name": "verify",
        "backend": "codex",
        "provider_session": {
            "effort": "ultra",
            "max_parallel_agents": 4,
        },
    })
    codex = load_config(codex_path).roles[0]
    codex_cmd = CodexAdapter().build_command(codex)
    assert 'model_reasoning_effort="ultra"' in codex_cmd
    assert "agents.max_concurrent_threads_per_session=4" in codex_cmd


@pytest.mark.parametrize(
    ("role", "message"),
    [
        (
            {
                "name": "verify",
                "backend": "codex",
                "provider_session": {"agent": "reviewer"},
            },
            "does not support provider_session.agent",
        ),
        (
            {
                "name": "impl",
                "backend": "claude-code",
                "provider_session": {"max_parallel_agents": 2},
            },
            "does not support provider_session.max_parallel_agents",
        ),
        (
            {
                "name": "impl",
                "backend": "claude-code",
                "provider_session": {"effort": "ultra"},
            },
            "does not support provider_session.effort",
        ),
    ],
)
def test_preflight_rejects_unsupported_explicit_fields(
    tmp_path: Path,
    role: dict,
    message: str,
) -> None:
    config = load_config(_config(tmp_path, role))
    results = run_preflight_checks(config)
    result = next(item for item in results if item.name == "provider_session_configs")
    assert result.ok is False
    assert message in result.detail


def test_loader_rejects_unknown_conflicting_and_over_ceiling_fields(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigError, match="did you mean 'effort'"):
        load_config(_config(tmp_path, {
            "name": "impl",
            "backend": "claude-code",
            "provider_session": {"efort": "max"},
        }))
    with pytest.raises(ConfigError, match="declare one agent identity"):
        load_config(_config(tmp_path, {
            "name": "impl",
            "backend": "claude-code",
            "agent": "old",
            "provider_session": {"agent": "new"},
        }))
    with pytest.raises(ConfigError, match="must be <= 6"):
        load_config(_config(tmp_path, {
            "name": "verify",
            "backend": "codex",
            "provider_session": {"max_parallel_agents": 7},
        }))


def test_effective_snapshot_digest_is_stable(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    SessionStore(state_dir / "session.yaml").create(str(tmp_path))
    role = RoleConfig(
        name="verify",
        backend="codex",
        provider_session=ProviderSessionConfig(
            effort="ultra",
            max_parallel_agents=4,
        ),
    )

    first = resolve_effective_provider_session(state_dir=state_dir, role=role)
    second = resolve_effective_provider_session(state_dir=state_dir, role=role)

    assert first.digest == second.digest
    assert first.ref == second.ref
    body = json.loads((state_dir / first.ref).read_text(encoding="utf-8"))
    assert body["resolved"]["effort"]["value"] == "ultra"
    assert body["resolved"]["max_parallel_agents"]["value"] == 4
    assert body["capability_snapshot"]["sha256"]


def test_registry_reuses_same_digest_and_recycles_changed_digest(
    tmp_path: Path,
) -> None:
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    old_session = registry.get_or_create("verify", backend="claude-code")
    registry.mark_spawned("verify")
    first = registry.bind_provider_session_config(
        "verify",
        digest="a" * 64,
        ref="artifacts/provider-sessions/effective/a.json",
        explicit=False,
    )
    same = registry.bind_provider_session_config(
        "verify",
        digest="a" * 64,
        ref="artifacts/provider-sessions/effective/a.json",
        explicit=False,
    )
    changed = registry.bind_provider_session_config(
        "verify",
        digest="b" * 64,
        ref="artifacts/provider-sessions/effective/b.json",
        explicit=True,
    )

    assert first["status"] == "legacy_bound"
    assert same["status"] == "current"
    assert changed["status"] == "recycled"
    assert registry.get("verify") is None
    new_session = registry.get_or_create("verify", backend="claude-code")
    assert new_session != old_session


def test_explicit_config_recycles_legacy_spawn_without_digest(
    tmp_path: Path,
) -> None:
    registry = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.get_or_create("impl", backend="claude-code")
    registry.mark_spawned("impl")

    result = registry.bind_provider_session_config(
        "impl",
        digest="c" * 64,
        ref="artifacts/provider-sessions/effective/c.json",
        explicit=True,
    )

    assert result["status"] == "recycled"
    assert result["reason"] == "provider_session_config_currentness_unproven"
    assert registry.get("impl") is None


def test_loader_parses_role_lifecycle_and_preserves_eager_default(
    tmp_path: Path,
) -> None:
    configured = load_config(_config(tmp_path, {
        "name": "impl",
        "backend": "claude-code",
        "lifecycle": {
            "mode": "on_demand",
            "idle_seconds": 120,
            "cooldown_seconds": 30,
            "preserve_session": True,
            "preserve_workdir": True,
        },
    })).roles[0]
    omitted = load_config(_config(tmp_path, {
        "name": "verify",
        "backend": "codex",
    })).roles[0]

    assert configured.lifecycle == RoleLifecycleConfig(
        mode="on_demand",
        idle_seconds=120,
        cooldown_seconds=30,
    )
    assert omitted.lifecycle == RoleLifecycleConfig()


def test_loader_rejects_on_demand_control_plane_role(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="control-plane roles must remain resident"):
        load_config(_config(tmp_path, {
            "name": "orchestrator",
            "backend": "claude-code",
            "lifecycle": {"mode": "on_demand"},
        }))


def test_preflight_rejects_on_demand_backend_without_native_resume(
    tmp_path: Path,
) -> None:
    config = load_config(_config(tmp_path, {
        "name": "impl",
        "backend": "mock",
        "lifecycle": {"mode": "on_demand"},
    }))

    result = next(
        item
        for item in run_preflight_checks(config)
        if item.name == "provider_session_configs"
    )

    assert result.ok is False
    assert "without native session resume" in result.detail
