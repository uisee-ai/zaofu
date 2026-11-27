"""Controlled review recovery reuses producer outputs and never dispatches Planner."""
import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.config.schema import ZfConfig, ProjectConfig, RoleConfig, WorkflowConfig, WorkflowStageConfig, FanoutAggregateConfig
from zf.runtime.call_result_envelope import write_immutable_json_sidecar, normalize_call_result_envelope
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_control_actions_recovery import _req
from tests.test_reader_fanout_runtime import _RecordingTransport
from tests.test_plan_candidate_preflight import _task_map, _source_index


def _case(root, *, stage_id='prd-plan'):
    state = root / '.zf'
    state.mkdir()
    (state / 'kanban.json').write_text('[]')
    log = EventLog(state / 'events.jsonl')
    writer = EventWriter(log)
    from zf.runtime.result_submit import provision_role_submit_credential
    provision_role_submit_credential(state, 'plan-critic')
    config = ZfConfig(project=ProjectConfig(name='test'),
        roles=[*[RoleConfig(name=name, backend='mock', role_kind='reader') for name in ('planner', 'plan-critic')],
               RoleConfig(name='dev', backend='mock', role_kind='writer')],
        workflow=WorkflowConfig(flow_metadata={'flow_kind': 'issue', 'result_protocol': {'mode': 'blocking'}}, stages=[WorkflowStageConfig(
            id=stage_id, attempt_domain='plan', topology='fanout_reader', trigger='plan.requested',
            roles=['planner'], aggregate=FanoutAggregateConfig(synth_role='plan-critic',
                success_event='task_map.ready', failure_event='prd.plan.failed'))]))
    report = {'status': 'passed', 'summary': 'Current decision confirmed', 'recommendation': 'approve',
              'task_map': _task_map(), 'source_index': _source_index(),
              'source_index_ref': 'inline:source-index', 'plan_md': '# Plan\nDecision confirmed.'}
    manifest = {'fanout_id': 'fanout', 'stage_id': stage_id, 'workflow_run_id': 'run',
                'trace_id': 'run', 'trigger_event_id': 'original-trigger',
                'trigger_payload': {'flow_kind': 'issue', 'issue_ref': 'docs/issue.md'}}
    call = build_plan_synth_call_payload(state_dir=state, project_root=root, manifest=manifest,
        reports=[{'child_id': 'planner', 'report': report}], run_id='attempt', role_instance='plan-critic')
    def sidecar(body, schema):
        return write_immutable_json_sidecar(state, body, root='test-inputs', kind='test',
                                          schema_version=schema, created_by='test')
    sources = sidecar({'schema_version': 'attempt-source-manifest.v1', 'attempt_id': 'attempt',
        'dispatch_id': 'attempt', 'workflow_run_id': 'run', 'sources': call['input_refs']}, 'attempt-source-manifest.v1')
    source = next(s for s in call['input_refs'] if s['source_id'] == 'child-result-planner')
    control = sidecar({'execution_status': 'completed', 'verdict': 'rejected', 'failure_class': ''},
                      'plan-synthesis-result.v1')
    identity = {**call, 'operation_id': 'old-operation', 'attempt_id': 'attempt', 'request_hash': 'a' * 64}
    envelope = sidecar(normalize_call_result_envelope(source_payload=identity, control_result=control,
        workflow_run_id='run', operation_id='old-operation', request_hash='a' * 64,
        source_event_id='old-synth', source_event_type='fanout.synth.completed', actor='plan-critic'),
        'call-result-envelope.v1')
    writer.append(ZfEvent(type='fanout.started', payload={**manifest, 'topology': 'fanout_reader',
        'children': [{'child_id': 'planner', 'role_instance': 'planner'}],
        'aggregate': {'synth_role': 'plan-critic', 'mode': 'wait_for_all',
                      'success_event': 'task_map.ready', 'failure_event': 'prd.plan.failed'}}))
    writer.append(ZfEvent(type='fanout.child.completed', payload={'fanout_id': 'fanout',
        'child_id': 'planner', 'status': 'completed', 'report': report}))
    writer.append(ZfEvent(type='fanout.synth.dispatched', payload={**identity, 'attempt_source_manifest': sources}))
    writer.append(ZfEvent(type='workflow.call.result.admitted', payload={'source_event_id': 'old-synth',
        'workflow_run_id': 'run', 'operation_id': 'old-operation', 'envelope_ref': envelope, 'control_result_ref': control}))
    writer.append(ZfEvent(id='old-synth', type='fanout.synth.completed',
        payload={**identity, 'control_result_ref': control, 'execution_status': 'completed', 'verdict': 'rejected'}))
    writer.append(ZfEvent(type='fanout.aggregate.completed', payload={**manifest, 'status': 'failed'}))
    failure = writer.append(ZfEvent(type='prd.plan.failed', causation_id='old-synth', payload=manifest))
    escalation = writer.append(ZfEvent(type='human.escalate', payload={'workflow_run_id': 'run',
        'source_event_id': failure.id, 'failure_class': 'stage_replan_cap_exhausted',
        'allowed_actions': ['start_new_generation']}))
    service = ControlledActionService(state, writer, config=config, project_root=root, actor='zf-cli')
    payload = {'review_only': True, 'escalation_event_id': escalation.id,
        'approval_ref': 'operator:approved-review', 'reason': 'Check confirmed vs old pending',
        'counter_evidence': [{k: source[k] for k in ('source_id', 'artifact_id', 'sha256')} |
                             {'json_path': '$', 'quote': 'confirmed'}]}
    transport = _RecordingTransport()
    runtime = Orchestrator(state, config, transport)
    return service, runtime, transport, payload, source


def _apply(service, payload):
    return service.execute(action='stage-replan-new-generation', requested_action='stage-replan-new-generation',
                           payload=payload, requested=_req(payload))


def test_controlled_reassessment_dispatches_only_critic_and_survives_restart(tmp_path):
    service, runtime, transport, payload, source = _case(tmp_path)
    from zf.core.task.schema import Task
    from zf.runtime.workflow_anchor import mark_workflow_managed_task
    runtime.task_store.add(mark_workflow_managed_task(Task(id='ANCHOR', status='blocked')))
    service.writer.append(ZfEvent(type='workflow.invoke.accepted', task_id='ANCHOR',
        payload={'workflow_run_id': 'run', 'task_id': 'ANCHOR'}))
    before = (runtime.state_dir / source['ref']).read_bytes()
    result = _apply(service, payload)
    assert result['ok'], result
    assert runtime.task_store.get('ANCHOR').status == 'in_progress'
    runtime._reconcile_reader_fanout_triggers()
    assert [s[0] for s in transport.sent] == ['plan-critic'], [(e.type, e.payload) for e in runtime.event_log.read_all() if e.type.endswith('failed') or e.type == 'fanout.synth.completed']
    events = runtime.event_log.read_all()
    dispatch = [e for e in events if e.type == 'fanout.synth.dispatched'][-1]
    assert dispatch.payload['operation_id'] != 'old-operation'
    manifest = hydrate_sidecar_ref(runtime.state_dir, dispatch.payload['attempt_source_manifest']).payload
    assert {'plan-review-counter-evidence', 'plan-review-prior-verdict'} <= {s['source_id'] for s in manifest['sources']}
    review_reads = {(r['source_id'], r['json_path']) for r in dispatch.payload['required_reads']
                    if r['source_id'].startswith('plan-review-')}
    assert review_reads == {('plan-review-counter-evidence', '$.reason'),
                            ('plan-review-counter-evidence', '$.counter_evidence'),
                            ('plan-review-prior-verdict', '$')}
    assert (runtime.state_dir / source['ref']).read_bytes() == before
    assert not any(e.type in {'plan.requested', 'task_map.ready', 'fanout.child.dispatched'} for e in events)
    assert _apply(service, payload)['replayed']
    restarted = Orchestrator(runtime.state_dir, runtime.config, transport)
    restarted._reconcile_reader_fanout_triggers()
    assert len(transport.sent) == 1
    assert len([e for e in runtime.event_log.read_all() if e.type == 'run.goal.updated']) == 1
    from zf.runtime.plan_critic_reassessment import reassessment_result_current
    current = restarted._fanout_manifest('fanout')
    assert not reassessment_result_current(restarted, current, next(e for e in events if e.id == 'old-synth'))
    from zf.runtime.artifact_read_ledger import read_attempt_artifact, seal_read_ledger
    from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
    from zf.runtime.artifact_read_ledger_validation import validate_required_reads
    requirements = dispatch.payload['required_reads']
    for group in (False, True):
        for row in requirements:
            if row['source_id'].startswith('plan-review-') == group:
                read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=row['source_id'],
                                      artifact_id=row['artifact_id'], json_path=row['json_path'])
        ledger = seal_read_ledger(runtime.state_dir, manifest['attempt_id'])
        gaps = validate_required_reads(runtime.state_dir, policy={
            'attempt_id': manifest['attempt_id'], 'required_reads': requirements}, ledger_descriptor=ledger)
        if group:
            assert not gaps
        else:
            assert {issue['message'].split('/', 1)[0] for issue in gaps} == {
                'plan-review-counter-evidence', 'plan-review-prior-verdict'}
    row = next(s for s in manifest['sources'] if s['source_id'] == 'child-result-planner')
    citation = {k: row[k] for k in ('source_id', 'artifact_id', 'sha256')}
    citation.update(json_path='$', quote='confirmed')
    token = provision_role_submit_credential(runtime.state_dir, 'plan-critic').read_text().strip()
    submit = SemanticResultSubmitService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    submitted = submit.submit(operation_id=dispatch.payload['operation_id'], role_instance='plan-critic',
        credential=token, semantic_result={'status': 'completed', 'recommendation': 'reject',
            'summary': 'A confirmed decision still conflicts with the accepted requirement',
            'report': {'status': 'failed', 'recommendation': 'reject', 'summary': 'Conflict remains',
                'fix_items': [{'observed_gap': 'Decision conflicts', 'required_change': 'Reconcile requirement',
                    'done_when': 'Conflict removed', 'basis': {'obligation': 'Existing accepted requirement',
                        'current_evidence': [citation], 'requirement_evidence': [citation]}}]}})
    result_event = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
    restarted.run_once(events=[result_event])
    current = restarted._fanout_manifest('fanout')
    assert current['aggregate']['status'] == 'failed'
    assert not any(e.type == 'task_map.ready' for e in runtime.event_log.read_all())


@pytest.mark.parametrize('changed', ['bytes', 'producer_result'])
def test_changed_candidate_after_authorization_closes_review_without_dispatch(tmp_path, changed):
    service, runtime, transport, payload, source = _case(tmp_path)
    assert _apply(service, payload)['ok']
    if changed == 'bytes':
        (runtime.state_dir / source['ref']).write_text('{}')
    else:
        service.writer.append(ZfEvent(type='fanout.child.completed', payload={'fanout_id': 'fanout',
            'child_id': 'planner', 'status': 'completed', 'report': {
                'status': 'passed', 'summary': 'Different producer result', 'recommendation': 'approve',
                'task_map': _task_map(), 'source_index': _source_index(),
                'source_index_ref': 'inline:source-index', 'plan_md': '# Plan\nChanged scope.'}}))
    runtime._reconcile_reader_fanout_triggers()
    assert not transport.sent
    assert runtime._fanout_manifest('fanout')['aggregate']['status'] == 'failed'
    before = len(runtime.event_log.read_all())
    runtime._reconcile_reader_fanout_triggers()
    assert len(runtime.event_log.read_all()) == before


@pytest.mark.parametrize('fault', ['evidence', 'approval', 'changed', 'superseded', 'scope', 'active', 'used', 'newer_verdict'])
def test_reassessment_fails_closed(tmp_path, fault):
    service, runtime, _, payload, source = _case(tmp_path)
    if fault == 'evidence': payload['counter_evidence'][0]['quote'] = 'pending'
    if fault == 'approval': payload.pop('approval_ref')
    if fault == 'changed': (runtime.state_dir / source['ref']).write_text('{}')
    if fault == 'superseded': service.writer.append(ZfEvent(type='fanout.started', payload={
        'workflow_run_id': 'run', 'stage_id': 'prd-plan', 'fanout_id': 'new'}))
    if fault == 'scope': payload['input_task_ids'] = ['T1']
    if fault == 'newer_verdict': service.writer.append(ZfEvent(type='fanout.aggregate.completed',
        payload={'fanout_id': 'fanout', 'status': 'completed'}))
    if fault == 'active':
        from zf.runtime.call_result_runtime import prepare_call_operation
        prepare_call_operation(runtime, payload={'workflow_run_id': 'run', 'role_instance': 'planner'},
            operation_type='fanout_reader_child', operation_key='active', stage_id='prd-plan', dispatch_id='active', task_id='')
    if fault == 'used':
        assert _apply(service, payload)['ok']
        payload['reason'] = 'Try another review of the same bytes'
    before = len([e for e in service.writer.event_log.read_all() if e.type == 'fanout.aggregate.started'])
    result = _apply(service, payload)
    assert not result['ok'], result
    assert len([e for e in service.writer.event_log.read_all() if e.type == 'fanout.aggregate.started']) == before
