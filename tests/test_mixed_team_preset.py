"""Tests for examples/mixed-team.yaml — 1202-T4.

Locks in the mixed-backend preset shape so later edits don't regress
the topology. E2E smoke belongs to 1203; this file is config-only.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config


MIXED_YAML = Path(__file__).resolve().parents[1] / "examples" / "mixed-team.yaml"


def test_mixed_team_yaml_loads():
    config = load_config(MIXED_YAML)
    assert config.preset == "mixed-team"
    assert config.project.state_dir == ".zf"


def test_mixed_team_has_codex_dev_pool_and_test():
    """2026-04-22 update: dev pool is now split 2 Claude + 2 Codex
    replicas to stress-test mixed-backend dispatch. Codex-side has
    ``dev_codex`` (× 2 replicas) plus ``test`` (× 1)."""
    config = load_config(MIXED_YAML)
    codex_roles = [r.name for r in config.roles if r.backend == "codex"]
    assert set(codex_roles) == {"dev_codex", "test"}, (
        f"expected dev_codex+test on codex, got {codex_roles}"
    )
    # dev_codex expanded to 2 instances
    dev_codex_count = sum(1 for n in codex_roles if n == "dev_codex")
    assert dev_codex_count == 2, (
        f"expected 2 dev_codex replicas, got {dev_codex_count}"
    )


def test_mixed_team_orchestrator_and_critics_on_claude():
    config = load_config(MIXED_YAML)
    claude_roles = {
        r.name for r in config.roles if r.backend == "claude-code"
    }
    assert {"orchestrator", "arch", "review", "judge"}.issubset(claude_roles)


def test_mixed_team_demos_permission_modes():
    """2026-04-22: dev pool split, permission_mode invariant unchanged.

    - orchestrator: allowlist (Claude tool whitelist)
    - dev (claude-code, ×2): bypass
    - dev_codex (codex, ×2): bypass (B-1203-05: headless-only option)
    - test (codex): bypass (B-1203-05 extended)
    """
    config = load_config(MIXED_YAML)
    # All roles with a given name share permission_mode; pick first.
    by_name = {}
    for r in config.roles:
        by_name.setdefault(r.name, r)

    assert by_name["orchestrator"].permission_mode == "allowlist"
    assert by_name["dev"].permission_mode == "bypass"
    assert by_name["dev_codex"].permission_mode == "bypass", (
        "B-1203-05: codex dev must use bypass until headless "
        "sandbox-escalation UX is fixed upstream"
    )
    assert by_name["test"].permission_mode == "bypass", (
        "B-1203-05 extended: test (codex) also bypass"
    )


def test_mixed_team_topology_is_clean():
    """No orphan events, no dead-end roles — invariant the validate
    command checks at cold-start."""
    from zf.core.workflow.topology import WorkflowTopology

    config = load_config(MIXED_YAML)
    topology = WorkflowTopology.from_config(config)
    assert topology.orphan_events() == [], (
        f"unexpected orphan events: {topology.orphan_events()}"
    )
    assert topology.dead_end_roles() == [], (
        f"unexpected dead-end roles: {topology.dead_end_roles()}"
    )
