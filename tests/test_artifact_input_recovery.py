from zf.runtime.artifact_input_page import expand_input_items
import json

import pytest

from tests.test_artifact_read_snapshot import published, IDENTITY
from tests.test_artifact_read_capability import _grant
from tests.test_artifact_input_lifecycle import admission, binding
from zf.cli.main import main
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.cli.artifact_required_read import prepare_required_read
from zf.runtime.artifact_input_recovery import build_input_recovery, current_task_input_recovery
from zf.runtime.artifact_read_ledger import active_read_ledger_path
from zf.runtime.recovery import build_recovery_briefing
from zf.runtime.recovery_context import build_task_recovery_context


def current_dispatch(state, required):
    task = Task(id='T7', title='navigation task', status='in_progress', assigned_to='dev-1',
        active_dispatch_id='a', execution_binding=TaskExecutionBinding(workflow_run_id='r'))
    TaskStore(state / 'kanban.json').add(task)
    TaskAttemptStore(state / 'task_attempts.json').ensure_for_dispatch(
        run_id='r', task_id='T7', dispatch_id='a', role='dev', instance_id='dev-1',
        operation_id='op', briefing_ref='', created_at='2026-11-02T00:00:00+00:00',
        lease_expires_at='', max_attempts=3)
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    for kind in ['workflow.operation.requested', 'workflow.operation.started', 'task.dispatched']:
        writer.append(ZfEvent(type=kind, task_id='T7', payload={'workflow_run_id': 'r',
            'operation_id': 'op', 'active_attempt_id': 'a', 'dispatch_id': 'a',
            'role_instance': 'dev-1', 'required_reads': required}))
    return task, writer


def test_authenticated_recovery_cli_runtime_briefing_and_advisor_share_inputs(tmp_path, monkeypatch, capsys):
    state = tmp_path / 'custom-state'
    manifest, required, _ = published(state, body={'shared': 'rules', 'tasks': [{} for _ in range(8)]})
    task, writer = current_dispatch(state, required)
    _grant(state, monkeypatch, manifest=manifest, operation_id='op')
    assert main(['artifact', 'recover', '--attempt', 'a', '--state-dir', str(state)]) == 0
    cli = json.loads(capsys.readouterr().out)
    assert cli['status'] == 'bound' and cli['required_visible'] == cli['historical_missing'] == 2
    assert cli['current_memory'] == 'unknown' and cli['authorizes_execution'] is False
    assert len(json.dumps(cli).encode()) <= 8192
    events = EventLog(state / 'events.jsonl').read_all()
    runtime = current_task_input_recovery(state, events, task, actor='dev-1')
    assert runtime['inputs']['items'] == cli['inputs']['items']
    advisory = build_task_recovery_context(state, events, task_id='T7')
    assert advisory['input_recovery'] == runtime
    briefing = build_recovery_briefing(state, 'dev-1', task, compact=True)
    assert 'Input recovery (projection, not execution authority)' in briefing
    assert 'artifact recover --attempt a' in briefing
    assert not active_read_ledger_path(state, 'a').exists()
    # New dispatch/session cannot revive previous authority.
    task.active_dispatch_id = 'new-dispatch'
    assert current_task_input_recovery(state, events, task)['status'] == 'unavailable'
    task.active_dispatch_id = 'a'
    writer.append(ZfEvent(type='workflow.operation.cancelled', payload={'operation_id': 'op',
        'workflow_run_id': 'r', 'active_attempt_id': 'a'}))
    assert current_task_input_recovery(state, EventLog(state / 'events.jsonl').read_all(), task)['status'] == 'unavailable'
    assert main(['artifact', 'recover', '--attempt', 'a', '--state-dir', str(state)]) == 1
    assert '"sources"' not in capsys.readouterr().out


def test_history_never_proves_current_memory_or_another_roles_consumption(tmp_path):
    manifest, rows, _ = published(tmp_path, body={'shared': 'rules', 'tasks': [{} for _ in range(8)]})
    _, receipts = prepare_required_read(tmp_path, manifest=manifest, rows=rows, identity=IDENTITY)
    own = build_input_recovery(state_dir=tmp_path, manifest=manifest, identity=IDENTITY,
        events=[], required=rows, reads=receipts)
    assert own['historical_missing'] == 0
    assert own['reconstruction_required'] == 2 and own['current_memory'] == 'unknown'
    other = build_input_recovery(state_dir=tmp_path, manifest=manifest,
        identity={**IDENTITY, 'actor': 'verify-1', 'role': 'verify'}, events=[], required=rows, reads=receipts)
    assert other['historical_missing'] == 2
    assert all(i['read_status'] == 'missing' for i in expand_input_items(other['inputs']))
    assert not active_read_ledger_path(tmp_path, 'a').exists()


def test_recovery_filters_before_counts_and_requires_rebind_for_old_package(tmp_path):
    manifest, rows, _ = published(tmp_path, aliases=True)
    rows.append({**rows[0], 'json_path': '$.tasks[99]'})
    args = dict(state_dir=tmp_path, manifest=manifest, identity=IDENTITY, events=[], required=rows, reads=[])
    pack = build_input_recovery(**args)
    assert pack['required_visible'] == 2 and 'alias' not in json.dumps(pack)
    manifest.update(binding())
    pack = build_input_recovery(**{**args, 'events': [admission(), admission('2')]})
    assert pack['status'] == 'rebind_required' and pack['next_action'] == 'request_controlled_rebind'
    assert 'required_read' not in pack['commands'] and pack['authorizes_execution'] is False


@pytest.mark.parametrize('tamper', [False, True])
def test_current_checkpoint_is_attached_and_revalidated(tmp_path, tamper):
    from tests.test_maintenance_worktree_checkpoint import _project
    from zf.runtime.maintenance import create_checkpoint
    root, state = tmp_path / 'project', tmp_path / 'state'
    _project(root)
    _, required, _ = published(state)
    task, _ = current_dispatch(state, required)
    (state / 'feature_list.json').write_text('[]')
    checkpoint = create_checkpoint(state, project_root=root, task_id='T7', assigned_worker='dev-1')
    if tamper:
        (state / checkpoint.workspace_snapshot_ref['ref']).write_text('{}')
    events = EventLog(state / 'events.jsonl').read_all()
    pack = current_task_input_recovery(state, events, task)
    assert pack['status'] == 'bound'
    assert pack['checkpoint']['status'] == ('unavailable' if tamper else 'bound')
    assert pack['checkpoint']['authorizes_continuation'] is False


def test_orchestrator_transports_real_recovery_input_pack(tmp_path, monkeypatch):
    from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.transport import TmuxTransport
    from zf.runtime.tmux import TmuxSession
    from zf.core.state.session import SessionStore
    state = tmp_path / 'state'
    _, required, _ = published(state)
    current_dispatch(state, required)
    SessionStore(state / 'session.yaml').create(project_root=str(tmp_path))
    role = RoleConfig(name='dev', instance_id='dev-1', backend='codex')
    runtime = Orchestrator(state, ZfConfig(project=ProjectConfig(name='test'), roles=[role]),
        TmuxTransport(TmuxSession(session_name='test', dry_run=True)))
    # Contract sufficiency has its own gates/tests; this test starts after that
    # precondition and uses the actual builder, file handoff and transport caller.
    monkeypatch.setattr(runtime, '_ensure_recovery_contract_sufficient', lambda *_: True)
    delivered = []
    monkeypatch.setattr(runtime, '_send_transport_task', lambda actor, path, prompt, context:
        delivered.append((actor, path.read_text(), prompt)))
    assert runtime._inject_recovery_briefing(role)
    assert len(delivered) == 1 and delivered[0][0] == 'dev-1'
    assert 'artifact recover --attempt a' in delivered[0][1]
    assert '"current_memory": "unknown"' in delivered[0][1]
    assert '"authorizes_execution": false' in delivered[0][1]
    assert not active_read_ledger_path(state, 'a').exists()
