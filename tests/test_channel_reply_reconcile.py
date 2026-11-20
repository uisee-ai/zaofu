"""Unknown delivery recovery uses native metadata and the existing action path."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_reconcile import native_abort_receipt
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_run_owner import provider_run_fields_for_request
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

SID = '01a1c55c-8800-7c01-8db8-c0184a539f6c'
TURN = '01a1c55c-8843-7d52-957b-36e6faa1a5bd'
NOW = datetime.now(timezone.utc)


def _fixture(tmp_path, monkeypatch):
    state = tmp_path / '.state'
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    writer.emit('channel.created', actor='web', payload={'channel_id': 'ch-test', 'name': 'recovery', 'owner_actor_ref': 'web'})
    writer.emit('channel.member.added', actor='web', payload={'channel_id': 'ch-test', 'member_id': 'critic',
        'member_type': 'persona_agent', 'backend': 'codex-headless', 'permissions': ['read', 'message']})
    writer.emit('channel.message.posted', actor='web', payload={'channel_id': 'ch-test', 'message_id': 'msg-one',
        'thread_id': 'main', 'member_id': 'web', 'role': 'user', 'text': 'Review this requirement.'})
    body = {'channel_id': 'ch-test', 'thread_id': 'main', 'request_id': 'reply-one', 'message_id': 'msg-one',
            'target_member_id': 'critic', 'backend': 'codex-headless', 'status': 'pending', 'run_generation': 1}
    body.update(provider_run_fields_for_request('ch-test', body))
    writer.append(ZfEvent(type='channel.agent.reply.requested', payload=body, ts=NOW.isoformat()))
    started = writer.append(ZfEvent(type='channel.agent.reply.started', payload=body, ts=NOW.isoformat()))
    failure = writer.append(ZfEvent(type='channel.agent.reply.failed', causation_id=started.id,
        payload={**body, 'provider_session_id': SID, 'failure_status': 'delivery_unknown', 'retryable': False},
        ts=(NOW + timedelta(seconds=10)).isoformat()))
    home = tmp_path / 'provider-home'
    monkeypatch.setenv('CODEX_HOME', str(home))
    native = home / 'sessions/2026/11/10' / f'rollout-test-{SID}.jsonl'
    native.parent.mkdir(parents=True)
    rows = [
        {'type': 'session_meta', 'payload': {'id': SID, 'cwd': str(tmp_path)}},
        {'type': 'event_msg', 'timestamp': (NOW + timedelta(seconds=1)).isoformat(), 'payload': {'type': 'task_started', 'turn_id': TURN}},
        {'type': 'event_msg', 'timestamp': (NOW + timedelta(seconds=9)).isoformat(), 'payload': {'type': 'turn_aborted', 'turn_id': TURN}},
    ]
    native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    service = ControlledActionService(state, writer, project_root=tmp_path, actor='web', source='operator', surface='web')
    dispatch = Mock()
    monkeypatch.setattr('zf.runtime.control_actions_channel_msg.dispatch_channel_replies_background', dispatch)
    payload = {'channel_id': 'ch-test', 'reconcile_request_id': 'reply-one', 'failure_event_id': failure.id, 'run_generation': 1}
    return service, payload, native, rows, dispatch


def _execute(service, payload):
    requested = service.writer.emit('web.action.requested', actor=service.actor, payload={'action': 'channel-drain-replies'})
    return service.execute(action='channel-drain-replies', requested_action='channel-drain-replies', requested=requested, payload=payload)


def test_reconciled_reply_retains_failure_and_dispatches_new_generation_once(tmp_path, monkeypatch):
    service, payload, _, _, dispatch = _fixture(tmp_path, monkeypatch)
    result = _execute(service, payload)
    assert result['ok'], result
    assert result['run_generation'] == 2
    receipt = hydrate_sidecar_ref(service.state_dir, result['native_abort_receipt_ref']).payload
    assert receipt['request_id'] == 'reply-one'
    assert receipt['failure_event_id'] == payload['failure_event_id']
    assert receipt['native_turn_id'] == TURN
    assert receipt['run_generation'] == 1
    assert _execute(service, payload)['replayed']
    events = service.writer.event_log.read_all()
    assert len([e for e in events if e.type == 'channel.agent.reply.requested']) == 2
    assert len([e for e in events if e.type == 'channel.agent.reply.failed']) == 1
    assert not any(e.type in {'channel.synthesis.proposed', 'channel.prd.confirmed'} for e in events)
    assert dispatch.called
    actual = dispatch_reply_request(state_dir=service.state_dir, writer=service.writer, channel_id='ch-test',
        request_id='reply-one', actor='web', source='test', project_root=tmp_path, deterministic_reply='Recovered reply')
    assert actual.completed == ['reply-one']
    channel = project_channel(service.state_dir, 'ch-test')
    assert channel['reply_requests'][0]['status'] == 'completed'
    assert channel['reply_requests'][0]['run_generation'] == 2
    service.writer.emit('channel.agent.reply.completed', actor='old-worker', payload={
        'channel_id': 'ch-test', 'request_id': 'reply-one', 'run_generation': 1})
    assert project_channel(service.state_dir, 'ch-test')['reply_requests'][0]['run_generation'] == 2


@pytest.mark.parametrize('fault', ['running', 'completed', 'completed_then_aborted', 'wrong_turn', 'wrong_session', 'wrong_project',
                                    'later_turn', 'ambiguous_turn', 'malformed', 'missing', 'stale_generation', 'stale_failure', 'non_owner'])
def test_unknown_or_mismatched_evidence_never_retries(tmp_path, monkeypatch, fault):
    service, payload, native, rows, dispatch = _fixture(tmp_path, monkeypatch)
    if fault == 'running': rows.pop()
    elif fault == 'completed': rows[-1]['payload']['type'] = 'task_complete'
    elif fault == 'completed_then_aborted':
        rows.insert(-1, {'type': 'event_msg', 'timestamp': (NOW + timedelta(seconds=8)).isoformat(),
                         'payload': {'type': 'task_complete', 'turn_id': TURN}})
    elif fault == 'wrong_turn': rows[-1]['payload']['turn_id'] = 'other'
    elif fault == 'wrong_session': rows[0]['payload']['id'] = 'other'
    elif fault == 'wrong_project': rows[0]['payload']['cwd'] = '/different'
    elif fault in {'later_turn', 'ambiguous_turn'}:
        rows.append({'type': 'event_msg', 'timestamp': (NOW + timedelta(seconds=20 if fault == 'later_turn' else 5)).isoformat(),
                     'payload': {'type': 'task_started', 'turn_id': 'other'}})
    elif fault == 'stale_generation': payload['run_generation'] = 2
    elif fault == 'stale_failure': payload['failure_event_id'] = 'other'
    elif fault == 'non_owner': service.actor = 'stranger'
    native.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    if fault == 'malformed': native.write_text('{partial')
    if fault == 'missing': native.unlink()
    result = _execute(service, payload)
    assert not result['ok'], result
    dispatch.assert_not_called()
    assert project_channel(service.state_dir, 'ch-test')['reply_requests'][0]['run_generation'] == 1


def test_concurrent_recovery_does_not_duplicate_generation(tmp_path, monkeypatch):
    service, payload, *_ = _fixture(tmp_path, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _execute(service, payload), range(2)))
    assert any(result['ok'] for result in results)
    assert len([e for e in service.writer.event_log.read_all() if e.type == 'channel.agent.reply.requested']) == 2
