"""Real subprocess holders, with mock provider commands and no provider account."""
import select
import subprocess
import sys
from types import SimpleNamespace

from zf.core.config.schema import RoleConfig
from zf.runtime.environment_usage import environment_in_use
from zf.runtime.worktree_env import run_project_setup
from zf.runtime.transport import TmuxTransport
from tests.test_tmux_transport_lifecycle import _FakeTmux
from tests.test_stop_clean_workdirs import _init_product_repo, _add_worker_worktree
from zf.cli.stop import clean_state_dir_workdirs


def test_actual_tmux_launch_command_holds_environment_for_native_process(tmp_path, monkeypatch):
    tmux = _FakeTmux()
    tmux.layout = SimpleNamespace(create_slot=lambda *args: None)
    transport = TmuxTransport(tmux)
    monkeypatch.setattr(transport, "_wait_for_shell_handshake", lambda role: None)
    transport.spawn(RoleConfig(name="dev", backend="codex"),
        [sys.executable, "-c", "print('ready', flush=True); input()"], cwd=tmp_path)
    process = subprocess.Popen(tmux.sent[-1][1], shell=True, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == "ready"
        result = run_project_setup(tmp_path, "touch forbidden")
        assert not result.ok and not result.ran
        assert not (tmp_path / "forbidden").exists()
    finally:
        process.communicate("exit\n", timeout=5)
    assert process.returncode == 0
    assert run_project_setup(tmp_path, "true").ok


def test_provider_closing_inherited_descriptors_still_blocks_installer(tmp_path):
    from zf.runtime.environment_usage import guarded_provider_argv

    process = subprocess.Popen(guarded_provider_argv(tmp_path, [sys.executable, '-c',
        "import os; os.closerange(3, 65536); print('ready', flush=True); input()"]),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == 'ready'
        assert not run_project_setup(tmp_path, 'touch forbidden', force=True).ok
        assert not (tmp_path / 'forbidden').exists()
        import pytest
        from zf.runtime.environment_resources import register_environment_resources
        with pytest.raises(TimeoutError):
            register_environment_resources(tmp_path, ['node_modules'])
    finally:
        process.communicate('\n', timeout=5)
    assert run_project_setup(tmp_path, 'true').ok


def test_unregistered_process_with_workspace_cwd_blocks_cleanup_and_setup(tmp_path):
    _init_product_repo(tmp_path)
    project = _add_worker_worktree(tmp_path, '.runtime', 'external-child')
    process = subprocess.Popen([sys.executable, '-c', "print('ready', flush=True); input()"],
        cwd=project, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == 'ready'
        assert not run_project_setup(project, 'touch forbidden', force=True).ok
        assert clean_state_dir_workdirs(tmp_path, tmp_path / '.runtime')[0] == 0
        assert project.exists()
    finally:
        process.communicate('\n', timeout=5)
    assert clean_state_dir_workdirs(tmp_path, tmp_path / '.runtime')[0] == 1


def test_detached_child_retains_resource_identity_after_provider_exits(tmp_path):
    import os
    import signal
    import time
    from zf.runtime.environment_usage import guarded_provider_argv

    child_code = "import os,time; os.chdir('/'); os.closerange(3,65536); time.sleep(20)"
    parent_code = (
        "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c',sys.argv[1]],"
        "start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "print(p.pid,flush=True)"
    )
    result = subprocess.run(guarded_provider_argv(tmp_path, [sys.executable, '-c', parent_code, child_code]),
        capture_output=True, text=True, timeout=5, check=True)
    pid = int(result.stdout)
    try:
        assert not run_project_setup(tmp_path, 'touch forbidden', force=True).ok
        assert not (tmp_path / 'forbidden').exists()
    finally:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            from pathlib import Path
            stat = Path(f'/proc/{pid}/stat')
            if not stat.exists() or stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                break
            time.sleep(.02)
    assert run_project_setup(tmp_path, 'true').ok


def test_stop_cleanup_retains_resource_with_an_actual_holder(tmp_path):
    _init_product_repo(tmp_path)
    project = _add_worker_worktree(tmp_path, ".runtime", "dev-held")
    with environment_in_use(project):
        removed, branches = clean_state_dir_workdirs(tmp_path, tmp_path / ".runtime")
        assert removed == 0 and branches == []
        assert project.exists()
    assert clean_state_dir_workdirs(tmp_path, tmp_path / ".runtime")[0] == 1


def test_readers_share_environment_and_only_last_exit_allows_mutation(tmp_path):
    from zf.runtime.environment_usage import environment_mutation
    import pytest

    with environment_in_use(tmp_path):
        with environment_in_use(tmp_path):
            with pytest.raises(TimeoutError):
                with environment_mutation(tmp_path):
                    pytest.fail('mutated live environment')
        assert not run_project_setup(tmp_path, 'true').ok
    assert run_project_setup(tmp_path, 'true').ok


def test_installer_prevents_native_provider_start(tmp_path):
    from zf.runtime.environment_usage import environment_mutation, guarded_provider_argv

    with environment_mutation(tmp_path):
        result = subprocess.run(guarded_provider_argv(tmp_path,
            [sys.executable, '-c', "from pathlib import Path; Path('started').touch()"]),
            cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert result.returncode == 75
    assert not (tmp_path / 'started').exists()


def test_compatible_setup_is_reusable_while_workers_are_running(tmp_path):
    assert run_project_setup(tmp_path, 'true').ok
    with environment_in_use(tmp_path):
        reused = run_project_setup(tmp_path, 'true')
        assert reused.ok and not reused.ran
        assert not run_project_setup(tmp_path, 'true', force=True).ok
        (tmp_path / 'package.json').write_text('{"name":"changed"}')
        assert not run_project_setup(tmp_path, 'true').ok


def test_other_worktree_is_not_blocked_by_a_worker(tmp_path):
    _init_product_repo(tmp_path)
    first = _add_worker_worktree(tmp_path, '.runtime', 'first')
    second = _add_worker_worktree(tmp_path, '.runtime', 'second')
    with environment_in_use(first):
        assert run_project_setup(second, 'true').ok


def test_task_cleanup_checks_holder_even_with_zero_session_counters(tmp_path):
    from pathlib import Path
    from tests.test_task_workspace_binding import _manager, _prepare

    manager, role, base = _manager(tmp_path)
    plan = _prepare(manager, role, base, 'T')
    with environment_in_use(Path(plan.project_path)):
        result = manager.cleanup(plan, task_terminal=True, integrated_or_archived=True,
            active_attempts=0, active_sessions=0)
        assert result.status == 'blocked'
        assert Path(plan.project_path).exists()


def test_role_retirement_refuses_live_environment(tmp_path):
    from pathlib import Path
    from tests.test_workdirs import _init_repo, _reader_manager

    _init_repo(tmp_path)
    manager = _reader_manager(tmp_path)
    manager.config.runtime.workdirs.mode = 'worktree'
    role = manager.config.roles[0]
    plan = manager.prepare(role)
    with environment_in_use(Path(plan.project_path)):
        result = manager.remove(role)
        assert result.status == 'blocked'
        assert Path(plan.project_path).exists()


def test_existing_provision_links_are_reusable_with_active_workers(tmp_path):
    from zf.runtime.worktree_env import provision_worktree_env

    source, worktree = tmp_path / 'source', tmp_path / 'worker'
    source.mkdir()
    worktree.mkdir()
    (source / 'node_modules').mkdir()
    assert provision_worktree_env(worktree, source, ['node_modules']) == ['node_modules']
    with environment_in_use(worktree):
        assert provision_worktree_env(worktree, source, ['node_modules']) == []


def test_qualified_bootstrap_is_reusable_but_changed_input_waits(tmp_path):
    import pytest
    from zf.runtime.worktree_env import provision_worktree_env

    (tmp_path / 'pyproject.toml').write_text('[project]\nname="test"\nversion="0.1"\n')
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        (tmp_path / '.venv').mkdir(exist_ok=True)
        return subprocess.CompletedProcess(argv, 0, '', '')
    assert provision_worktree_env(tmp_path, tmp_path, ['.venv'], bootstrap_uv_dev=True, runner=runner) == ['.venv']
    with environment_in_use(tmp_path):
        assert provision_worktree_env(tmp_path, tmp_path, ['.venv'], bootstrap_uv_dev=True, runner=runner) == ['.venv']
        (tmp_path / 'uv.lock').write_text('changed')
        with pytest.raises(TimeoutError):
            provision_worktree_env(tmp_path, tmp_path, ['.venv'], bootstrap_uv_dev=True, runner=runner)
    assert len(calls) == 1


def test_missing_dependency_link_waits_and_does_not_mutate_live_workspace(tmp_path):
    import pytest
    from zf.runtime.worktree_env import provision_worktree_env

    source, worktree = tmp_path / 'source', tmp_path / 'worker'
    source.mkdir()
    worktree.mkdir()
    (source / 'node_modules').mkdir()
    with environment_in_use(worktree):
        with pytest.raises(TimeoutError):
            provision_worktree_env(worktree, source, ['node_modules'])
        assert not (worktree / 'node_modules').exists()
