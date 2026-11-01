"""The approved initial commit contains exactly the reviewed source snapshot."""
import json

import pytest

from tests.test_control_actions_workflow_request import _service
from zf.cli.main import main
from zf.runtime.project_git_baseline import apply_git_baseline, preview_git_baseline
from zf.runtime.workflow_git_readiness import git_read


def test_controlled_baseline_preserves_dataset_and_runtime(tmp_path, capsys):
    service, log = _service(tmp_path)
    (tmp_path / "dataset").mkdir()
    (tmp_path / "dataset" / "sensor.raw").write_bytes(b"sensor bytes")
    manifest = preview_git_baseline(tmp_path, service.state_dir, paths=["zf.yaml"], exclude_paths=["dataset"])
    assert not (tmp_path / ".git").exists()
    assert not (tmp_path / ".gitignore").exists()
    manifest_path = tmp_path.parent / (tmp_path.name + "-manifest.json")
    manifest_path.write_text(json.dumps(manifest))
    assert main(["project", "git-baseline", "--root", str(tmp_path), "--apply", "--manifest", str(manifest_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "applied", result
    assert set(git_read(tmp_path, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()) == {"zf.yaml", ".gitignore"}
    assert git_read(tmp_path, "status", "--porcelain").stdout == ""
    assert (tmp_path / "dataset" / "sensor.raw").read_bytes() == b"sensor bytes"
    assert apply_git_baseline(tmp_path, service.state_dir, manifest)["status"] == "already_applied"
    types = [e.type for e in log.read_all()]
    assert "runtime.action.completed" in types
    assert "workflow.invoke.requested" not in types


@pytest.mark.parametrize("change", ["source", "ignore", "digest", "staged"])
def test_baseline_rejects_stale_or_unapproved_changes(tmp_path, change):
    (tmp_path / "zf.yaml").write_text("version: '1.0'\n")
    manifest = preview_git_baseline(tmp_path, tmp_path / ".state", paths=["zf.yaml"], exclude_paths=[])
    if change == "source":
        (tmp_path / "zf.yaml").write_text("changed\n")
    elif change == "ignore":
        (tmp_path / ".gitignore").write_text("changed\n")
    elif change == "digest":
        manifest["branch"] = "other"
    else:
        assert git_read(tmp_path, "init", "-b", "main").returncode == 0
        assert git_read(tmp_path, "add", "--", "zf.yaml").returncode == 0
    with pytest.raises(ValueError):
        apply_git_baseline(tmp_path, tmp_path / ".state", manifest)
    assert git_read(tmp_path, "rev-parse", "--verify", "HEAD").returncode != 0


@pytest.mark.parametrize("path", ["../outside", ".git/config", ".env", "secret.key", "symlink", "token.txt", "dataset/fixture"])
def test_baseline_refuses_unsafe_files(tmp_path, path):
    (tmp_path / "zf.yaml").write_text("version: '1.0'\n")
    if path == "symlink":
        (tmp_path / path).symlink_to(tmp_path / "zf.yaml")
    elif path in {".env", "secret.key", "token.txt"}:
        (tmp_path / path).write_text("ghp_" + "a" * 36 if path == "token.txt" else "credential")
    with pytest.raises(ValueError):
        preview_git_baseline(tmp_path, tmp_path / ".state", paths=["zf.yaml", path], exclude_paths=["dataset"])
    assert not (tmp_path / ".git").exists()
