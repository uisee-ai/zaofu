"""Default zf-init hooks remain active during preauthorized publication."""
import hashlib

import pytest

from tests.test_task_host_actions import case, approve
from tests.test_task_publication import prepare
from zf.core.workspace.git_hooks import PRE_COMMIT_HOOK, install_pre_commit_hook
from zf.runtime.task_host_manifest import git, preview
from zf.runtime.task_publication import reconcile_task_publication


def test_initialized_project_can_publish_without_bypassing_its_hook(case, monkeypatch):
    service, root = prepare(case)
    assert install_pre_commit_hook(service.project_root) == 'installed'
    from zf.runtime import task_host_actions
    run = task_host_actions.subprocess.run
    hook_output = []

    def observed(*args, **kwargs):
        result = run(*args, **kwargs)
        if args and isinstance(args[0], list) and 'commit' in args[0]:
            hook_output.append(result.stderr)
        return result

    monkeypatch.setattr(task_host_actions.subprocess, 'run', observed)
    before = git(root, 'rev-parse', 'HEAD')
    assert reconcile_task_publication(service) == 1
    assert git(root, 'rev-parse', 'HEAD') != before
    assert hook_output and 'pre-commit: 即将提交 1 个文件' in hook_output[0]
    hook = service.project_root / '.git/hooks/pre-commit'
    assert hook.read_text() == PRE_COMMIT_HOOK
    assert reconcile_task_publication(service) == 1
    assert reconcile_task_publication(service) == 0
    events = service.writer.event_log.read_all()
    assert len([e for e in events if e.type == 'task.rework.requested']) == 1
    assert not any(e.type in {'task.done', 'dev.build.done'} for e in events)


@pytest.mark.parametrize('change', ['modified', 'extra', 'configured', 'symlink', 'directory_symlink'])
def test_unknown_or_indirected_hooks_still_need_review(case, change):
    service, root, _ = case
    install_pre_commit_hook(service.project_root)
    hooks = service.project_root / '.git/hooks'
    hook = hooks / 'pre-commit'
    if change == 'modified':
        hook.write_text(PRE_COMMIT_HOOK + '\nexit 0\n')
    elif change == 'extra':
        extra = hooks / 'commit-msg'
        extra.write_text('#!/bin/sh\nexit 0\n'); extra.chmod(0o755)
    elif change == 'configured':
        git(root, 'config', 'core.hooksPath', str(hooks))
    elif change == 'symlink':
        target = hooks / 'trusted.sample'; hook.rename(target); hook.symlink_to(target)
    else:
        target = hooks.with_name('other-hooks'); hooks.rename(target); hooks.symlink_to(target, target_is_directory=True)
    before = git(root, 'rev-parse', 'HEAD')
    with pytest.raises(ValueError, match='hooks require separate review'):
        preview(service, operation_id='op-1', kind='commit', paths=['probe.py'])
    assert git(root, 'rev-parse', 'HEAD') == before
    assert not git(root, 'diff', '--cached', '--name-only')


@pytest.mark.parametrize('change', ['removed', 'modified', 'mode'])
def test_hook_review_is_pinned_and_rechecked_before_staging(case, change):
    service, root, _ = case
    install_pre_commit_hook(service.project_root)
    hook = service.project_root / '.git/hooks/pre-commit'
    manifest = preview(service, operation_id='op-1', kind='commit', paths=['probe.py'])
    assert manifest['git_hooks'][0]['sha256'] == hashlib.sha256(PRE_COMMIT_HOOK.encode()).hexdigest()
    before = git(root, 'rev-parse', 'HEAD')
    if change == 'removed': hook.unlink()
    elif change == 'modified': hook.write_text('#!/bin/sh\nexit 0\n')
    else: hook.chmod(0o744)
    with pytest.raises(ValueError, match='stale|hooks require separate review'):
        approve(service, manifest)
    assert git(root, 'rev-parse', 'HEAD') == before
    assert not git(root, 'diff', '--cached', '--name-only')
