"""Evaluation inputs must carry exact source revisions and their dependencies."""
import importlib.util
from pathlib import Path
import subprocess
import json
import sys

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


@pytest.mark.parametrize(("timeout", "expected_timeout"), [("0", None), ("601", 601)])
def test_four_arm_prepare_uses_same_case_and_does_not_call_provider(tmp_path, monkeypatch, timeout, expected_timeout):
    helper = module()
    inputs = tmp_path / "inputs"
    (inputs / "project").mkdir(parents=True)
    (inputs / "prompt.md").write_text("Review the request without changing it.")
    (inputs / "project/request.md").write_text("Untrusted example, same in all arms.")
    variant = tmp_path / "variant/method"
    variant.mkdir(parents=True)
    (variant / "SKILL.md").write_text("Short method")

    def freeze(ref, destination):
        (destination / "method").mkdir(parents=True)
        (destination / "method/SKILL.md").write_text(ref)
        return "a" * 40

    monkeypatch.setattr(helper, "freeze_skills", freeze)
    monkeypatch.setattr(helper.subprocess, "check_output", lambda *a, **k: pytest.fail("provider or Docker called"))
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", ["eval", "--inputs", str(inputs), "--output", str(output),
        "--baseline", "old", "--candidate", "new", "--variant", str(variant.parent),
        "--include-bare", "--repetitions", "2", "--max-provider-calls", "8",
        "--model", "test-only", "--timeout", timeout, "--prepare-only"])
    assert helper.main() == 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["prepared_only"] and manifest["isolation"] is None
    assert manifest["codex_version"] is None
    assert manifest["timeout_seconds"] == expected_timeout
    assert [r["arm"] for r in manifest["runs"]] == [
        "baseline", "candidate", "variant", "bare", "bare", "variant", "candidate", "baseline"]
    for row in manifest["runs"]:
        project = output / row["id"] / "project"
        assert (project / "request.md").read_bytes() == (inputs / "project/request.md").read_bytes()
        assert "turn_completed" not in row
    assert (output / "bundles/candidate/method/SKILL.md").read_text() == "new"
    assert (output / "bundles/variant/method/SKILL.md").read_text() == "Short method"
    assert list((output / "bundles/bare").iterdir()) == []
    assert manifest["bundle_digests"]["candidate"] != manifest["bundle_digests"]["variant"]
    assert manifest["variant_replacements"]["method/SKILL.md"]


@pytest.mark.parametrize("extra", [
    ["--max-provider-calls", "1"],
    ["--timeout", "-1"],
    ["--include-bare", "--candidate-facts", "facts.json"],
])
def test_invalid_experiment_fails_before_creating_output(tmp_path, monkeypatch, extra):
    helper = module()
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", ["eval", "--inputs", "missing", "--output", str(output),
        "--baseline", "HEAD", "--candidate", "HEAD", "--model", "test", "--prepare-only", *extra])
    with pytest.raises(SystemExit) as exc:
        helper.main()
    assert exc.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("bad_path", ["method/new.md", "method/run.py", ".agents/method.md"])
def test_variant_cannot_inject_new_or_executable_paths(tmp_path, bad_path):
    source, target = tmp_path / "source", tmp_path / "target"
    (source / bad_path).parent.mkdir(parents=True)
    (source / bad_path).write_text("unapproved")
    (target / "method").mkdir(parents=True)
    (target / "method/SKILL.md").write_text("original")
    with pytest.raises(ValueError, match="variant"):
        module().overlay_variant(source, target)
    assert (target / "method/SKILL.md").read_text() == "original"


def test_variant_rejects_symlinks_before_replacing_any_file(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    for path in (source, target):
        (path / "method").mkdir(parents=True)
        (path / "method/SKILL.md").write_text(str(path))
    (source / "method/z.md").symlink_to(target / "method/SKILL.md")
    with pytest.raises(ValueError, match="unsafe"):
        module().overlay_variant(source, target)
    assert (target / "method/SKILL.md").read_text() == str(target)
