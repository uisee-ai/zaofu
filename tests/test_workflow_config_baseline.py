import hashlib
import subprocess

import pytest

from tests.test_workflow_config_apply import _apply_payload, _execute, _proposal
from zf.core.events import EventLog, EventWriter
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_config_baseline import ConfigBaselineError, establish_config_baseline


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def setup(root):
    proposal, ref, validation = _proposal(root)
    (root / "app.py").write_text("original\n")
    git(root, "init", "-q")
    git(root, "add", "--", "zf.yaml", "app.py")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "test: initial baseline")
    state = root / ".zf"
    log = EventLog(state / "events.jsonl")
    service = ControlledActionService(state, EventWriter(log), project_root=root, actor="operator")
    payload = {**_apply_payload(proposal, ref, validation), "establish_delivery_baseline": True}
    return service, log, payload


def test_controlled_apply_establishes_exact_clean_baseline_and_replays(tmp_path):
    service, log, payload = setup(tmp_path)
    initial = git(tmp_path, "rev-parse", "HEAD")
    result = _execute(service, payload)
    assert result["ok"], result
    commit = git(tmp_path, "rev-parse", "HEAD")
    assert commit != initial
    assert git(tmp_path, "diff-tree", "--no-commit-id", "--name-only", "-r", commit) == "zf.yaml"
    assert git(tmp_path, "status", "--porcelain", "--untracked-files=no") == ""
    baseline = hydrate_sidecar_ref(service.state_dir, result["delivery_baseline_ref"]).payload
    assert baseline["commit"] == commit
    assert baseline["proposal_digest"] == payload["proposal_digest"]
    assert _execute(service, payload)["ok"]
    assert git(tmp_path, "rev-parse", "HEAD") == commit
    assert next(e for e in log.read_all() if e.type == "workflow.config.change.applied").payload["delivery_baseline_ref"]


@pytest.mark.parametrize("staged", [False, True])
def test_other_changes_block_without_adopting_them_and_retry_recovers(tmp_path, staged):
    service, _, payload = setup(tmp_path)
    initial = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "app.py").write_text("user change\n")
    if staged:
        git(tmp_path, "add", "--", "app.py")
    index_before = git(tmp_path, "show", ":app.py")
    result = _execute(service, payload)
    assert not result["ok"] and result["code"] == "config_delivery_baseline_blocked"
    assert git(tmp_path, "rev-parse", "HEAD") == initial
    assert git(tmp_path, "show", ":app.py") == index_before
    assert (tmp_path / "app.py").read_text() == "user change\n"
    # Owner resolves their own change; retry adopts only the approved config.
    git(tmp_path, "restore", "--staged", "--worktree", "--", "app.py")
    assert _execute(service, payload)["ok"]
    assert git(tmp_path, "status", "--porcelain", "--untracked-files=no") == ""


def test_start_recovers_legacy_apply_without_baseline(tmp_path, monkeypatch):
    from zf.runtime.workflow_config_apply import WorkflowConfigApplyService
    checkpoint = WorkflowConfigApplyService._checkpoint
    monkeypatch.setattr(WorkflowConfigApplyService, "_checkpoint", lambda *args: "")
    service, _, payload = setup(tmp_path)
    assert _execute(service, {k: v for k, v in payload.items() if k != "establish_delivery_baseline"})["ok"]
    assert git(tmp_path, "status", "--porcelain", "--untracked-files=no")
    monkeypatch.setattr(WorkflowConfigApplyService, "_checkpoint", checkpoint)
    assert _execute(service, payload)["ok"]
    assert git(tmp_path, "status", "--porcelain", "--untracked-files=no") == ""


def test_ref_update_interruption_repairs_index_on_retry(tmp_path):
    service, _, payload = setup(tmp_path)
    initial = git(tmp_path, "rev-parse", "HEAD")
    assert _execute(service, payload)["ok"]
    commit = git(tmp_path, "rev-parse", "HEAD")
    # Reproduce the durable state left after a commit but before index replacement.
    git(tmp_path, "read-tree", initial)
    assert git(tmp_path, "diff", "--cached", "--name-only") == "zf.yaml"
    assert _execute(service, payload)["ok"]
    assert git(tmp_path, "rev-parse", "HEAD") == commit
    assert git(tmp_path, "status", "--porcelain", "--untracked-files=no") == ""


def test_unapproved_config_in_index_is_preserved(tmp_path):
    service, _, payload = setup(tmp_path)
    config = tmp_path / "zf.yaml"
    original = config.read_bytes()
    config.write_bytes(original + b"\n# unapproved\n")
    git(tmp_path, "add", "--", "zf.yaml")
    config.write_bytes(original)
    staged = git(tmp_path, "show", ":zf.yaml")
    result = _execute(service, payload)
    assert result["code"] == "config_delivery_baseline_blocked"
    assert git(tmp_path, "show", ":zf.yaml") == staged


def test_committed_config_drift_is_not_adopted(tmp_path):
    service, _, payload = setup(tmp_path)
    # The proposal's base was dirty before approval, so it must not be swept in.
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "test: unrelated commit")
    target = tmp_path / "zf.yaml"
    target.write_text("approved but not committed\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ConfigBaselineError, match="approved base or target"):
        establish_config_baseline(target, base_digest="other", target_digest=digest, proposal_digest="P")


def test_nested_project_uses_repository_relative_path(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    service, _, payload = setup(nested)
    # Move the test repository metadata to its parent to exercise a nested project.
    (nested / ".git").rename(tmp_path / ".git")
    git(tmp_path, "read-tree", "--empty")
    git(tmp_path, "add", "--", "nested/zf.yaml", "nested/app.py")
    git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "test: nested project")
    assert _execute(service, payload)["ok"]
    assert git(tmp_path, "status", "--porcelain", "--untracked-files=no") == ""


def test_staged_change_undone_only_in_worktree_is_not_lost(tmp_path):
    service, _, payload = setup(tmp_path)
    (tmp_path / "app.py").write_text("staged user change\n")
    git(tmp_path, "add", "--", "app.py")
    (tmp_path / "app.py").write_text("original\n")
    result = _execute(service, payload)
    assert result["code"] == "config_delivery_baseline_blocked"
    assert git(tmp_path, "show", ":app.py") == "staged user change"
    assert (tmp_path / "app.py").read_text() == "original\n"


def test_busy_index_preserves_owner_lock(tmp_path):
    service, _, payload = setup(tmp_path)
    lock = tmp_path / ".git/index.lock"
    lock.write_text("other git owner")
    result = _execute(service, payload)
    assert result["code"] == "config_delivery_baseline_blocked"
    assert lock.read_text() == "other git owner"
    lock.unlink()
    assert _execute(service, payload)["ok"]
