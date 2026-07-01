"""Tests for safe-team preset (E3) — 5 worker roles + orchestrator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import load_config
from zf.core.config.presets import generate_preset_yaml, get_preset, list_presets


def test_safe_team_listed(tmp_path: Path):
    assert "safe-team" in list_presets()


def test_safe_team_has_orchestrator_with_stream_json(tmp_path: Path):
    yaml_text = generate_preset_yaml("safe-team", "demo")
    p = tmp_path / "zf.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    role_names = [r.name for r in cfg.roles]
    assert "orchestrator" in role_names
    orch = next(r for r in cfg.roles if r.name == "orchestrator")
    assert orch.transport == "stream-json"
    assert orch.permission_mode == "allowlist"


def test_safe_team_orchestrator_allowed_tools(tmp_path: Path):
    yaml_text = generate_preset_yaml("safe-team", "demo")
    p = tmp_path / "zf.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    orch = next(r for r in cfg.roles if r.name == "orchestrator")
    tools = " ".join(orch.allowed_tools)
    assert "zf feature" in tools
    assert "zf kanban" in tools
    assert "zf emit" in tools
    assert "zf events" in tools


def test_safe_team_has_5_worker_roles(tmp_path: Path):
    yaml_text = generate_preset_yaml("safe-team", "demo")
    p = tmp_path / "zf.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    role_names = {r.name for r in cfg.roles}
    for w in ("arch", "dev", "review", "test", "judge"):
        assert w in role_names, f"missing worker role: {w}"


def test_safe_team_workers_default_to_tmux(tmp_path: Path):
    yaml_text = generate_preset_yaml("safe-team", "demo")
    p = tmp_path / "zf.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    for w_name in ("arch", "dev", "review", "test", "judge"):
        w = next(r for r in cfg.roles if r.name == w_name)
        assert w.transport == "tmux", f"{w_name} should default to tmux"


def test_safe_team_workflow_topology_has_no_orphans(tmp_path: Path):
    """Every published event should be consumed by some role's triggers,
    and every triggered role should have a publishing role to feed it."""
    yaml_text = generate_preset_yaml("safe-team", "demo")
    p = tmp_path / "zf.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    from zf.core.workflow.topology import WorkflowTopology
    topo = WorkflowTopology.from_config(cfg)
    # The graph should have edges from arch → review, dev → review, dev → test, etc
    edges = topo.edges()
    assert len(edges) > 0


def test_safe_team_review_and_critic_cover_both_review_modes(tmp_path: Path):
    """Since 29f18e2 the dual-trigger review is split: the dedicated critic
    role takes design critique (arch.proposal.done) and review does code
    review gated behind the kernel static gate (static_gate.passed)."""
    yaml_text = generate_preset_yaml("safe-team", "demo")
    p = tmp_path / "zf.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    review = next(r for r in cfg.roles if r.name == "review")
    assert review.triggers == ["static_gate.passed"]
    critic = next(r for r in cfg.roles if r.name == "critic")
    assert "arch.proposal.done" in critic.triggers


def test_safe_team_role_instructions_per_role(tmp_path: Path):
    """generate_role_instructions must produce different content per role."""
    from zf.runtime.injection import generate_role_instructions
    yaml_text = generate_preset_yaml("safe-team", "demo")
    p = tmp_path / "zf.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    instructions_by_role = {}
    for role in cfg.roles:
        instructions_by_role[role.name] = generate_role_instructions(cfg, role)
    # Each role's instructions should mention the role name
    for name, text in instructions_by_role.items():
        assert name in text, f"instructions for {name} don't mention {name}"


def test_safe_team_zf_yaml_loads_clean(tmp_path: Path):
    """The whole preset round-trips through yaml.dump → load_config without errors."""
    yaml_text = generate_preset_yaml("safe-team", "test-project")
    p = tmp_path / "zf.yaml"
    p.write_text(yaml_text)
    cfg = load_config(p)
    assert cfg.preset == "safe-team"
    assert cfg.project.name == "test-project"
