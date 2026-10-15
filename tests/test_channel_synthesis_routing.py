"""Rejected formal generation releases retries without erasing source evidence."""
from copy import deepcopy

from zf.core.config.schema import ChannelConfig, ZfConfig
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_semantic_sources import build_semantic_source_bundle
from zf.runtime.channel_synthesis_lock import synthesis_request_in_flight
from zf.runtime.channel_synthesis_routing import synthesis_route_error
from tests.test_channel_prd_file import setup, request


def test_budget_rejection_is_failed_action_and_retry_keeps_original_evidence(tmp_path, monkeypatch):
    state, writer, service, file, body = setup(tmp_path, monkeypatch)
    service.config = ZfConfig(channel=ChannelConfig(semantic_source_max_chars=120_000))
    failed = request(service, writer, file, body)
    assert not failed['ok'] and failed['_status_code'] == 409, failed
    assert 'semantic_source_budget_exceeded' in failed['reason']
    rejected = project_channel(state, 'ch-file')
    assert synthesis_request_in_flight(rejected, 'main') is None
    assert rejected['synthesis_requests'][-1]['status'] == 'blocked'
    assert rejected['discussion_attention']['main']['reason'] == 'context_pack_rejected'
    assert not rejected['syntheses']
    from types import SimpleNamespace
    from zf.runtime.channel_synthesis_reactor import react_channel_synthesis_requested
    routed = []
    monkeypatch.setattr('zf.runtime.channel_synthesis_reactor._route_runtime_message',
                        lambda *args: routed.append(args))
    host = SimpleNamespace(state_dir=state, event_log=writer.event_log, event_writer=writer)
    original = next(e for e in writer.event_log.read_all() if e.type == 'channel.synthesis.requested')
    react_channel_synthesis_requested(host, original)
    assert not routed, 'watcher startup must not retry rejected generation without a new request'
    count = len([e for e in writer.event_log.read_all() if e.type == 'channel.context_pack.rejected'])

    service.config = ZfConfig(channel=ChannelConfig(semantic_source_max_chars=1_000_000))
    accepted = request(service, writer, file, body)
    assert accepted['ok'], accepted
    current = project_channel(state, 'ch-file')
    assert synthesis_request_in_flight(current, 'main')['request_id'] == accepted['request_id']
    assert current['discussion_attention']['main']['context_rejections'] == []
    react_channel_synthesis_requested(host, original)
    assert not routed, 'a superseded rejected request cannot race the explicit retry'
    assert len([e for e in writer.event_log.read_all() if e.type == 'channel.context_pack.rejected']) == count
    message_id = 'msg-' + accepted['request_id']
    bundle = build_semantic_source_bundle(current, thread_id='main', trigger_message_id=message_id,
                                           state_dir=state, max_source_chars=1_000_000)
    assert bundle['complete'] and bundle['required']
    selected = next(d for d in bundle['documents'] if d['kind'] == 'channel_prd_source')
    assert selected['content'] == body
    assert bundle['source_chars'] >= len(body)
    assert not request(service, writer, file, body)['ok']  # running duplicate remains rejected


def test_legacy_rejection_replays_without_migration_and_stale_rejections_are_scoped(tmp_path, monkeypatch):
    state, writer, service, file, body = setup(tmp_path, monkeypatch)
    def emit(kind, **payload):
        return writer.emit(kind, actor='test', payload={'channel_id':'ch-file','thread_id':'main',**payload})
    emit('channel.synthesis.requested', request_id='legacy', target_member_id='synth')
    emit('channel.message.posted', message_id='legacy-message', refs={'synthesis_request_id':'legacy'})
    emit('channel.context_pack.rejected', context_pack_id='bad-other-target', trigger_message_id='legacy-message',
         target_member_id='critic', reason='wrong role')
    assert synthesis_request_in_flight(project_channel(state,'ch-file'),'main')
    emit('channel.context_pack.rejected', context_pack_id='bad-synth', trigger_message_id='legacy-message',
         target_member_id='synth', reason='semantic_source_budget_exceeded:200000>120000')
    before = writer.event_log.path.read_bytes()
    projected = project_channel(state,'ch-file')
    assert writer.event_log.path.read_bytes() == before
    assert synthesis_request_in_flight(projected,'main') is None
    assert projected['synthesis_requests'][-1]['reason'].startswith('semantic_source_budget_exceeded')
    result = request(service, writer, file, body)
    assert result['ok'], result
    # A late old-context rejection cannot release the new request.
    emit('channel.context_pack.rejected', context_pack_id='bad-synth', trigger_message_id='legacy-message',
         target_member_id='synth', reason='late old rejection')
    assert synthesis_request_in_flight(project_channel(state,'ch-file'),'main')['request_id'] == result['request_id']
    # Independent current errors are not hidden by retry.
    emit('channel.context_pack.rejected', context_pack_id='independent', trigger_message_id='other-message',
         target_member_id='critic', reason='unrelated evidence missing')
    errors = project_channel(state,'ch-file')['discussion_attention']['main']['context_rejections']
    assert any(e['reason'] == 'unrelated evidence missing' for e in errors)


def test_selected_snapshot_is_verified_before_inlining_and_does_not_mutate_channel(tmp_path, monkeypatch):
    state, writer, service, file, body = setup(tmp_path, monkeypatch)
    result=request(service, writer, file, body)
    current=project_channel(state,'ch-file');original=deepcopy(current)
    args=dict(thread_id='main', trigger_message_id='msg-'+result['request_id'], state_dir=state, max_source_chars=1_000_000)
    bundle=build_semantic_source_bundle(current, **args)
    assert current==original
    source=next(d for d in bundle['documents'] if d['kind']=='channel_prd_source')
    (state/source['artifact_ref']).write_text('tampered')
    failed=build_semantic_source_bundle(current, **args)
    assert not failed['complete'] and not failed['documents']
    assert 'prd_source_file:' in failed['reason']


def test_route_outcome_requires_actual_work():
    assert synthesis_route_error({'skipped':[{'reason':'member_removed'}]}, {}, 'm')=='member_removed'
    assert synthesis_route_error({'reply_requests':['reply']}, {}, 'm')==''
    assert synthesis_route_error({'queued':['reply']}, {}, 'm')==''
    assert synthesis_route_error({}, {}, 'm')=='synthesis_not_dispatched'
