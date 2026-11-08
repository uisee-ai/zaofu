"""A repaired admission incident must not keep a diagnostic reader alive."""
import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.reader_failure_supersession import reader_manifest_replacement_admission
from zf.runtime.workflow_operation import load_workflow_operation
from tests.test_reader_fanout_runtime import _durable_reader_state
from tests.test_fanout_hardening import _FlakyTransport, _state_with_transport


def recovery_events(source):
    def event(kind, id, **payload):
        return ZfEvent(type=kind, id=id, origin='kernel', correlation_id='run-durable',
                       payload={'workflow_run_id':'run-durable', **payload})
    admitted = event('task_map.admitted', 'map-admitted', schema_version='task-map.admission.v1',
                     trigger_event_id=source.id, plan_admission_incident_id='incident',
                     task_map_ref='map.json', task_map_digest='a'*64)
    generation = event('task.pipeline.generation.admitted', 'wrc-admitted',
                       schema_version='task-pipeline-generation.v1', trigger_event_id=source.id,
                       task_map_admitted_event_id=admitted.id, task_map_generation='generation',
                       plan_artifact_package_ref='package.json', plan_artifact_package_digest='b'*64)
    cancelled = event('fanout.cancelled', 'old-aborted', failure_scope='plan_admission',
                      trigger_event_id=source.id)
    old_package = event('plan.artifact_package.admitted', 'old-package', status='admitted',
                       package_slot='execution_plan', task_map_generation='generation',
                       plan_revision='generation', package_ref='package.json', package_digest='b'*64,
                       source_event_id=source.id)
    redrive = event('task_map.ready', 'redrive', redrive_of=source.id,
                    task_map_ref='map.json', task_map_digest='a'*64)
    package = event('plan.artifact_package.admitted', 'valid-package', status='admitted',
                    package_slot='execution_plan', task_map_generation='generation',
                    plan_revision='generation', package_ref='package.json', package_digest='b'*64,
                    source_event_id=redrive.id)
    return [cancelled, admitted, generation, old_package, redrive, package]


@pytest.mark.parametrize('mode', ['recovered', 'aborted_only', 'no_wrc', 'wrong_incident',
                                  'wrong_run', 'wrong_source', 'wrong_digest', 'wrong_package',
                                  'untrusted', 'untrusted_package', 'untrusted_wrc',
                                  'missing_binding', 'no_incident'])
def test_tick_retires_only_proven_plan_admission_recovery(tmp_path, monkeypatch, mode):
    state, log, transport, runtime, config, child = _durable_reader_state(
        tmp_path, plan_admission_source=mode != 'no_incident')
    events = log.read_all()
    if mode == 'no_incident':
        # An ordinary candidate review is never retired by unrelated plan admission.
        source = ZfEvent(type='task_map.ready', id='unrelated', origin='kernel',
                         correlation_id='run-durable')
    else:
        source = next(e for e in events if e.type == 'task_map.ready')
    rows = recovery_events(source)
    if mode == 'aborted_only': rows.pop()
    if mode == 'no_wrc': rows.pop(2)
    if mode == 'wrong_incident': rows[1].payload['plan_admission_incident_id'] = 'other'
    if mode == 'wrong_run': rows[2].payload['workflow_run_id'] = 'other'
    if mode == 'wrong_source': rows[1].payload['trigger_event_id'] = 'other'
    if mode == 'wrong_digest': rows[1].payload['task_map_digest'] = 'c'*64
    if mode == 'wrong_package': rows[-1].payload['package_digest'] = 'c'*64
    if mode == 'untrusted': rows[1].origin = 'external'
    if mode == 'untrusted_package': rows[-1].origin = 'external'
    if mode == 'untrusted_wrc': rows[2].origin = 'external'
    if mode == 'missing_binding': rows[2].payload.pop('plan_artifact_package_digest')
    writer = EventWriter(log, default_origin='kernel')
    for row in rows: writer.append(row)
    stops=[]
    monkeypatch.setattr(transport, 'terminate', lambda role: stops.append(role), raising=False)
    monkeypatch.setattr(transport, 'is_alive', lambda role: role not in stops)
    runtime.run_once(events=[])
    cancelled=[e for e in log.read_all() if e.type=='fanout.cancelled'
               and e.payload.get('fanout_id')==child['fanout_id']]
    if mode != 'recovered':
        assert not cancelled and not stops
        return
    assert len(cancelled)==1
    assert cancelled[0].payload['reason']=='superseded_by_recovered_plan_admission'
    assert stops==['verify-1']
    op=child.get('operation_id') or child['payload']['operation_id']
    assert load_workflow_operation(log,op)['status']=='cancelled'
    Orchestrator(state,config,transport).run_once(events=[])
    assert stops==['verify-1'] and len(transport.sent)==1
    assert not any(e.type in {'task.done','review.approved','run.goal.blocked'} for e in log.read_all())


def test_recovery_proof_works_before_reader_starts():
    source=ZfEvent(type='task_map.ready',id='source',origin='kernel',correlation_id='run-durable',
                   payload={'task_map_ref':'map.json','task_map_digest':'a'*64})
    trigger=ZfEvent(type='flow.discovery.requested',id='trigger',origin='kernel',
                    correlation_id='run-durable',payload={'workflow_run_id':'run-durable',
                    'rework_of':source.id,'plan_admission_incident_id':'incident'})
    started=ZfEvent(type='fanout.started',id='started',origin='kernel',
                    payload={'fanout_id':'reader','trigger_event_id':trigger.id})
    events=[source,trigger,*recovery_events(source),started]
    assert reader_manifest_replacement_admission(events,{
        'fanout_id':'reader','trigger_payload':trigger.payload,'children':[]}).id=='map-admitted'


def test_queued_recovered_incident_never_dispatches_provider(tmp_path):
    _,log,transport,runtime=_state_with_transport(tmp_path,_FlakyTransport(alive=True))
    writer=EventWriter(log,default_origin='kernel')
    source=writer.append(ZfEvent(type='task_map.ready',correlation_id='run-durable',
        payload={'workflow_run_id':'run-durable','task_map_ref':'map.json','task_map_digest':'a'*64}))
    trigger=writer.append(ZfEvent(type='candidate.ready',correlation_id='diagnosis',payload={
        'workflow_run_id':'run-durable','pdd_id':'F-11111111','rework_of':source.id,
        'plan_admission_incident_id':'incident'}))
    for row in recovery_events(source):writer.append(row)
    runtime.run_once(events=[trigger])
    runtime.run_once(events=[])
    assert not transport.sent
    cancelled=[e for e in log.read_all() if e.type=='fanout.cancelled' and e.payload.get('fanout_id')]
    assert len(cancelled)==1
    assert cancelled[0].payload['reason']=='superseded_by_recovered_plan_admission'
    assert not any(e.type=='workflow.operation.requested' for e in log.read_all())
