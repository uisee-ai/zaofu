"""Native files and the actual managed observer, including a persistent provider."""

from datetime import datetime, timezone
import json
import time
from types import SimpleNamespace

import pytest

from tests.test_controller_managed import managed as managed, workspace as workspace
from zf.core.config.schema import RoleConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.integrations.controller.execution import Execution
from zf.integrations.controller.native import delta, role_snapshot, snapshot
from zf.integrations.controller.tmux import observe
from zf.integrations.controller.workspace import synchronize_workspace
from zf.runtime.codex_turn_boundary import completed_turn, first_turn


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def append(path, *records):
    with path.open('a') as stream:
        for record in records:
            stream.write(json.dumps(record) + '\n')


def event(kind, when, **payload):
    return {'type': 'event_msg', 'timestamp': iso(when), 'payload': {'type': kind, **payload}}


@pytest.fixture
def native(managed, tmp_path, monkeypatch):
    _, _, context, _ = managed
    home = context.state_dir / 'workdirs/resident/codex-home'
    monkeypatch.setenv('CODEX_HOME', str(home))
    registry = RoleSessionRegistry(context.state_dir / 'role_sessions.yaml', context.project_root)
    identity = str(registry.get_or_create('resident', backend='codex'))
    path = home / 'sessions/2026/11/17' / f'rollout-fixture-{identity}.jsonl'
    path.parent.mkdir(parents=True)
    path.touch()
    append(path, {'type': 'session_meta', 'timestamp': iso(time.time() - 100),
                  'payload': {'id': identity, 'cwd': str(context.project_root), 'model_provider': 'openai'}},
           {'type': 'turn_context', 'payload': {'model': 'fixture'}})
    return path, registry, identity


def receipt(path, when, total=100):
    append(path, event('token_count', when, rate_limits={'plan_type': 'plus'}, info={
        'total_token_usage': {'input_tokens': total, 'cached_input_tokens': total // 2,
                             'output_tokens': 10, 'reasoning_output_tokens': 0}}))


def test_live_tui_settles_only_complete_native_turn_with_proven_empty_baseline(managed, native, monkeypatch):
    config, peer, context, credentials = managed
    path, registry, identity = native
    before = snapshot(context, 'codex', identity)
    assert before['status'] == 'awaiting_usage'
    execution = Execution((config, credentials, context), operation_id='workflow-test',
                          provider='codex', model='fixture', session_id=identity).begin(lambda: None)
    started = execution.body['started_at'] + .01
    append(path, event('task_started', started, turn_id='turn-a'))
    receipt(path, started + .01)
    append(path, event('token_count', started + .01, rate_limits={'primary': {
        'window_minutes': 300, 'used_percent': 8.5}}))
    partial = snapshot(context, 'codex', identity)
    assert not completed_turn(before, partial, started_at=execution.body['started_at'], session_id=identity)
    calls = []

    def complete(_):
        assert not peer.settlements and not calls
        calls.append(True)
        append(path, event('task_complete', started + .02, turn_id='turn-a', last_agent_message='private body'))

    monkeypatch.setattr('zf.integrations.controller.tmux.time', SimpleNamespace(sleep=complete))
    observe(SimpleNamespace(is_alive=lambda _: True), 'resident', registry,
            RoleConfig(name='resident', backend='codex'), before, execution)
    assert calls == [True]
    assert execution.stop.is_set() and execution.body['release']['status'] == 'released'
    synchronize_workspace(config)
    assert len(peer.settlements) == 1
    tokens = peer.settlements[0]['tokens']
    assert tokens['input_tokens'] == tokens['cache_read_tokens'] == 50
    assert tokens['output_tokens'] == 10
    assert 'private body' not in json.dumps(execution.body)
    from zf.integrations.controller.health import deliver
    reported = []
    target = SimpleNamespace(hello={'capabilities':['subscription_windows_v1']},
                             request=lambda _, path, body: reported.append(body) or {'status':'accepted'})
    assert deliver(config.state_dir, target) == 0
    windows = [r['subscription_window'] for r in reported if r.get('subscription_window')]
    assert windows == [{'name':'primary','minutes':300,'used_percent':8.5,'resets_at':None}]


def test_native_resumed_turn_subtracts_previous_complete_counters(managed, native):
    _, _, context, _ = managed
    path, _, identity = native
    started = time.time()
    append(path, event('task_started', started, turn_id='one'))
    receipt(path, started + .01)
    append(path, event('task_complete', started + .02, turn_id='one'))
    before = snapshot(context, 'codex', identity)
    append(path, event('task_started', started + 1, turn_id='two'))
    receipt(path, started + 1.01, total=150)
    append(path, event('task_complete', started + 1.02, turn_id='two'))
    after = snapshot(context, 'codex', identity)
    assert completed_turn(before, after, started_at=started + .5, session_id=identity)
    assert not first_turn(after, started_at=started + .5, session_id=identity)
    assert delta(before, after)['input_tokens'] == 25


def test_stopped_fresh_tui_reports_known_partial_usage_without_final_settlement(managed, native):
    config, peer, context, credentials = managed
    path, registry, identity = native
    execution = Execution((config, credentials, context), operation_id='interrupted-workflow',
                          provider='codex', model='fixture', session_id=identity).begin(lambda: None)
    started = execution.body['started_at'] + .01
    # Native startup can lag budget admission; a fresh session does not prove finality.
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]['timestamp'] = iso(started)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    append(path, event('task_started', started + .01, turn_id='incomplete'))
    receipt(path, started + .02)
    observe(SimpleNamespace(is_alive=lambda _: False), 'resident', registry,
            RoleConfig(name='resident', backend='codex'), {}, execution)
    synchronize_workspace(config)
    records = [item['payload'] for item in peer.records.values() if item['payload']['kind'] == 'usage']
    assert len(records) == 1
    assert records[0]['tokens']['input_tokens'] == 50
    assert records[0]['completeness'] == 'partial'
    assert not peer.settlements
    assert execution.ledger.get(execution.id)['status'] == 'settlement_pending'


@pytest.mark.parametrize('ending', ['completed', 'running', 'aborted', 'earlier_aborted'])
def test_terminal_stop_requires_all_native_turns_complete(managed, native, ending):
    from zf.integrations.controller.terminal_execution import observe as terminal_observe
    from zf.web.terminal_usage import TerminalUsageService
    config, peer, context, credentials = managed
    path, _, identity = native
    execution = Execution((config, credentials, context), operation_id='terminal-test',
                          provider='codex', model='fixture', session_id='public-terminal').begin(lambda: None)
    started = execution.body['started_at'] + .01
    append(path, event('task_started', started, turn_id='first'))
    receipt(path, started + .01)
    if ending == 'earlier_aborted':
        append(path, event('turn_aborted', started + .02, turn_id='first'),
               event('task_started', started + .03, turn_id='second'))
        receipt(path, started + .04, total=200)
        append(path, event('task_complete', started + .05, turn_id='second'))
    elif ending != 'running':
        append(path, event('task_complete' if ending == 'completed' else 'turn_aborted',
                           started + .02, turn_id='first'))
    usage = TerminalUsageService(state_dir=context.state_dir)
    current = SimpleNamespace(session_id='public-terminal', provider='codex', provider_session_id=identity,
                              state='stopped')
    previous = {**snapshot(context, 'codex', identity), 'fresh_input_tokens': 1, 'cached_input_tokens': 1}
    service = SimpleNamespace(project_root=context.project_root, get_session=lambda _: current,
        usage=SimpleNamespace(snapshot=lambda _: previous,
                              native_snapshot=usage.native_snapshot))
    terminal_observe(service, current, execution)
    synchronize_workspace(config)
    records = [item['payload'] for item in peer.records.values() if item['payload']['kind'] == 'usage']
    assert records[0]['completeness'] == ('complete' if ending == 'completed' else 'partial')
    # Counter and completion evidence must come from the same final native read.
    assert records[0]['tokens']['input_tokens'] == (100 if ending == 'earlier_aborted' else 50)
    assert bool(peer.settlements) == (ending == 'completed')


def test_aborted_turn_without_receipt_is_never_zero_baselined_on_resume(managed, native):
    _, _, context, _ = managed
    path, _, identity = native
    started = time.time()
    append(path, event('task_started', started, turn_id='one'),
           event('turn_aborted', started + .01, turn_id='one'))
    before = snapshot(context, 'codex', identity)
    append(path, event('task_started', started + 1, turn_id='two'))
    receipt(path, started + 1.01)
    append(path, event('task_complete', started + 1.02, turn_id='two'))
    after = snapshot(context, 'codex', identity)
    assert completed_turn(before, after, started_at=started + .5, session_id=identity)
    fresh = first_turn(after, started_at=started + .5, session_id=identity)
    assert fresh is False and delta(before, after, fresh=fresh) == {}


def test_role_accounting_uses_isolated_home_and_exact_worktree_cwd(managed, native, tmp_path, monkeypatch):
    _, _, context, _ = managed
    path, _, identity = native
    role = RoleConfig(name='resident', backend='codex')
    worker = tmp_path / 'worker-worktree'
    worker.mkdir()
    path.write_text(path.read_text().replace(str(context.project_root), str(worker)))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'unrelated-home'))
    assert snapshot(context, 'codex', identity)['status'] == 'unavailable'
    assert role_snapshot(context, role, identity)['status'] == 'unavailable'
    assert role_snapshot(context, role, identity, cwd=worker)['turn_boundary']['session_id'] == identity


@pytest.mark.parametrize('damage', ['partial_json', 'non_object', 'wrong_turn', 'wrong_project',
                                  'wrong_identity', 'no_usage', 'aborted', 'repeated_turn', 'prior_usage'])
def test_ambiguous_native_boundary_never_proves_first_turn(managed, native, damage):
    _, _, context, _ = managed
    path, _, identity = native
    started = time.time()
    if damage == 'prior_usage':
        receipt(path, started - 1)
    append(path, event('task_started', started, turn_id='one'))
    if damage != 'no_usage':
        receipt(path, started + .01)
    append(path, event('turn_aborted' if damage == 'aborted' else 'task_complete', started + .02,
                       turn_id='other' if damage == 'wrong_turn' else 'one'))
    if damage == 'repeated_turn':
        append(path, event('task_started', started + .03, turn_id='one'))
    if damage in {'partial_json', 'non_object'}:
        with path.open('a') as stream:
            stream.write('{"partial"' if damage == 'partial_json' else '[]\n')
    if damage == 'wrong_project':
        path.write_text(path.read_text().replace(str(context.project_root), '/another-project'))
    if damage == 'wrong_identity':
        path.write_text(path.read_text().replace(identity, 'unrelated-identity'))
    after = snapshot(context, 'codex', identity)
    assert not completed_turn({}, after, started_at=started - .1, session_id=identity)


def test_subscription_windows_are_sparse_time_ordered_and_numeric_only(managed, native):
    _, _, context, _ = managed
    path, _, identity = native
    now = time.time()
    receipt(path, now)
    append(path, event('token_count', now, rate_limits={'primary': {'window_minutes': 300,
             'used_percent': 12.5, 'resets_at': int(now) + 600, 'extra': 'must-stay-local'}}),
           event('token_count', now-2, rate_limits={'primary': {'window_minutes':300, 'used_percent':99}}),
           event('token_count', now+1, rate_limits={'secondary': {'window_minutes':10080, 'used_percent':7}}))
    result = snapshot(context, 'codex', identity)['subscription_windows']
    assert result['primary']['used_percent'] == 12.5 and result['secondary']['minutes'] == 10080
    assert 'must-stay-local' not in str(result)
