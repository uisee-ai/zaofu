"""Tests for the `zf profile` CLI (doc 102 B5) + from-0 init/bootstrap/scaffold."""

from __future__ import annotations

import argparse
import json

import yaml

from zf.cli.profile import run_bootstrap, run_detect, run_recommend


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
    # build on real code → validated prod flow
    assert rec["archetype"] == "prd-fanout-claude" and rec["catalog"] == "flow"
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
