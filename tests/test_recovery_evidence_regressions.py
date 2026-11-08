from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.rework_triage import classify_rework_trigger
from zf.runtime.task_refs import TaskRefManager
from tests.test_task_refs import _config, _git, _init_repo
from tests.test_task_pipeline_worker_liveness import runtime_case


@pytest.mark.parametrize('event_type,expected', [('test.failed', 'product_issue'), ('dev.blocked', 'ambiguous')])
def test_successful_docker_command_does_not_classify_as_network_failure(event_type, expected):
    result = classify_rework_trigger(ZfEvent(type=event_type, payload={
        'summary': 'Adapter returned SUCCEEDED for an unregistered model',
        'probe_receipts': [{'command': 'docker run --network host mcp/playwright:latest', 'status': 'passed'}],
        'known_gaps': ['Unregistered human backend must remain PENDING_HUMAN'],
    }))
    assert result.classification == expected


def test_failed_probe_diagnostic_still_identifies_environment():
    result = classify_rework_trigger(ZfEvent(type='test.failed', payload={
        'probe_receipts': [{'command': 'run-test', 'status': 'failed', 'error': 'connection refused'}],
    }))
    assert result.classification == 'environment_issue'


@pytest.mark.parametrize('name', ['中文证据.json', 'with space.json', 'with"quote.json', 'with\nnewline.json'])
@pytest.mark.parametrize('allowed', [True, False])
def test_scope_gate_uses_unquoted_git_paths(tmp_path: Path, name: str, allowed: bool):
    base = _init_repo(tmp_path)
    state = tmp_path / '.zf'
    state.mkdir()
    TaskStore(state / 'kanban.json').add(Task(id='TASK-1', status='in_progress',
        contract=TaskContract(scope=['app/evidence/scene/**'])))
    branch = _git(tmp_path, 'branch', '--show-current')
    _git(tmp_path, 'checkout', '-q', 'worker/dev')
    relative = f'app/evidence/{"scene" if allowed else "other"}/{name}'
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text('{}\n')
    _git(tmp_path, 'add', '--', relative)
    _git(tmp_path, 'commit', '-q', '-m', 'evidence')
    head = _git(tmp_path, 'rev-parse', 'HEAD')
    _git(tmp_path, 'checkout', '-q', branch)
    result = TaskRefManager(state_dir=state, project_root=tmp_path, config=_config(state)).process_dev_build_done(
        ZfEvent(type='dev.build.done', actor='dev', task_id='TASK-1',
            payload={'source_commit': head, 'source_branch': 'worker/dev', 'base_commit': base}))
    assert result.status == ('updated' if allowed else 'rejected')
    if not allowed:
        assert result.payload['out_of_scope_files'] == [relative]


@pytest.mark.parametrize('status', ['settled', 'blocked', 'failed', 'cancelled', 'superseded'])
def test_cleared_dispatch_after_terminal_does_not_reanimate_worker(tmp_path, monkeypatch, status):
    runtime, transport, _, _ = runtime_case(tmp_path, status=status)
    runtime.task_store.update('T1', active_dispatch_id='')
    transport.alive_flags['verify-1'] = False
    monkeypatch.setattr(runtime, '_worker_liveness_stale', lambda role: (True, 'old'))
    runtime._dead_threshold = 1
    assert runtime._active_task_for_instance('verify-1') is None
    runtime._capture_logs()
    assert transport.spawn_calls == []
    assert not any(e.type in {'worker.stuck', 'dispatch.paused'} for e in runtime.event_log.read_all())
