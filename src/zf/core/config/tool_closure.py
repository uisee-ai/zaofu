"""Tool closure validation — check for allow/deny conflicts at startup."""

from __future__ import annotations

from zf.core.config.schema import ZfConfig

# Tools that only the orchestrator should have
_ORCHESTRATOR_ONLY = {"tmux_send", "dispatch", "human_escalate", "kill_worker"}


def validate_tool_closure(config: ZfConfig) -> list[str]:
    """Check for tool configuration conflicts. Returns list of errors."""
    errors: list[str] = []

    for role in config.roles:
        # 1231-T5: Codex has no tool allowlist concept. allowed_tools is
        # dead config for backend=codex (SpawnCoordinator emits a
        # worker.spawn_warning at spawn time). Skip allowlist-shape checks
        # to avoid blocking sensible yamls; path-conflict check still runs
        # since `--add-dir` depends on allowed_paths being sane.
        codex_role = role.backend == "codex"

        if not codex_role:
            # Check for wildcards
            if "*" in role.allowed_tools:
                errors.append(f"Role '{role.name}': wildcard '*' in allowed_tools is forbidden")

            # Check orchestrator-only tools
            if role.name != "orchestrator":
                for tool in role.allowed_tools:
                    if tool in _ORCHESTRATOR_ONLY:
                        errors.append(
                            f"Role '{role.name}': tool '{tool}' is orchestrator-only"
                        )

        # Check allow/deny conflicts within constraints — applies to
        # every backend including codex (path semantics are real).
        allowed = set(role.constraints.allowed_paths)
        blocked = set(role.constraints.blocked_paths)
        overlap = allowed & blocked
        if overlap:
            errors.append(
                f"Role '{role.name}': paths in both allowed and blocked: {overlap}"
            )

    return errors
