"""Evaluation inputs must carry exact source revisions and their dependencies."""
import importlib.util
from pathlib import Path
import subprocess

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / "scripts/eval-plan-execution.py"
    spec = importlib.util.spec_from_file_location("plan_eval", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_freeze_includes_yoke_dependencies_and_ignores_dirty_checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    files = {"skills/method/SKILL.md": "source v1", "yoke/grill/SKILL.md": "dependency",
             "yoke/method/SKILL.md": "overridden builtin"}
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    subprocess.run(["git", "-C", str(repo), "add", "--", *files], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Eval Test", "-c",
                    "user.email=eval@example.invalid", "commit", "-qm", "test: inputs"], check=True)
    (repo / "skills/method/SKILL.md").write_text("uncommitted change")
    helper = module()
    helper.ROOT = repo
    commit = helper.freeze_skills("HEAD", tmp_path / "frozen")
    assert len(commit) == 40
    assert (tmp_path / "frozen/method/SKILL.md").read_text() == "source v1"
    assert (tmp_path / "frozen/grill/SKILL.md").read_text() == "dependency"


@pytest.mark.parametrize("reserved", [".agents", ".git", ".codex", ".claude"])
def test_inputs_cannot_override_frozen_instructions(tmp_path, reserved):
    source = tmp_path / "source"
    (source / reserved).mkdir(parents=True)
    (source / reserved / "override").write_text("wrong revision")
    with pytest.raises(ValueError, match="reserved"):
        module().copy_inputs(source, tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_inputs_do_not_follow_host_symlink(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "host").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        module().copy_inputs(source, tmp_path / "target")
    assert not (tmp_path / "target").exists()
