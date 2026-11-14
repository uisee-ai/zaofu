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
