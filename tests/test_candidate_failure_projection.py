"""Unavailable admitted readers must not become product/Plan failures."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.fanout import FanoutContext, FanoutManifestProjector
from zf.runtime.failure_kind import aggregate_failure_kind
from zf.runtime.durable_call_fanout import DurableCallFanoutMixin


@pytest.mark.parametrize('verdict,expected', [('blocked', 'infra'), ('abstained', 'infra'), ('rejected', '')])
def test_live_child_projection_keeps_typed_failure_for_aggregate(tmp_path, verdict, expected):
    writer = EventWriter(EventLog(tmp_path / 'events.jsonl'))
    context = FanoutContext.create(stage_id='verify', topology='fanout_reader', trace_id='run',
        trigger_event_id='candidate', target_ref='sha', role_instances=['reader'])
    writer.append(context.started_event())
    writer.emit('fanout.child.failed', payload={'fanout_id': context.fanout_id,
        'child_id': 'reader', 'semantic_verdict': verdict, 'control_result_schema': 'verification-result.v1'})
    manifest = FanoutManifestProjector(tmp_path).rebuild(context.fanout_id, writer.event_log.read_all())
    assert aggregate_failure_kind(manifest['children']) == expected


@pytest.mark.parametrize('schema,failure_class,expected', [
    ('verification-result.v1', '', 'infra'),
    ('workflow-read-result.v1', 'reader_execution_failure', 'infra'),
    ('workflow-read-result.v1', 'reader_abstained', ''),
])
def test_settled_replay_keeps_same_failure_routing(tmp_path, monkeypatch, schema, failure_class, expected):
    import zf.runtime.call_result_envelope as envelopes
    import zf.runtime.call_result_runtime as results
    monkeypatch.setattr(envelopes, 'hydrate_call_result_envelope', lambda *_: {})
    monkeypatch.setattr(results, 'hydrate_admitted_control_result', lambda *_: {
        'schema_version': schema, 'verdict': 'abstained', 'failure_class': failure_class})
    writer = EventWriter(EventLog(tmp_path / 'events.jsonl'))
    context = FanoutContext.create(stage_id='verify', topology='fanout_reader', trace_id='run',
        trigger_event_id='candidate', target_ref='sha', role_instances=['reader'])
    writer.append(context.started_event())
    probe = SimpleNamespace(state_dir=tmp_path, event_writer=writer, _evaluate_reader_fanout=lambda _: None)
    prepared = SimpleNamespace(admitted_call_result_ref='artifact', admitted_call_result_digest='sha',
        operation_id='op', request_hash='hash', mode='blocking')
    DurableCallFanoutMixin._replay_settled_fanout_call(probe, context=context,
        child=context.expected_children[0], prepared=prepared, topology='fanout_reader', causation_id='retry')
    manifest = FanoutManifestProjector(tmp_path).rebuild(context.fanout_id, writer.event_log.read_all())
    assert aggregate_failure_kind(manifest['children']) == expected


def test_runtime_aggregate_publishes_infra_and_never_requests_plan(tmp_path):
    from tests.test_reader_fanout_runtime import _state
    from zf.runtime.candidate_rework import plan_candidate_rework
    state, log, transport, runtime = _state(tmp_path)
    writer = EventWriter(log)
    context = FanoutContext.create(stage_id='review-candidate', topology='fanout_reader', trace_id='run',
        trigger_event_id='candidate', target_ref='sha', role_instances=['review-a', 'review-b'])
    started = context.started_event()
    started.payload['aggregate'] = asdict(runtime.config.workflow.stages[0].aggregate)
    started.payload['pdd_id'] = 'P1'
    writer.append(started)
    for child in context.expected_children:
        writer.emit('fanout.child.failed', payload={'fanout_id': context.fanout_id,
            'stage_id': context.stage_id, 'trace_id': 'run', 'child_id': child.child_id,
            'control_result_schema': 'verification-result.v1', 'semantic_verdict': 'abstained',
            'report': {'status': 'blocked', 'recommendation': 'abstain', 'summary': 'probe unavailable', 'findings': []}})
    runtime._evaluate_reader_fanout(context.fanout_id)
    events = log.read_all()
    outcome = next(e for e in events if e.type == 'review.rejected')
    assert outcome.payload['failure_kind'] == 'infra'
    plans = plan_candidate_rework(events, max_attempts=2)
    assert plans == []  # Infrastructure is excluded from semantic candidate rework.


def test_retry_quality_failure_does_not_inherit_previous_infrastructure_kind(tmp_path):
    writer = EventWriter(EventLog(tmp_path / 'events.jsonl'))
    context = FanoutContext.create(stage_id='verify', topology='fanout_reader', trace_id='run',
        trigger_event_id='candidate', target_ref='sha', role_instances=['reader'])
    writer.append(context.started_event())
    identity = {'fanout_id': context.fanout_id, 'child_id': 'reader'}
    writer.emit('fanout.child.failed', payload={**identity, 'failure_kind': 'infra',
        'control_result_schema': 'verification-result.v1', 'semantic_verdict': 'abstained'})
    writer.append(context.child_dispatched_event(context.expected_children[0], run_id='retry'))
    writer.emit('fanout.child.failed', payload={**identity, 'control_result_schema': 'verification-result.v1',
        'semantic_verdict': 'rejected'})
    manifest = FanoutManifestProjector(tmp_path).rebuild(context.fanout_id, writer.event_log.read_all())
    assert aggregate_failure_kind(manifest['children']) == ''


def test_live_admitted_reader_execution_failure_reaches_stage_event(tmp_path, monkeypatch):
    from tests.test_reader_fanout_runtime import _state, _start_fanout
    from zf.runtime.call_result_admission import CallResultAdmissionOutcome
    state, log, transport, runtime = _state(tmp_path)
    _start_fanout(runtime)
    fanout = next(e.payload['fanout_id'] for e in log.read_all() if e.type == 'fanout.started')
    monkeypatch.setattr('zf.runtime.call_result_admission.result_protocol_mode', lambda *_: 'blocking')
    monkeypatch.setattr('zf.runtime.call_result_runtime.admit_runtime_call_result', lambda *a, **k:
        CallResultAdmissionOutcome(status='admitted', mode='blocking', operation_id='op', request_hash='hash'))
    monkeypatch.setattr('zf.runtime.call_result_runtime.hydrate_admitted_control_result', lambda *_: {
        'schema_version': 'workflow-read-result.v1', 'verdict': 'abstained',
        'failure_class': 'reader_execution_failure', 'summary': 'Probe unavailable',
        'status': 'failed', 'recommendation': 'abstain', 'findings': []})
    for child in ('review-a', 'review-b'):
        runtime.run_once(events=[ZfEvent(type='review.approved', actor=child, correlation_id='trace-1',
            payload={'fanout_id': fanout, 'child_id': child, 'run_id': f'run-{fanout}-{child}',
                     'report': {'status': 'blocked', 'recommendation': 'abstain',
                                'summary': 'Probe unavailable', 'findings': []}})])
    outcome = next(e for e in log.read_all() if e.type == 'review.rejected')
    assert outcome.payload['failure_kind'] == 'infra'
