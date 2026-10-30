"""Approved config apply must leave a usable, exact Git delivery baseline."""
import hashlib
import subprocess

import pytest

from tests.test_workflow_config_apply import _proposal, _apply_payload
from zf.runtime.git_capture import git_env
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_config_apply import WorkflowConfigApplyError, WorkflowConfigApplyService


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          env=git_env(), capture_output=True, text=True).stdout.strip()


@pytest.fixture
def project(tmp_path):
    proposal, ref, validation = _proposal(tmp_path)
    git(tmp_path, "init", "-b", "main")
    (tmp_path / "business.txt").write_text("original\n")
    git(tmp_path, "add", "--", "zf.yaml", "business.txt")
    git(tmp_path, "commit", "-m", "chore: initialize")
    service = WorkflowConfigApplyService(state_dir=tmp_path / ".zf", project_root=tmp_path)
    return service, _apply_payload(proposal, ref, validation), proposal


def test_apply_checkpoints_only_approved_config_and_replays_without_duplicate_commit(tmp_path, project):
    service, payload, proposal = project
    before = git(tmp_path, "rev-parse", "HEAD")
    first = service.apply(payload, actor="test")
    head = git(tmp_path, "rev-parse", "HEAD")
    assert head != before
    assert git(tmp_path, "diff-tree", "--no-commit-id", "--name-only", "-r", head) == "zf.yaml"
    assert git(tmp_path, "diff", "--name-only", "HEAD") == ""
    receipt = hydrate_sidecar_ref(tmp_path / ".zf", first.receipt_ref).payload
    assert receipt["config_commit"] == head
    assert hashlib.sha256((tmp_path / "zf.yaml").read_bytes()).hexdigest() == proposal["target_config"]["sha256"]
    assert service.apply(payload, actor="test").replayed
    assert git(tmp_path, "rev-parse", "HEAD") == head


def test_staged_unrelated_work_is_preserved_and_cannot_be_swept(tmp_path, project):
    service, payload, _ = project
    (tmp_path / "business.txt").write_text("staged user work\n")
    git(tmp_path, "add", "--", "business.txt")
    head = git(tmp_path, "rev-parse", "HEAD")
    staged = git(tmp_path, "diff", "--cached")
    with pytest.raises(WorkflowConfigApplyError, match="clean index"):
        service.apply(payload, actor="test")
    assert git(tmp_path, "rev-parse", "HEAD") == head
    assert git(tmp_path, "diff", "--cached") == staged


def test_failed_commit_is_retryable_without_rewriting_approval_or_bypassing_hooks(tmp_path, project):
    service, payload, _ = project
    hook = tmp_path / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    head = git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(WorkflowConfigApplyError, match="checkpoint failed"):
        service.apply(payload, actor="test")
    assert git(tmp_path, "rev-parse", "HEAD") == head
    hook.unlink()
    result = service.apply(payload, actor="test")
    assert result.status == "applied"
    assert git(tmp_path, "diff", "--name-only", "HEAD") == ""


def test_checkpoint_rejects_new_git_baseline_not_covered_by_proposal(tmp_path, project):
    service, payload, _ = project
    original = (tmp_path / "zf.yaml").read_text()
    (tmp_path / "zf.yaml").write_text(original + "\n# another driver\n")
    git(tmp_path, "add", "--", "zf.yaml")
    git(tmp_path, "commit", "-m", "chore: another config revision")
    (tmp_path / "zf.yaml").write_text(original)
    head = git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(WorkflowConfigApplyError, match="Git baseline"):
        service.apply(payload, actor="test")
    assert git(tmp_path, "rev-parse", "HEAD") == head


def test_legacy_applied_receipt_can_finish_checkpoint_without_duplicate_apply(tmp_path, project, monkeypatch):
    service, payload, _ = project
    checkpoint = service._checkpoint
    monkeypatch.setattr(service, "_checkpoint", lambda *args: "")
    old = service.apply(payload, actor="test")
    old_receipt = hydrate_sidecar_ref(tmp_path / ".zf", old.receipt_ref).payload
    assert not old_receipt["config_commit"]
    monkeypatch.setattr(service, "_checkpoint", checkpoint)
    replay = service.apply(payload, actor="test")
    assert replay.replayed
    receipt = hydrate_sidecar_ref(tmp_path / ".zf", replay.receipt_ref).payload
    assert receipt["config_commit"] == git(tmp_path, "rev-parse", "HEAD")
    assert receipt["prior_receipt_ref"] == old.receipt_ref
    assert hydrate_sidecar_ref(tmp_path / ".zf", old.receipt_ref).payload == old_receipt
