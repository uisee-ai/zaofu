"""1231-T5: tool_closure should ignore allowed_tools checks for codex
roles (Codex has no tool allowlist — SpawnCoordinator already emits
worker.spawn_warning at spawn time; the loader shouldn't hard-error).

Path-conflict checks (allowed_paths × blocked_paths) still apply — they
map to codex's `--add-dir` behavior.
"""

from __future__ import annotations

from zf.core.config.schema import (
    ConstraintsConfig,
    ProjectConfig,
    RoleConfig,
    ZfConfig,
)
from zf.core.config.tool_closure import validate_tool_closure


def test_codex_allowed_tools_wildcard_does_not_error():
    """Claude: wildcard forbidden. Codex: allowed_tools is ignored, no
    error."""
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        roles=[
            RoleConfig(
                name="dev",
                backend="codex",
                permission_mode="bypass",
                allowed_tools=["*"],
            ),
        ],
    )
    errors = validate_tool_closure(config)
    assert errors == [], (
        f"codex role with wildcard should be silently ignored, got: {errors}"
    )


def test_claude_wildcard_still_errors():
    """Regression guard: the existing claude path must not regress."""
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        roles=[
            RoleConfig(
                name="dev",
                backend="claude-code",
                permission_mode="allowlist",
                allowed_tools=["*"],
            ),
        ],
    )
    errors = validate_tool_closure(config)
    assert errors, "claude role with wildcard must still error"
    assert "wildcard" in errors[0].lower()


def test_non_orchestrator_orchestrator_only_tool_errors():
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        roles=[
            RoleConfig(
                name="dev",
                backend="claude-code",
                permission_mode="allowlist",
                allowed_tools=["kill_worker"],
            ),
        ],
    )
    errors = validate_tool_closure(config)
    assert errors
    assert "orchestrator-only" in errors[0]


def test_codex_role_with_orchestrator_only_tool_is_ignored():
    """Codex can't use these tools anyway — allowed_tools is dead config."""
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        roles=[
            RoleConfig(
                name="dev",
                backend="codex",
                permission_mode="bypass",
                allowed_tools=["kill_worker"],
            ),
        ],
    )
    errors = validate_tool_closure(config)
    assert errors == [], (
        f"codex role with orchestrator-only tool should be ignored: {errors}"
    )


def test_codex_path_conflicts_still_error():
    """Path conflicts matter for codex (`--add-dir` cares); keep strict."""
    config = ZfConfig(
        project=ProjectConfig(name="x"),
        roles=[
            RoleConfig(
                name="dev",
                backend="codex",
                permission_mode="restricted",
                constraints=ConstraintsConfig(
                    allowed_paths=["src/"],
                    blocked_paths=["src/"],
                ),
            ),
        ],
    )
    errors = validate_tool_closure(config)
    assert errors, "path overlap must still error even for codex"
    assert "both allowed and blocked" in errors[0]
