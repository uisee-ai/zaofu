from pathlib import Path

from tests.test_self_repair_runner import _dispatch_requested, _git, _writer
from zf.runtime.repair_dispatch import RepairRequest, repair_branch_name
from zf.runtime.self_repair_runner import dispatch_pending_self_repairs


def test_repair_names_distinguish_full_fingerprint_and_lossy_sanitization():
    fingerprints = ["run-manager:projection_stale:/tmp/zf-unattended-r13/state",
                    "run-manager:projection_stale:/tmp/zf-unattended-r14/state",
                    "worker:a/b", "worker/a:b"]
    names = [repair_branch_name(RepairRequest(fp, 0, "C", "candidate")) for fp in fingerprints]
    assert len(set(names)) == len(names)


def test_same_repair_identity_is_stable_and_attempt_scoped():
    req = RepairRequest("stall:X", 0, "C", "candidate")
    assert repair_branch_name(req) == repair_branch_name(req)
    assert repair_branch_name(req) != repair_branch_name(RepairRequest("stall:X", 1, "C", "candidate"))


def test_real_git_repairs_in_separate_state_dirs_do_not_collide_or_overwrite(tmp_path):
    root = tmp_path / "harness"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Qualification")
    _git(root, "config", "user.email", "qualification@example.invalid")
    (root / "base.txt").write_text("base\n")
    _git(root, "add", "--", "base.txt")
    _git(root, "commit", "--only", "base.txt", "-qm", "test: seed")
    worktrees = []
    for name in ("run-one", "run-two"):
        state = tmp_path / name
        state.mkdir()
        log, writer = _writer(state)
        request = _dispatch_requested("stall:shared-prefix")
        writer.append(request)
        assert dispatch_pending_self_repairs(log.read_all(), writer, root=str(root),
                                             tmp_root=str(tmp_path), spawn=False) == 1
        dispatched = next(e for e in log.read_all() if e.type == "autoresearch.repair.dispatched")
        worktree = Path(dispatched.payload["worktree"])
        worktrees.append(worktree)
        (worktree / "retained.txt").write_text(name)
        assert dispatch_pending_self_repairs(log.read_all(), writer, root=str(root),
                                             tmp_root=str(tmp_path), spawn=False) == 0
    assert worktrees[0] != worktrees[1]
    assert [(w / "retained.txt").read_text() for w in worktrees] == ["run-one", "run-two"]
