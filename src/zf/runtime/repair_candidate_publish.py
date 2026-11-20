"""Publish one exited repair provider's bounded patch through a host Git gate."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from zf.core.state.locks import locked_path
from zf.runtime.project_git_baseline import _file
from zf.runtime.task_host_actions import _commit
from zf.runtime.task_host_manifest import digest, git
from zf.runtime.workflow_git_readiness import git_read
from zf.runtime.source_repair_apply import _hard_denied


def _changed_paths(root: Path, base: str) -> list[str]:
    return sorted((set(git(root, 'diff', base, '--name-only', '-z', '--').split('\0'))
                   | set(git(root, 'ls-files', '--others', '--exclude-standard', '-z').split('\0'))) - {''})


def publish_repair_candidate(*, writer, state_dir: Path, harness: Path, root: Path,
                             payload: dict, contract: dict, policy) -> str:
    if not (policy.enabled and policy.publish_candidate and policy.apply_policy == 'verified_checkpoint_apply'):
        return ''
    with locked_path(state_dir / 'locks' / 'repair-candidate-publish'):
        if (root.is_symlink() or root.resolve() != root or Path(git(root, 'rev-parse', '--show-toplevel')) != root
                or git(root, 'rev-parse', '--git-common-dir') == '.git'
                or git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir') != git(
                    harness, 'rev-parse', '--path-format=absolute', '--git-common-dir')):
            raise ValueError('repair candidate is not the dispatched harness worktree')
        base = payload['base_commit']
        if (contract['base_commit'] != base or git(root, 'rev-parse', 'HEAD') != base
                or git(root, 'symbolic-ref', '--short', 'HEAD') != payload['branch']
                or not payload['branch'].startswith('self-repair/')):
            raise ValueError('repair candidate branch/base binding changed')
        hooks = Path(git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'hooks'))
        if (git_read(root, 'config', '--get', 'core.hooksPath').stdout.strip()
                or any(p.is_file() and os.access(p, os.X_OK) and not p.name.endswith('.sample') for p in hooks.glob('*'))
                or git(root, 'diff', '--cached', '--name-only')):
            raise ValueError('repair publish refuses Git hooks or an existing staged index')
        names = _changed_paths(root, base)
        if not names:
            return ''
        for name in names:
            if (_hard_denied(name) or any(fnmatch.fnmatchcase(name, p) for p in policy.deny_paths)
                    or not any(fnmatch.fnmatchcase(name, p) for p in policy.allow_paths)
                    or not any(fnmatch.fnmatchcase(name, p) for p in contract['scope'])):
                raise ValueError(f'repair publish path outside authorized scope: {name}')
        git(root, 'var', 'GIT_AUTHOR_IDENT')
        git(root, 'var', 'GIT_COMMITTER_IDENT')
        def snapshot(checkout: Path) -> list[dict]:
            # Scan every delta path, including concurrent/untracked additions,
            # against the frozen base even after commit. Unchanged tracked test
            # fixtures are inherited history, not newly published source bytes.
            return [_file(checkout, name) for name in _changed_paths(checkout, base)]

        body = {'head': base, 'files': snapshot(root), 'paths': names,
                'repair_contract_digest': payload['repair_contract_digest']}
        key = digest(body)
        previous = [e for e in writer.event_log.read_all() if e.payload.get('repair_publish_digest') == key]
        if previous:
            raise ValueError('repair publish already attempted; inspect retained effect')
        start = writer.emit('runtime.action.attempt.started', actor='zf-self-repair', payload={
            'action': 'repair-candidate-publish', 'repair_publish_digest': key,
            'repair_contract_digest': payload['repair_contract_digest'], 'base_commit': base,
            'branch': payload['branch'], 'paths': names})
        result = _commit(root, {**body, 'manifest_digest': key}, source_snapshot=snapshot)
        writer.emit('runtime.action.attempt.completed', actor='zf-self-repair', causation_id=start.id,
            payload={'action': 'repair-candidate-publish', 'repair_publish_digest': key,
                     **result, 'product_verdict': 'not_evaluated'})
        return result['source_commit']
