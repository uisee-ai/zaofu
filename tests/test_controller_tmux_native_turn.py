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
    partial = snapshot(context, 'codex', identity)
    assert not completed_turn(before, partial, started_at=execution.body['started_at'], session_id=identity)
    calls = []

    def complete(_):
        assert not peer.settlements
        calls.append(True)
        append(path, event('task_complete', started + .02, turn_id='turn-a', last_agent_message='private body'))

    monkeypatch.setattr('zf.integrations.controller.tmux.time.sleep', complete)
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
