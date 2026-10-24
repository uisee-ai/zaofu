"""Supervised checkpoints preserve actual worker edits, not only root diffs."""

import base64
import json
import subprocess
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, RuntimeConfig, WorkdirConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.maintenance import create_checkpoint
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _git(root, *args, input=None):
    return subprocess.run(['git', *args], cwd=root, input=input, capture_output=True, check=True).stdout


def _project(root):
    root.mkdir(parents=True)
    _git(root, 'init', '-q')
    _git(root, 'config', 'user.name', 'Checkpoint Test')
    _git(root, 'config', 'user.email', 'checkpoint@example.invalid')
    (root / 'app.py').write_text('base\n')
    _git(root, 'add', '--', 'app.py')
    _git(root, 'commit', '-qm', 'test: checkpoint base')
    return _git(root, 'rev-parse', 'HEAD').decode().strip()


def test_checkpoint_roundtrip_preserves_index_worktree_and_untracked_binary(tmp_path):
    root = tmp_path / 'project'
    head = _project(root)
    state = tmp_path / 'runtime-state'
    (root / 'app.py').write_text('staged\n')
    _git(root, 'add', '--', 'app.py')
    (root / 'app.py').write_text('unstaged\n')
    content = b'\x00\xffnew file\n'
    (root / 'new.bin').write_bytes(content)
    (root / 'new.bin').chmod(0o755)
    checkpoint = create_checkpoint(state, project_root=root, task_id='TASK-1')
    snapshot = hydrate_sidecar_ref(state, checkpoint.workspace_snapshot_ref).payload
    assert snapshot['head'] == head
    assert snapshot['status'] == 'captured'
    restore = tmp_path / 'restore'
    _git(tmp_path, 'clone', '-q', str(root), str(restore))
    _git(restore, 'apply', '--index', '--binary', input=base64.b64decode(snapshot['staged_patch_base64']))
    _git(restore, 'apply', '--binary', input=base64.b64decode(snapshot['unstaged_patch_base64']))
    for entry in snapshot['untracked']:
        target = restore / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(entry['content_base64']))
        target.chmod(entry['mode'])
    assert _git(restore, 'show', ':app.py') == b'staged\n'
    assert (restore / 'app.py').read_bytes() == b'unstaged\n'
    assert (restore / 'new.bin').read_bytes() == content
    assert (restore / 'new.bin').stat().st_mode & 0o777 == 0o755
    assert _git(root, 'status', '--porcelain') == _git(restore, 'status', '--porcelain')


def test_controlled_checkpoint_uses_configured_worker_workspace(tmp_path):
    project = tmp_path / 'main'
    _project(project)
    state = tmp_path / 'custom-runtime'
    worker = state / 'isolated/dev-1/project'
    head = _project(worker)
    (worker / 'app.py').write_text('worker edit\n')
    cfg = ZfConfig(project=ProjectConfig(name='test'), roles=[RoleConfig(name='dev-1')],
                   runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True, mode='worktree', root=str(state / 'isolated'))))
    TaskStore(state / 'kanban.json').add(Task(id='TASK-1', title='source task', assigned_to='dev-1'))
    log = EventLog(state / 'events.jsonl')
    writer = EventWriter(log)
    request = writer.emit('control.action.requested', actor='operator', payload={'action': 'maintenance-prepare'})
    result = ControlledActionService(state, writer, config=cfg, project_root=project).execute(
        action='maintenance-prepare', requested_action='maintenance-prepare', requested=request,
        payload={'trigger_id': 'repair-1', 'task_id': 'TASK-1', 'checkpoint': True, 'assigned_worker': 'dev-1'},
    )
    assert result['ok'] is True
    packet = json.loads(Path(result['checkpoint_path']).read_text())
    assert packet['worktree_root'] == str(worker)
    snapshot = hydrate_sidecar_ref(state, packet['workspace_snapshot_ref']).payload
    assert snapshot['head'] == head
    assert b'worker edit' in base64.b64decode(snapshot['unstaged_patch_base64'])


def test_checkpoint_ids_do_not_overwrite_same_second(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from zf.runtime import maintenance

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 24, tzinfo=timezone.utc)

    monkeypatch.setattr(maintenance, 'datetime', FrozenDatetime)
    root = tmp_path / 'project'
    _project(root)
    state = tmp_path / 'runtime'
    first = create_checkpoint(state, project_root=root, task_id='TASK-1')
    before = Path(first.resume_packet_path).read_bytes()
    (root / 'app.py').write_text('later\n')
    second = create_checkpoint(state, project_root=root, task_id='TASK-1')
    assert first.checkpoint_id != second.checkpoint_id
    assert Path(first.resume_packet_path).read_bytes() == before


def test_snapshot_excludes_custom_runtime_and_keeps_symlink_without_following(tmp_path):
    root = tmp_path / 'project'
    _project(root)
    state = root / 'custom-runtime'
    state.mkdir()
    (state / 'private-state').write_text('not source')
    (root / 'outside-link').symlink_to(tmp_path / 'outside-secret')
    (tmp_path / 'outside-secret').write_text('do not follow')
    checkpoint = create_checkpoint(state, project_root=root, task_id='TASK-1')
    snapshot = hydrate_sidecar_ref(state, checkpoint.workspace_snapshot_ref).payload
    assert snapshot['status'] == 'captured'
    assert snapshot['untracked'] == [{'path': 'outside-link', 'kind': 'symlink',
                                      'target': str(tmp_path / 'outside-secret')}]
    assert 'do not follow' not in json.dumps(snapshot)
    assert snapshot['provider_quiescence'] == 'unverified'


@pytest.mark.parametrize('kind', ['not_git', 'too_large', 'changing'])
def test_incomplete_snapshot_is_explicitly_unavailable(tmp_path, monkeypatch, kind):
    from zf.runtime import maintenance_worktree as module
    root = tmp_path / 'project'
    if kind == 'not_git':
        root.mkdir()
    else:
        _project(root)
    if kind == 'too_large':
        monkeypatch.setattr(module, 'MAX_SNAPSHOT_BYTES', 64)
        (root / 'large.bin').write_bytes(b'x' * 128)
    if kind == 'changing':
        calls = iter([{'head': 'before'}, {'head': 'after'}])
        monkeypatch.setattr(module, '_capture', lambda *_: next(calls))
    state = tmp_path / 'state'
    checkpoint = create_checkpoint(state, project_root=root, task_id='TASK-1')
    snapshot = hydrate_sidecar_ref(state, checkpoint.workspace_snapshot_ref).payload
    assert snapshot['status'] == 'unavailable'
    assert snapshot['reason']
    assert 'untracked' not in snapshot
    assert root.exists()


def test_worker_resolution_rejects_wrong_owner_and_missing_workdir(tmp_path):
    from zf.runtime.maintenance_worktree import checkpoint_root
    state = tmp_path / 'state'
    cfg = ZfConfig(project=ProjectConfig(name='test'), roles=[RoleConfig(name='dev-1')],
                   runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True, mode='worktree', root=str(state / 'workers'))))
    TaskStore(state / 'kanban.json').add(Task(id='T', title='task', assigned_to='dev-1'))
    with pytest.raises(ValueError, match='assignee'):
        checkpoint_root(state, tmp_path, cfg, 'T', 'dev-2')
    with pytest.raises(ValueError, match='missing'):
        checkpoint_root(state, tmp_path, cfg, 'T', '')


def test_checkpoint_cli_infers_canonical_worker(tmp_path, monkeypatch, capsys):
    from zf.cli.main import main
    from zf.core.config.project_context import ProjectContext

    state = tmp_path / 'runtime'
    project = tmp_path / 'main'
    _project(project)
    worker = state / 'workers/dev-1/project'
    _project(worker)
    (worker / 'app.py').write_text('CLI worker edit\n')
    cfg = ZfConfig(project=ProjectConfig(name='test'), roles=[RoleConfig(name='dev-1')],
                   runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True, mode='worktree', root=str(state / 'workers'))))
    TaskStore(state / 'kanban.json').add(Task(id='T', title='task', assigned_to='dev-1'))
    context = ProjectContext(project, project / 'zf.yaml', cfg, state)
    monkeypatch.setattr('zf.cli.autoresearch.resolve_project_context', lambda **_: context)
    assert main(['autoresearch', 'self-repair', 'checkpoint', '--task', 'T']) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet['worktree_root'] == str(worker)
    snapshot = hydrate_sidecar_ref(state, packet['workspace_snapshot_ref']).payload
    assert b'CLI worker edit' in base64.b64decode(snapshot['unstaged_patch_base64'])


@pytest.mark.parametrize('task_id', ['A/../../escape', r'A\..\escape'])
def test_checkpoint_rejects_path_components_before_writing(tmp_path, task_id):
    root = tmp_path / 'project'
    _project(root)
    state = tmp_path / 'state'
    with pytest.raises(ValueError, match='task_id must be a file-safe identifier'):
        create_checkpoint(state, project_root=root, task_id=task_id)
    assert not state.exists()
