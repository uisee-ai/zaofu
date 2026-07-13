"""Tests for the `zf profile` CLI (doc 102 B5) + from-0 init/bootstrap/scaffold."""

from __future__ import annotations

import argparse
import json

import yaml

from zf.cli.profile import run_bootstrap, run_detect, run_recommend
from zf.core.config.candidate_gate import combined_candidate_gate_gap
from zf.core.config.loader import load_config
from zf.core.config.render import build_config_inspection_report


def _ns(**kw):
    return argparse.Namespace(**kw)


def _py_repo(root):
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("def test_x(): pass\n")


def test_cli_detect(tmp_path, capsys):
    _py_repo(tmp_path)
    rc = run_detect(_ns(path=str(tmp_path), json=False))
    assert rc == 0
    assert "python" in capsys.readouterr().out


def test_cli_recommend_json(tmp_path, capsys):
    _py_repo(tmp_path)
    rc = run_recommend(_ns(path=str(tmp_path), intent="build", stack=None, json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    rec = payload["recommendation"]
    # build on real code → validated prod flow (claude default = controller v3)
    assert rec["archetype"] == "prd-fanout-v3-claude" and rec["catalog"] == "flow"
    assert payload["profile"]["confidence"] == "high"


def test_cli_bootstrap_dry_run_writes_nothing(tmp_path, capsys):
    _py_repo(tmp_path)
    run_bootstrap(_ns(path=str(tmp_path), intent="build", stack=None,
                      apply=False, scaffold=False))
    assert not (tmp_path / "zf.yaml").exists()
    assert "dry-run" in capsys.readouterr().out


def test_cli_bootstrap_apply_declared_from_zero(tmp_path):
    # from-0: empty dir + declared stack + scaffold → materialize + scaffold
    rc = run_bootstrap(_ns(path=str(tmp_path), intent="build", stack="python",
                           apply=True, scaffold=True))
    assert rc == 0
    cfg = yaml.safe_load((tmp_path / "zf.yaml").read_text())
    assert cfg["quality_gates"]["static"]["required_checks"] == ["ruff check .", "pytest"]
    assert (tmp_path / "src").is_dir()
    assert (tmp_path / "tests").is_dir()
    assert (tmp_path / "README.md").exists()


def test_cli_bootstrap_apply_existing_no_clobber(tmp_path):
    _py_repo(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump(
        {"quality_gates": {"static": {"required_checks": ["my-custom-check"]}}}))
    run_bootstrap(_ns(path=str(tmp_path), intent="build", stack=None,
                      apply=True, scaffold=False))
    cfg = yaml.safe_load((tmp_path / "zf.yaml").read_text())
    assert cfg["quality_gates"]["static"]["required_checks"] == ["my-custom-check"]


def test_cli_bootstrap_unknown_stack_errors(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        run_bootstrap(_ns(path=str(tmp_path), intent="build", stack="cobol",
                          apply=False, scaffold=False))


def test_cli_bootstrap_refactor_flow_copies_profile_sources_and_skills(tmp_path):
    rc = run_bootstrap(_ns(
        path=str(tmp_path),
        intent="refactor",
        stack="node",
        backend="codex",
        scale="internal",
        apply=True,
        scaffold=False,
    ))

    assert rc == 0
    assert (tmp_path / "common" / "profiles.yaml").is_file()
    assert (tmp_path / "skills" / "zf-provider-contract-parity" / "SKILL.md").is_file()
    config = load_config(tmp_path / "zf.yaml")
    assert len(config.roles) == 11
    assert any(role.skills for role in config.roles)
    assert combined_candidate_gate_gap(config) == ""
    assert config.quality_gates["static"].required_checks == ["npm run lint", "npm test"]
    # fb9aa16a 起 profile 携带 skill_sources(2026-07-08 agent-skills 退役
    # 后只剩 zaofu-skills);bootstrap 自包含契约 = 启用技能(含 bundle 直挂
    # 的裸 yoke 名)全部 vendor 进项目 `skills/`,拷贝的 profile 源全部重写
    # 为本地相对路径,不留机器绝对路径。
    assert [(source.name, source.path) for source in config.skill_sources] == [
        ("zaofu-skills", "skills"),
    ]
    enabled = {
        str(skill)
        for role in config.roles
        for skill in (role.skills or [])
    }
    vendored = {
        p.parent.name
        for p in (tmp_path / "skills").glob("*/SKILL.md")
    }
    missing = sorted(enabled - vendored)
    assert not missing, f"enabled skills not vendored locally: {missing}"
    # Self-containment must include the transitive dependency closure, not just
    # directly-enabled skills: zf-yoke-*-role-context wrappers declare
    # `dependencies:` (method skills) materialized on demand at runtime. If the
    # closure isn't vendored the project breaks on another host / stale repo
    # (2026-07-08 E2E finding). source-verification is a dep of the dev-worker
    # wrapper and is never a directly-enabled bundle name here → proves closure.
    assert "zf-yoke-dev-worker-role-context" in vendored
    for dep in ("source-verification", "tdd-evidence", "verify-review"):
        assert dep in vendored, f"dependency-closure skill not vendored: {dep}"
    report = build_config_inspection_report(
        config,
        config_path=tmp_path / "zf.yaml",
        project_root=tmp_path,
        state_dir=tmp_path / config.project.state_dir,
    )
    assert report["status"] != "STOP"
