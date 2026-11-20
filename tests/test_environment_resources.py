"""Resource identity must follow shared dependency targets across workspaces."""
import subprocess
from pathlib import Path

import pytest

from zf.runtime.environment_usage import environment_in_use
from zf.runtime.worktree_env import provision_worktree_env, run_project_setup


def shared_workspaces(tmp_path):
    source, first, second = [tmp_path / name for name in ('source', 'first', 'second')]
    for root in (source, first, second):
        root.mkdir()
    (source / 'node_modules').mkdir()
    for root in (first, second):
        provision_worktree_env(root, source, ['node_modules'])
    return source, first, second


def test_setup_cannot_mutate_dependency_used_by_another_workspace(tmp_path):
    source, first, second = shared_workspaces(tmp_path)
    with environment_in_use(first):
        for root in (second, source):
            result = run_project_setup(root, 'touch node_modules/corrupted')
            assert not result.ok and not result.ran
        assert not (source / 'node_modules/corrupted').exists()
    assert run_project_setup(second, 'touch node_modules/installed').ok


def test_shared_consumers_and_compatible_reuse_remain_parallel(tmp_path):
    source, first, second = shared_workspaces(tmp_path)
    with environment_in_use(first), environment_in_use(second):
        assert provision_worktree_env(second, source, ['node_modules']) == []


def test_native_process_holds_shared_target_and_retargeted_launch_is_refused(tmp_path):
    import select
    import sys
    from zf.runtime.environment_usage import guarded_provider_argv

    source, first, second = shared_workspaces(tmp_path)
    command = guarded_provider_argv(first, [sys.executable, '-c', "print('ready', flush=True); input()"])
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 5)[0]
        assert process.stdout.readline().strip() == 'ready'
        assert not run_project_setup(second, 'touch node_modules/damaged').ok
        assert not run_project_setup(source, 'touch node_modules/damaged').ok
    finally:
        process.communicate('exit\n', timeout=5)
    assert process.returncode == 0
    old_command = guarded_provider_argv(first, [sys.executable, '-c', "print('must not launch')"])
    replacement = tmp_path / 'different-modules'
    replacement.mkdir()
    (first / 'node_modules').unlink()
    (first / 'node_modules').symlink_to(replacement)
    refused = subprocess.run(old_command, capture_output=True, text=True, timeout=5)
    assert refused.returncode == 75 and not refused.stdout


def test_resource_redeclaration_cannot_bypass_live_holder(tmp_path):
    from zf.runtime.environment_resources import register_environment_resources

    _, first, _ = shared_workspaces(tmp_path)
    with environment_in_use(first):
        with pytest.raises(TimeoutError):
            register_environment_resources(first, ['new-dependency'])


def test_git_shared_resources_survive_nested_parent_creation(tmp_path):
    from tests.test_workdirs import _init_repo
    from zf.runtime.environment_resources import register_environment_resources, environment_lock_plan

    _init_repo(tmp_path)
    register_environment_resources(tmp_path, ['new/nested/node_modules'])
    before = environment_lock_plan(tmp_path)
    (tmp_path / 'new/nested').mkdir(parents=True)
    assert environment_lock_plan(tmp_path)['locks'] == before['locks']


def test_stop_retains_source_worktree_while_another_workspace_uses_its_dependencies(tmp_path, capsys):
    from tests.test_stop_clean_workdirs import _init_product_repo, _add_worker_worktree
    from zf.cli.stop import clean_state_dir_workdirs

    _init_product_repo(tmp_path)
    source = _add_worker_worktree(tmp_path, '.runtime', 'source')
    (source / 'node_modules').mkdir()
    consumer = tmp_path / 'consumer'
    consumer.mkdir()
    provision_worktree_env(consumer, source, ['node_modules'])
    with environment_in_use(consumer):
        assert clean_state_dir_workdirs(tmp_path, tmp_path / '.runtime')[0] == 0
    assert source.exists()
    assert 'resource in use' in capsys.readouterr().err


def test_replaced_dependency_invalidates_setup_readiness(tmp_path):
    source, first, _ = shared_workspaces(tmp_path)
    script = "printf 'install\\n' >> installations"
    assert run_project_setup(first, script).ran
    assert not run_project_setup(first, script).ran
    (source / 'node_modules').rename(source / 'retired-modules')
    (source / 'node_modules').mkdir()
    result = run_project_setup(first, script)
    assert result.ok and result.ran
    assert (first / 'installations').read_text().splitlines() == ['install', 'install']


def test_native_launch_refuses_replaced_instance_at_same_path(tmp_path):
    import sys
    from zf.runtime.environment_usage import guarded_provider_argv

    source, first, _ = shared_workspaces(tmp_path)
    command = guarded_provider_argv(first, [sys.executable, '-c', "print('must not launch')"])
    (source / 'node_modules').rename(source / 'old-modules')
    (source / 'node_modules').mkdir()
    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    assert result.returncode == 75 and not result.stdout


def test_corrupt_resource_manifest_cannot_disable_execution_fence(tmp_path):
    from zf.runtime.environment_resources import resource_manifest
    from zf.runtime.environment_usage import guarded_provider_argv

    _, first, _ = shared_workspaces(tmp_path)
    resource_manifest(first).write_text('{}')
    with pytest.raises(ValueError, match='resource manifest'):
        guarded_provider_argv(first, ['must-not-launch'])


def test_locks_do_not_create_missing_dependency_directories(tmp_path):
    from zf.runtime.environment_resources import register_environment_resources, environment_lock_plan

    register_environment_resources(tmp_path, ['node_modules', 'node_modules/nested/package'])
    before = environment_lock_plan(tmp_path)
    with environment_in_use(tmp_path):
        assert not (tmp_path / 'node_modules').exists()
    (tmp_path / 'node_modules/nested').mkdir(parents=True)
    assert environment_lock_plan(tmp_path)['locks'] == before['locks']
