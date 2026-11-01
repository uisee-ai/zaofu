from zf.runtime.workflow_git_readiness import git_baseline_diagnostics, git_read, preparation_git_diagnostics
from zf.runtime.project_git_baseline import apply_git_baseline, preview_git_baseline
from zf.runtime.workflow_preparation_view import preparation_view


def test_git_states_and_registered_route_scope(tmp_path):
    config = tmp_path / "zf.yaml"
    config.write_text("version: '1.0'\n")
    assert git_baseline_diagnostics(tmp_path)[0]["kind"] == "git_repository_missing"
    assert preparation_git_diagnostics(tmp_path) == []
    config.write_text("metadata: null\n")
    assert preparation_git_diagnostics(tmp_path) == []
    config.write_text("metadata: {zaofu: {catalog: {assembly: project-bound-v1}}}\n")
    assert preparation_git_diagnostics(tmp_path)[0]["kind"] == "git_repository_missing"
    git_read(tmp_path, "init", "-b", "main")
    assert git_baseline_diagnostics(tmp_path)[0]["kind"] == "git_head_missing"
    manifest = preview_git_baseline(tmp_path, tmp_path / ".state", paths=["zf.yaml"], exclude_paths=[])
    apply_git_baseline(tmp_path, tmp_path / ".state", manifest)
    assert preparation_git_diagnostics(tmp_path) == []
    git_read(tmp_path, "checkout", "--detach")
    assert git_baseline_diagnostics(tmp_path)[0]["kind"] == "git_detached_head"


def test_preparation_failure_and_current_revision_projection():
    request = {"status": "ready", "revision": 2, "synthesis_operation_revision": 2}
    operation = {"status": "failed", "reason": "Git baseline missing"}
    projected = preparation_view(request, operation)
    assert projected["status"] == "preparation_failed"
    assert projected["can_retry"]
    assert projected["blockers"][0]["message"] == operation["reason"]
    assert preparation_view({**request, "revision": 3}, operation)["status"] == "ready"
    assert preparation_view({**request, "status": "proposed"}, operation)["status"] == "proposed"
