"""Concurrent or changing setup must not publish a false ready environment."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import subprocess

from zf.runtime.worktree_env import SETUP_MARKER, run_project_setup


def test_concurrent_setup_waits_without_running_second_installer(tmp_path):
    entered, release = Event(), Event()
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        entered.set()
        assert release.wait(5)
        return subprocess.CompletedProcess(argv, 0, "", "")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_project_setup, tmp_path, "install", runner=runner)
        assert entered.wait(5)
        try:
            second = run_project_setup(tmp_path, "install", runner=lambda *a, **k: subprocess.CompletedProcess(a, 0))
            assert not second.ok and not second.ran
            assert "in progress" in second.detail
        finally:
            release.set()
        assert first.result().ok
    assert len(calls) == 1
    assert not run_project_setup(tmp_path, "install").ran


def test_inputs_changed_during_setup_are_not_marked_ready(tmp_path):
    manifest = tmp_path / "package.json"
    manifest.write_text('{"version":"1"}')

    def runner(argv, **kwargs):
        manifest.write_text('{"version":"2"}')
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = run_project_setup(tmp_path, "install", runner=runner)
    assert not result.ok and result.ran
    assert "changed" in result.detail
    assert not (tmp_path / SETUP_MARKER).exists()


def test_failed_rebuild_invalidates_previous_ready_marker(tmp_path):
    assert run_project_setup(tmp_path, "true").ok
    failed = run_project_setup(tmp_path, "true", force=True,
        runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "interrupted rebuild"))
    assert not failed.ok
    assert not (tmp_path / SETUP_MARKER).exists()
    assert run_project_setup(tmp_path, "true").ran


def test_distinct_role_markers_cannot_install_into_same_physical_workspace(tmp_path):
    worktree = tmp_path / "project"
    worktree.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(worktree, target_is_directory=True)
    entered, release = Event(), Event()
    def installer(argv, **kwargs):
        entered.set()
        assert release.wait(5)
        return subprocess.CompletedProcess(argv, 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_project_setup, worktree, "install", marker_dir=tmp_path / "role-a", runner=installer)
        assert entered.wait(5)
        try:
            second = run_project_setup(alias, "install", marker_dir=tmp_path / "role-b",
                runner=lambda argv, **kw: subprocess.CompletedProcess(argv, 0))
            assert not second.ok and not second.ran
        finally:
            release.set()
        assert first.result().ok


def test_failed_bootstrap_directory_does_not_become_ready_on_retry(tmp_path):
    from zf.runtime.worktree_env import provision_worktree_env
    worktree = tmp_path / "project"
    worktree.mkdir()
    (worktree / "pyproject.toml").write_text("[project]\nname='broken'\nversion='1.0'\n")
    calls = []
    def fail(argv, **kwargs):
        calls.append(argv)
        (worktree / ".venv").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(argv, 1, "", "incomplete install")
    assert provision_worktree_env(worktree, tmp_path / "source", [".venv"], bootstrap_uv_dev=True, runner=fail) == []
    assert provision_worktree_env(worktree, tmp_path / "source", [".venv"], bootstrap_uv_dev=True, runner=fail) == []
    assert len(calls) == 4  # both attempts retried; existence is not a readiness receipt
    assert (worktree / ".venv").exists()  # preserve the candidate for repair


def test_bootstrap_shares_installer_lock_and_never_falls_back_to_foreign_venv(tmp_path):
    from zf.runtime.worktree_env import provision_worktree_env
    import pytest
    worktree = tmp_path / "project"
    worktree.mkdir()
    (worktree / "pyproject.toml").write_text("[project]\nname='sample'\nversion='1.0'\n")
    source = tmp_path / "source"
    (source / ".venv").mkdir(parents=True)
    entered, release = Event(), Event()
    calls = []
    def installer(argv, **kw):
        entered.set()
        assert release.wait(5)
        return subprocess.CompletedProcess(argv, 0)
    def fail(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_project_setup, worktree, "install", runner=installer)
        assert entered.wait(5)
        try:
            with pytest.raises(TimeoutError):
                provision_worktree_env(worktree, source, [".venv"], bootstrap_uv_dev=True, runner=fail)
        finally:
            release.set()
        assert first.result().ok
    assert calls == []
    assert provision_worktree_env(worktree, source, [".venv"], bootstrap_uv_dev=True, runner=fail) == []
    assert not (worktree / ".venv").exists()


def test_declared_data_identity_invalidates_readiness_and_missing_input_fails_closed(tmp_path):
    manifest = tmp_path / "dataset.identity.json"
    manifest.write_text('{"dataset":"v1"}')
    calls = []
    def installer(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)
    def prepare():
        return run_project_setup(tmp_path, "install", setup_inputs=["dataset.identity.json"], runner=installer)
    assert prepare().ok
    assert not prepare().ran
    manifest.write_text('{"dataset":"v2"}')
    assert prepare().ran
    assert len(calls) == 2
    manifest.unlink()
    result = prepare()
    assert not result.ok and not result.ran
    assert not (tmp_path / SETUP_MARKER).exists()


def test_bootstrap_reuses_only_receipt_bound_local_environment(tmp_path):
    from zf.runtime.worktree_env import provision_worktree_env
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\nversion='1.0'\n")
    calls = []
    def installer(argv, **kw):
        calls.append(argv)
        (tmp_path / ".venv").mkdir()
        return subprocess.CompletedProcess(argv, 0)
    def prepare():
        return provision_worktree_env(tmp_path, tmp_path / "source", [".venv"], bootstrap_uv_dev=True, runner=installer)
    assert prepare() == [".venv"]
    assert prepare() == [".venv"]
    assert len(calls) == 1
    (tmp_path / ".venv").rename(tmp_path / "preserved-env")
    (tmp_path / ".venv").mkdir()
    assert prepare() == []
    assert len(calls) == 1  # unknown replacement is preserved, not rebuilt while potentially in use


def test_setup_input_symlink_cannot_escape_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("private identity")
    project = tmp_path / "project"
    project.mkdir()
    (project / "identity").symlink_to(outside)
    result = run_project_setup(project, "touch unexpected", setup_inputs=["identity"])
    assert not result.ok and not result.ran
    assert not (project / "unexpected").exists()
