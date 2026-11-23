"""Semantic submit exhaustion must release an exactly bound Reader child."""
from copy import deepcopy

import pytest

from tests.test_reader_replan_plan_binding import _dispatched_replan
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.call_result_runtime import workflow_operation_service
from zf.runtime.workflow_operation import reduce_workflow_operations


def exhausted(tmp_path, fault=''):
    state, log, orch, child, _, _ = _dispatched_replan(tmp_path)
    op = reduce_workflow_operations(log.read_all())[child['operation_id']]
    identity = {k: op[k] for k in ('workflow_run_id', 'operation_id', 'request_hash')}
    envelope_identity = {**identity, 'attempt_id': op['active_attempt_id'], 'producer_role': op['role_instance']}
    if fault == 'envelope':
        envelope_identity['attempt_id'] = 'foreign'
    ref = write_immutable_json_sidecar(state, {'identity': envelope_identity}, root='test', kind='test',
        schema_version='call-result-envelope.v1', created_by='test')
    invalid = ZfEvent(type='workflow.call.result.invalid', actor='zf-cli',
        origin='worker' if fault == 'worker' else '' if fault == 'legacy' else 'kernel',
        payload={**identity, 'envelope_ref': ref, 'reason': 'output_repair_exhausted', 'issues': []})
    log.append(invalid)
    failed = workflow_operation_service(orch).fail(**identity, task_id=op['task_id'],
        reason='output_repair_exhausted', causation_id=invalid.id, correlation_id=op['workflow_run_id'])
    if fault == 'corrupt-envelope':
        (state / ref['ref']).write_text('{}')
    if fault in {'paused', 'cancelled'}:
        log.append(ZfEvent(type='run.' + fault, origin='kernel', payload={'workflow_run_id': op['workflow_run_id']}))
    if fault == 'dispatch-paused':
        log.append(ZfEvent(type='dispatch.paused', origin='kernel'))
    if fault == 'new-operation-event':
        log.append(ZfEvent(type='workflow.operation.interrupted', origin='kernel', payload=identity))
    if fault == 'new-dispatch':
        dispatched = next(e for e in log.read_all() if e.type == 'fanout.child.dispatched')
        log.append(ZfEvent(type='fanout.child.dispatched', origin='kernel', payload=deepcopy(dispatched.payload)))
    return state, log, orch, child, failed


@pytest.mark.parametrize('origin', ['', 'legacy'])
def test_recovery_tick_converges_protocol_failure_and_exposes_controlled_retry(tmp_path, origin):
    from zf.runtime.reader_rebuild_admission import reader_rebuild_error

    state, log, orch, child, failed = exhausted(tmp_path, origin)
    assert not [e for e in log.read_all() if e.type == 'fanout.child.failed']
    orch._recover_unrecorded_reader_fanout_results()
    terminal = [e for e in log.read_all() if e.type == 'fanout.child.failed']
    assert len(terminal) == 1
    assert terminal[0].causation_id == failed.id
    assert terminal[0].payload['failure_class'] == 'call_result_protocol'
    assert terminal[0].payload['call_result_status'] == 'invalid'
    op = reduce_workflow_operations(log.read_all())[child['operation_id']]
    assert op['status'] == 'failed' and not op.get('semantic_verdict')
    assert orch._fanout_manifest(child['fanout_id'])['children'][0]['status'] == 'failed'
    payload = {'workflow_run_id': op['workflow_run_id'], 'fanout_id': child['fanout_id'],
        'fanout_child_id': child['child_id'], 'target_operation_id': op['operation_id'],
        'active_attempt_id': op['active_attempt_id'], 'source_event_id': terminal[0].id}
    assert not reader_rebuild_error(state, log.read_all(), payload)
    orch._recover_unrecorded_reader_fanout_results()
    assert len([e for e in log.read_all() if e.type == 'fanout.child.failed']) == 1
    assert not [e for e in log.read_all() if e.type == 'workflow.call.result.admitted']


@pytest.mark.parametrize('fault', ['worker', 'envelope', 'corrupt-envelope', 'paused', 'cancelled', 'dispatch-paused',
    'new-operation-event', 'new-dispatch', 'superseded'])
def test_protocol_failure_recovery_rejects_unproven_or_newer_binding(tmp_path, monkeypatch, fault):
    from zf.runtime.reader_protocol_terminal import reconcile_protocol_failed_readers

    _, log, orch, _, _ = exhausted(tmp_path, fault)
    if fault == 'superseded':
        monkeypatch.setattr(orch, '_fanout_identity_stale_reason', lambda _: ('superseded', 'new'))
    before = [e.id for e in log.read_all()]
    assert not reconcile_protocol_failed_readers(orch, log.read_all())
    assert [e.id for e in log.read_all()] == before


@pytest.mark.parametrize('stale_projection', [False, True])
def test_recovery_after_child_failure_append_does_not_duplicate_or_lose_aggregation(tmp_path, monkeypatch, stale_projection):
    from zf.runtime.fanout import FanoutManifestProjector

    _, log, orch, child, _ = exhausted(tmp_path)
    project = FanoutManifestProjector.project_event
    if stale_projection:
        monkeypatch.setattr(FanoutManifestProjector, 'project_event', lambda *args: None)
    release = orch._release_fanout_worker_if_terminal
    def crash(**kwargs):
        raise RuntimeError('after durable child failure')
    monkeypatch.setattr(orch, '_release_fanout_worker_if_terminal', crash)
    with pytest.raises(RuntimeError, match='durable child'):
        orch._recover_unrecorded_reader_fanout_results()
    monkeypatch.setattr(orch, '_release_fanout_worker_if_terminal', release)
    monkeypatch.setattr(FanoutManifestProjector, 'project_event', project)
    if stale_projection:
        assert orch._fanout_manifest(child['fanout_id'])['children'][0]['status'] == 'dispatched'
    orch._recover_unrecorded_reader_fanout_results()
    assert len([e for e in log.read_all() if e.type == 'fanout.child.failed']) == 1
    assert orch._fanout_manifest(child['fanout_id'])['aggregate']['status'] == 'failed'
