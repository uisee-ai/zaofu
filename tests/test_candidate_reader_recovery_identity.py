"""Reader repair reuses frozen identity, without treating replay as a new freeze."""
from copy import deepcopy

import pytest

from tests.test_candidate_retained_tasks import mixed, prepare, currentness
from tests.test_reader_stage_recovery import execute
from tests.test_candidate_host_consumption import evidence, prepare as prepare_host
from zf.core.config.schema import FanoutAggregateConfig, WorkflowStageConfig
from zf.core.events.model import ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.stage_failure_replan import reader_stage_lineage_payload


def recover(runtime):
    writer = runtime.event_writer
    writer.default_origin = 'kernel'
    native = next(e for e in runtime.event_log.read_all() if e.type == 'candidate.ready')
    run = native.payload['workflow_run_id']
    runtime.config.workflow.stages = [WorkflowStageConfig(id='candidate-verify', trigger='candidate.ready',
        topology='fanout_reader', attempt_domain='verify', roles=['verify-lane-0'],
        aggregate=FanoutAggregateConfig(success_event='test.passed', failure_event='test.failed'))]
    writer.emit('run.admission.admitted', payload={'workflow_run_id': run})
    writer.emit('fanout.started', correlation_id=run, payload={
        'workflow_run_id': run, 'stage_id': 'candidate-verify', 'fanout_id': 'verify-f1',
        'trigger_event_id': native.id})
    failure = writer.emit('test.failed', correlation_id=run, payload={
        'workflow_run_id': run, 'stage_id': 'candidate-verify', 'fanout_id': 'verify-f1',
        'failure_kind': 'infra', 'reason': 'host evidence unavailable'})
    service = ControlledActionService(runtime.state_dir, writer, config=runtime.config,
        project_root=runtime.project_root, actor='run-manager')
    result = execute(service, failure.id, action='stage-replan-new-generation')
    assert result['ok'], result
    retry = [e for e in runtime.event_log.read_all() if e.type == 'candidate.ready'][-1]
    return native, retry, failure


def legacy_view(runtime, native, retry):
    events = deepcopy(runtime.event_log.read_all())
    old = next(e for e in events if e.id == retry.id)
    old.payload['schema_version'] = native.payload['schema_version']
    old.payload.pop('candidate_snapshot_event_id', None)
    old.payload.pop('superseded_task_ids', None)
    old.payload.pop('task_supersession_event_ids', None)
    runtime.event_log.read_all = lambda: events
    return events, old


def test_actual_controlled_reader_retry_keeps_contract_and_target(mixed):
    before = prepare(mixed)
    native, retry, _ = recover(mixed)
    assert retry.payload.get('schema_version') != 'candidate-freeze-receipt.v1'
    assert retry.payload['candidate_snapshot_event_id'] == native.id
    assert prepare(mixed) == before
    assert currentness(mixed, before) == []


def test_exact_fanout_lineage_keeps_explicit_empty_freeze_members(mixed):
    native, _, _ = recover(mixed)
    lineage = reader_stage_lineage_payload(mixed.event_log.read_all(), stage_id='candidate-verify',
        fanout_id='verify-f1', correlation_id='run-1')
    assert lineage == native.payload


def test_legacy_reader_repair_resolves_complete_native_identity(mixed):
    before = prepare(mixed)
    native, retry, _ = recover(mixed)
    legacy_view(mixed, native, retry)
    assert prepare(mixed) == before
    assert currentness(mixed, before) == []


@pytest.mark.parametrize('valid', [True, False])
def test_batch_resume_projects_native_identity_or_blocks_incomplete_lineage(mixed, valid):
    from zf.runtime.workflow_resume import build_workflow_batch_resume_checkpoints
    native, retry, _ = recover(mixed)
    events, old = legacy_view(mixed, native, retry)
    if not valid:
        events[:] = [e for e in events if e.payload.get('result_event_id') != retry.id]
    events.extend([
        ZfEvent(type='fanout.started', origin='kernel', correlation_id='run-1',
            payload={'workflow_run_id':'run-1', 'fanout_id':'retry-fanout',
                     'stage_id':'candidate-verify', 'trigger_event_id':old.id}),
        ZfEvent(type='fanout.aggregate.completed', origin='kernel', correlation_id='run-1',
            payload={'workflow_run_id':'run-1', 'fanout_id':'retry-fanout',
                     'stage_id':'candidate-verify', 'status':'failed', 'failure_kind':'infra'}),
    ])
    checkpoint = next(c for c in build_workflow_batch_resume_checkpoints(mixed.state_dir, events=events)
                      if c.fanout_id == 'retry-fanout')
    if valid:
        assert checkpoint.candidate_snapshot_event_id == native.id
        assert checkpoint.safe_resume_action == 'reemit_candidate_ready'
    else:
        assert checkpoint.safe_resume_action == 'blocked_external_gate'
        assert not checkpoint.mutating_resume_supported
        assert 'incomplete' in checkpoint.reason


def test_invalid_explicit_parent_cannot_fall_back_to_matching_freeze(mixed):
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
    native, _, _ = recover(mixed)
    assert frozen_candidate_for_payload(mixed.event_log.read_all(), 'run-1',
        payload={'candidate_snapshot_event_id':'missing', 'freeze_id':native.payload['freeze_id']}) is None


@pytest.mark.parametrize('legacy', [False, True])
def test_aggregate_rebuild_validates_later_receipt_for_exact_historical_repair(mixed, monkeypatch, legacy):
    from zf.runtime import candidate_continuation
    from zf.runtime.fanout_aggregate_recovery import candidate_binding_current
    native, retry, _ = recover(mixed)
    if legacy:
        events, retry = legacy_view(mixed, native, retry)
    else:
        events = mixed.event_log.read_all()
    body = {**native.payload, 'fanout_id':'discovery-repair'}
    trigger = {**body, 'source_event_id':retry.id}
    events.append(ZfEvent(type='fanout.started', origin='kernel', payload={
        'fanout_id':'discovery-repair', 'trigger_payload':trigger}))
    source = ZfEvent(type='flow.discovery.completed', origin='kernel', payload=body)
    events.append(source)
    mixed._fanout_manifest = lambda _: {'trigger_payload':trigger}
    monkeypatch.setattr(candidate_continuation, '_git_commit', lambda *args: body['candidate_head_commit'])
    assert candidate_binding_current(mixed,events,source,native.payload['workflow_run_id'])
    replacement = deepcopy(native)
    replacement.id = 'new-native-after-repair'
    events.append(replacement)
    assert not candidate_binding_current(mixed,events,source,native.payload['workflow_run_id'])


def test_real_new_freeze_still_invalidates_previous_reader(mixed):
    before = prepare(mixed)
    native, retry, _ = recover(mixed)
    events, _ = legacy_view(mixed, native, retry)
    replacement = deepcopy(native)
    replacement.id = 'new-native-freeze'
    events.append(replacement)
    assert currentness(mixed, before)


@pytest.mark.parametrize('fault', ['foreign_origin', 'other_run', 'changed_target', 'missing_completion',
    'wrong_approval', 'wrong_fanout', 'changed_receipt', 'changed_empty_member',
    'null_empty_member', 'missing_other_field', 'wrong_action', 'wrong_generation_source', 'new_native'])
def test_legacy_reader_repair_cannot_borrow_unproven_identity(mixed, fault):
    native, retry, failure = recover(mixed)
    events, old = legacy_view(mixed, native, retry)
    if fault == 'foreign_origin': old.origin = 'worker'
    if fault == 'other_run':
        next(e for e in events if e.id == failure.id).payload['workflow_run_id'] = 'other-run'
    if fault == 'changed_target': old.payload['candidate_head_commit'] = '0' * 40
    if fault == 'missing_completion':
        events[:] = [e for e in events if e.payload.get('result_event_id') != retry.id]
    if fault == 'wrong_approval': old.payload['operator_authorization_ref'] = 'forged'
    if fault == 'wrong_fanout':
        next(e for e in events if e.id == failure.id).payload['fanout_id'] = 'unrelated'
    if fault == 'changed_receipt': old.payload['freeze_receipt_digest'] = 'f' * 64
    if fault == 'changed_empty_member': old.payload['superseded_task_ids'] = ['TASK-A']
    if fault == 'null_empty_member': old.payload['superseded_task_ids'] = None
    if fault == 'missing_other_field': old.payload.pop('freeze_id')
    if fault == 'wrong_action':
        next(e for e in events if e.id == old.causation_id).payload['action'] = 'other-action'
    if fault == 'wrong_generation_source': old.payload['stage_replan_generation_source_event_id'] = 'other'
    if fault == 'new_native':
        replacement = deepcopy(native)
        replacement.id = 'new-freeze'
        events.insert(next(i for i, e in enumerate(events) if e.id == old.id), replacement)
    with pytest.raises(ValueError):
        prepare(mixed)


@pytest.mark.parametrize('legacy', [False, True])
def test_actual_resumed_consumer_keeps_original_host_receipt(evidence, legacy):
    from zf.core.config.schema import ZfConfig
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    runtime, seeded, source_op, source_payload, receipt, completed = evidence
    config = ZfConfig()
    config.workflow.flow_metadata = runtime.config.workflow.flow_metadata
    config.runtime.host_verification = runtime.config.runtime.host_verification
    runtime.config = config
    native, retry, _ = recover(runtime)
    if legacy:
        legacy_view(runtime, native, retry)
    _, consumer = prepare_host(runtime, seeded, 'recovered-reader')
    assert consumer['contract_snapshot_digest'] == source_payload['contract_snapshot_digest']
    assert consumer['target_snapshot_digest'] == source_payload['target_snapshot_digest']
    bound = hydrate_sidecar_ref(runtime.state_dir, consumer['candidate_host_evidence_ref']).payload
    assert bound['receipts'][0]['receipt_ref'] == receipt
    assert bound['receipts'][0]['receipt_id'] == completed.id
    assert bound['receipts'][0]['source_operation_id'] == source_op.operation_id
    assert sum(e.type == 'runtime.action.attempt.started' and e.payload.get('kind') == 'verify'
               for e in runtime.event_log.read_all()) == 1


def test_reader_preregistration_failure_reaches_aggregate_as_infrastructure(tmp_path, monkeypatch):
    from tests.test_reader_fanout_runtime import _durable_reader_state
    from zf.runtime.orchestrator import Orchestrator

    def unavailable(*args, **kwargs):
        raise ValueError('fixture frozen input binding unavailable')

    monkeypatch.setattr(Orchestrator, '_prepare_reader_fanout_child_operation', unavailable)
    _, log, _, orch, _, child = _durable_reader_state(tmp_path)
    failure = next(e for e in log.read_all() if e.type == 'fanout.child.failed')
    assert failure.origin == 'kernel' and failure.payload['failure_kind'] == 'infra'
    assert child['failure_class'] == 'reader_execution_failure'
    orch.run_once(events=[failure])
    orch.run_once()
    aggregate = next(e for e in log.read_all() if e.type == 'review.rejected')
    assert aggregate.payload['failure_kind'] == 'infra'
    assert not any(e.type in {'candidate.rework.requested', 'task_map.ready'} for e in log.read_all())


@pytest.mark.parametrize('successor', [
    'obsolete', 'different_freeze_same_sha', 'same_freeze', 'missing_parent', 'untrusted_source',
    'contradictory_sha', 'untrusted_fanout', 'future_trigger', 'duplicate_start', 'unproven_stall',
    'untrusted_original_failure', 'untrusted_original_fanout', 'untrusted_original_trigger',
])
def test_controlled_retry_distinguishes_obsolete_fanouts_from_current_or_unbound_ones(mixed, successor):
    from dataclasses import replace
    from zf.runtime.reader_stage_recovery import repaired_reader_stage_trigger, SUPERSEDED_FANOUT_REASON

    native, _, failure = recover(mixed)
    events = mixed.event_log.read_all()
    rows = events[:events.index(failure) + 1]
    old = replace(native, id='old-freeze', origin='kernel', payload={
        **native.payload, 'freeze_id':'obsolete-freeze',
        'candidate_head_commit':native.payload['candidate_head_commit'] if successor=='different_freeze_same_sha'
                                else '0' * 40})
    rows.insert(rows.index(native), old)
    bound = native if successor == 'same_freeze' else old
    later = ZfEvent(type='candidate.ready', origin='kernel', correlation_id='run-1',
        payload={'workflow_run_id':'run-1', 'freeze_id':bound.payload['freeze_id'],
                 'candidate_head_commit':bound.payload['candidate_head_commit'], 'candidate_snapshot_event_id':bound.id})
    start = ZfEvent(type='fanout.started', origin='kernel', correlation_id='run-1',
        payload={'stage_id':'candidate-verify', 'fanout_id':'later', 'trigger_event_id':later.id})
    if successor=='missing_parent':
        later.payload['candidate_snapshot_event_id']='unknown'
    elif successor=='untrusted_source':
        later.origin='worker'
    elif successor=='contradictory_sha':
        later.payload['candidate_head_commit']='f' * 40
    elif successor=='untrusted_fanout':
        start.origin='worker'
    elif successor=='duplicate_start':
        start.payload['fanout_id']=failure.payload['fanout_id']
    elif successor=='unproven_stall':
        later.actor='zf-stall-redispatch'
    elif successor=='untrusted_original_failure':
        rows[rows.index(failure)]=replace(failure,origin='worker')
    elif successor=='untrusted_original_fanout':
        original_start=next(e for e in rows if e.type=='fanout.started'
                            and e.payload.get('fanout_id')==failure.payload['fanout_id'])
        rows[rows.index(original_start)]=replace(original_start,origin='worker')
    elif successor=='untrusted_original_trigger':
        rows[rows.index(native)]=replace(native,origin='worker')
    rows.extend([start,later] if successor=='future_trigger' else [later,start])
    payload={'repaired_failure_event_id':failure.id,'approval_ref':'reviewed-owner-policy',
             'reason':'bound host evidence available'}
    requested=ZfEvent(type='control.action.requested',origin='kernel',correlation_id='run-1',payload=payload)
    if successor in {'obsolete','different_freeze_same_sha'}:
        event=repaired_reader_stage_trigger(mixed.state_dir,mixed.config,rows,payload,requested)
        assert (event.type,event.payload['candidate_snapshot_event_id'],event.payload['repaired_failure_event_id']) == (
            'candidate.ready',native.id,failure.id)
    else:
        with pytest.raises(ValueError, match=SUPERSEDED_FANOUT_REASON):
            repaired_reader_stage_trigger(mixed.state_dir,mixed.config,rows,payload,requested)
