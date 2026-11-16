import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.repair_candidate_publish import publish_repair_candidate
from zf.runtime.task_host_manifest import git


def test_kernel_publishes_only_authorized_patch_after_provider_exit(tmp_path):
    root = tmp_path / 'harness'; root.mkdir()
    def run(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    run('init', '-b', 'dev'); run('config', 'user.name', 'Test'); run('config', 'user.email', 'test@example.invalid')
    (root / 'base').write_text('base'); run('add', '--', 'base'); run('commit', '-m', 'chore: seed')
    base = run('rev-parse', 'HEAD'); work = tmp_path / 'repair'
    run('worktree', 'add', '-b', 'self-repair/test-a1', str(work))
    writer = EventWriter(EventLog(tmp_path / 'state/events.jsonl'))
    payload = {'base_commit': base, 'branch': 'self-repair/test-a1', 'repair_contract_digest':'a' * 64}
    contract = {'base_commit':base, 'scope':['src/zf/**']}
    policy = SimpleNamespace(enabled=True, publish_candidate=True, apply_policy='verified_checkpoint_apply',
                             allow_paths=['src/zf/**'], deny_paths=['**/events.jsonl'])
    (work / 'src/zf').mkdir(parents=True)
    (work / 'src/zf/fix.py').write_text('fixed = True\n')
    head = publish_repair_candidate(writer=writer, state_dir=tmp_path/'state', harness=root, root=work,
                                    payload=payload, contract=contract, policy=policy)
    assert head != base and git(work, 'rev-parse', 'HEAD^') == base
    assert run('rev-parse', 'HEAD') == base  # isolated publication is not apply
    assert git(work, 'status', '--porcelain') == ''
    assert not any(e.type == 'task.done' for e in writer.event_log.read_all())


def test_publisher_disabled_is_inert(tmp_path):
    result = publish_repair_candidate(writer=None, state_dir=tmp_path, harness=tmp_path, root=tmp_path,
        payload={}, contract={}, policy=SimpleNamespace(enabled=False))
    assert result == ''
