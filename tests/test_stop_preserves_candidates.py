"""The public stop cleanup entry must retain unmerged or unpublished work."""
import pytest

from tests.test_stop_clean_workdirs import _init_product_repo, _add_worker_worktree, _git
from zf.cli.stop import clean_state_dir_workdirs


@pytest.mark.parametrize("material", ["dirty", "ignored", "unmerged"])
def test_stop_cleanup_preserves_candidate_material(tmp_path, material):
    _init_product_repo(tmp_path)
    work = _add_worker_worktree(tmp_path, ".zf-a", "dev-lane-0")
    candidate = work / "result.txt"
    if material == "ignored":
        # Local excludes are deliberately outside the branch's code tree.
        info = tmp_path / ".git/info/exclude"
        info.write_text(info.read_text() + "\nresult.txt\n")
    candidate.write_text("candidate evidence")
    if material == "unmerged":
        assert _git(work, "add", "result.txt").returncode == 0
        assert _git(work, "commit", "-qm", "feat: candidate").returncode == 0
    assert clean_state_dir_workdirs(tmp_path, tmp_path / ".zf-a") == (0, [])
    assert candidate.read_text() == "candidate evidence"
