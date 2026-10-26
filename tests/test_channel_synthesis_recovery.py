"""An exhausted synthesis can be retried through the existing controlled action."""
from pathlib import Path

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_synthesis_lock import synthesis_request_in_flight
from zf.runtime.control_actions import ControlledActionService


def seed(tmp_path: Path, *, exhausted_generation: int | None = 3, failure_status="timeout"):
    state = tmp_path / 'state'
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    def emit(event_type, **payload):
        return writer.emit(event_type, actor='web', payload={'channel_id': 'ch-recovery', 'thread_id': 'main', **payload})
    emit('channel.created', owner_actor_ref='web')
    emit('channel.member.invited', member_id='synth', member_type='persona_agent', backend='fake',
         channel_role='synthesizer', visibility_profile='planner', role_context_ref='channel_roles/synthesizer.md',
         permissions=['read', 'message', 'summarize'])
    emit('channel.discussion.started', revision=2, context_digest='same-context', roster=[], synthesizer='synth')
    emit('channel.discussion.phase.changed', phase='phase3_synthesis')
    emit('channel.synthesis.requested', request_id='synth-old', target_member_id='synth', discussion_revision=2)
    emit('channel.message.posted', message_id='msg-old', role='user', text='Generate full PRD', refs={'synthesis_request_id':'synth-old'})
    emit('channel.agent.reply.requested', request_id='reply-old', message_id='msg-old', target_member_id='synth', run_generation=3)
    emit('channel.agent.reply.failed', request_id='reply-old', run_generation=3, reason='deadline', failure_status=failure_status)
    if exhausted_generation is not None:
        emit('channel.agent.reply.remediation.exhausted', request_id='reply-old', run_generation=exhausted_generation, reason='budget exhausted')
    return state, writer, emit


@pytest.mark.parametrize('exhausted_generation', [None, 2, 3])
def test_only_current_exhaustion_releases_synthesis_and_exposes_retry(tmp_path, exhausted_generation):
    state, writer, emit = seed(tmp_path, exhausted_generation=exhausted_generation)
    before = writer.event_log.path.read_bytes()
    channel = project_channel(state, 'ch-recovery')
    attention = channel['discussion_attention']['main']
    assert bool(synthesis_request_in_flight(channel, 'main')) is (exhausted_generation != 3)
    assert attention['can_synthesize'] is (exhausted_generation == 3)
    assert attention['state'] == 'blocked'
    if exhausted_generation == 3:
        assert attention['next_action'] == 'synthesize'
        assert not attention['can_restart']
        assert channel['synthesis_requests'][-1]['status'] == 'failed'
    assert writer.event_log.path.read_bytes() == before


def test_retry_action_preserves_discussion_and_deduplicates_new_attempt(tmp_path, monkeypatch):
    state, writer, emit = seed(tmp_path)
    monkeypatch.setattr('zf.runtime.control_actions_channel_msg.dispatch_channel_replies_background', lambda **kwargs: None)
    service = ControlledActionService(state, writer, project_root=tmp_path, actor='web', source='web', surface='web')
    def request():
        requested = emit('runtime.action.requested', action='channel-synthesis-request')
        return service.execute(action='channel-synthesis-request', requested_action='channel.synthesis.request',
                               requested=requested, payload={'channel_id':'ch-recovery', 'thread_id':'main'})
    emit('channel.member.invited', member_id='chair', member_type='persona_agent', backend='fake', permissions=['read', 'message', 'summarize'])
    emit('channel.discussion.mode.set', mode='multi_lens', default_responder_id='chair')
    first = request()
    assert first['status'] == 'requested'
    assert first['target_member_id'] == 'synth'
    assert first['request_id'] != 'synth-old'
    second = request()
    assert second['status'] == 'already_requested'
    assert second['request_id'] == first['request_id']
    channel = project_channel(state, 'ch-recovery')
    assert channel['discussions']['main']['revision'] == 2
    assert channel['discussions']['main']['context_digest'] == 'same-context'
    assert channel['discussion_attention']['main']['failed_reply_count'] == 0
    assert channel['discussion_attention']['main']['execution_state'] in {'queued', 'running'}
    assert next(r for r in channel['reply_requests'] if r['request_id'] == 'reply-old')['status'] == 'failed'
    assert not any(e.type == 'channel.discussion.continued' for e in writer.event_log.read_all())


def test_owner_question_keeps_retry_unavailable(tmp_path):
    state, writer, emit = seed(tmp_path)
    emit('channel.question.opened', question_id='decision', question='Choose scope', kind='owner_decision', status='open')
    channel = project_channel(state, 'ch-recovery')
    assert not channel['discussion_attention']['main']['can_synthesize']


def test_unknown_provider_delivery_rejects_new_synthesis_before_enqueue(tmp_path, monkeypatch):
    state, writer, emit = seed(tmp_path, failure_status='delivery_unknown')
    dispatched = []
    monkeypatch.setattr('zf.runtime.control_actions_channel_msg.dispatch_channel_replies_background',
                        lambda **kwargs: dispatched.append(kwargs))
    service = ControlledActionService(state, writer, project_root=tmp_path,
                                     actor='web', source='web', surface='web')
    requested = emit('runtime.action.requested', action='channel-synthesis-request')
    result = service.execute(action='channel-synthesis-request', requested_action='channel-synthesis-request',
                             requested=requested, payload={'channel_id': 'ch-recovery', 'thread_id': 'main'})
    assert result['status'] == 'provider_delivery_unknown'
    assert result['_status_code'] == 409 and not result['ok']
    assert 'reply-old' in result['reason']
    assert not dispatched
    channel = project_channel(state, 'ch-recovery')
    assert len(channel['synthesis_requests']) == len(channel['reply_requests']) == 1
    assert channel['reply_requests'][0]['failure_status'] == 'delivery_unknown'
    assert not channel['discussion_attention']['main']['can_synthesize']
