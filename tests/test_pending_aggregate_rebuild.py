"""A consumed rebuild request survives reversible Run blocking and restart."""
import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from tests.test_reader_fanout_runtime import _state, _start_fanout


def blocked_rebuild(tmp_path, terminal_type='run.goal.blocked', request_origin='kernel'):
    state, log, transport, orch = _state(tmp_path)
    orch.session_store.create(project_root=str(orch.project_root))
    stage = orch.config.workflow.stages[0]
    stage.roles = ['review-a']
    stage.aggregate.child_success_event = 'flow.discovery.child.completed'
    stage.aggregate.child_failure_event = 'flow.discovery.child.failed'
    stage.aggregate.success_event = 'flow.discovery.completed'
    stage.aggregate.failure_event = 'flow.discovery.failed'
    _start_fanout(orch)
    dispatched = next(e for e in log.read_all() if e.type=='fanout.child.dispatched')
    orch.run_once(events=[ZfEvent(type='flow.discovery.child.completed',actor='review-a',correlation_id='trace-1',payload={
        'fanout_id':dispatched.payload['fanout_id'],'stage_id':dispatched.payload['stage_id'],
        'child_id':dispatched.payload['child_id'],'run_id':dispatched.payload['run_id'],
        'role_instance':'review-a','status':'completed','report':{'status':'passed','summary':'no gaps',
        'recommendation':'approve','findings':[],'open_p0_p1_gap_count':0,'evidence_refs':['git:abc'],'test_refs':['pytest']}})])
    completed = next(e for e in log.read_all() if e.type=='flow.discovery.completed')
    blocked=orch.event_writer.append(ZfEvent(type=terminal_type,origin='kernel',correlation_id='trace-1',payload={'run_id':'trace-1'}))
    rebuild=orch.event_writer.append(ZfEvent(type='fanout.aggregate.rebuild.requested',origin=request_origin,actor='operator',correlation_id='trace-1',payload={
        'fanout_id':dispatched.payload['fanout_id'],'source_event_id':completed.id,'reason':'reviewed recovery'}))
    orch.run_once(events=[blocked,rebuild])
    orch.run_once()  # Persist the consumed ledger position while still blocked.
    assert len([e for e in log.read_all() if e.type=='flow.discovery.completed'])==1
    assert orch.session_store.load().latest_event_offset > 0
    return state, log, transport, orch, dispatched, completed, rebuild


@pytest.mark.parametrize('fanout_cancelled', [False, True])
@pytest.mark.parametrize('terminal_type', ['run.goal.blocked', 'run.cancelled'])
@pytest.mark.parametrize('request_origin', ['kernel', 'external'])
def test_rebuild_resumes_after_run_unblocked_and_controller_restart(tmp_path, terminal_type, request_origin, fanout_cancelled):
    state, log, transport, orch, dispatched, completed, rebuild = blocked_rebuild(tmp_path, terminal_type, request_origin)
    if fanout_cancelled:
        orch.event_writer.append(ZfEvent(type='fanout.cancelled',origin='kernel',correlation_id='trace-1',
            payload={'fanout_id':dispatched.payload['fanout_id'],'reason':'operator cancelled'}))
    restored=Orchestrator(state,orch.config,transport,project_root=orch.project_root)
    active=orch.event_writer.append(ZfEvent(type='run.goal.updated',origin='kernel',correlation_id='trace-1',payload={'run_id':'trace-1','status':'active'}))
    restored.run_once(events=[active]);restored.run_once()
    results=[e for e in log.read_all() if e.type=='flow.discovery.completed']
    expected = 2 if terminal_type=='run.goal.blocked' and request_origin=='kernel' and not fanout_cancelled else 1
    assert len(results)==expected
    if expected==2:
        assert results[-1].payload['rebuild_request_event_id']==rebuild.id
    restored.run_once()
    assert len([e for e in log.read_all() if e.type=='flow.discovery.completed'])==expected


@pytest.mark.parametrize('crash_point', ['sidecar', 'aggregate', 'publication'])
def test_rebuild_publication_survives_each_persistence_boundary(tmp_path, monkeypatch, crash_point):
    state, log, transport, orch, dispatched, completed, rebuild = blocked_rebuild(tmp_path)
    resumed = Orchestrator(state,orch.config,transport,project_root=orch.project_root)
    active = orch.event_writer.append(ZfEvent(type='run.goal.updated',origin='kernel',correlation_id='trace-1',
        payload={'run_id':'trace-1','status':'active'}))
    sends = len(transport.sent)
    with monkeypatch.context() as patch:
        if crash_point == 'sidecar':
            from zf.runtime import reader_aggregate_publication as publication
            original = publication.freeze_publication
            def crash(*args):
                original(*args)
                raise OSError('crash after immutable body')
            patch.setattr(publication,'freeze_publication',crash)
        else:
            original = resumed.event_writer.append
            def crash(event):
                result=original(event)
                if (event.payload.get('rebuild_of')==completed.id and event.type==
                        ('fanout.aggregate.completed' if crash_point=='aggregate' else 'flow.discovery.completed')):
                    raise OSError('crash after durable append')
                return result
            patch.setattr(resumed.event_writer,'append',crash)
        resumed.run_once(events=[active])
    recovered = Orchestrator(state,orch.config,transport,project_root=orch.project_root)
    recovered.run_once(); recovered.run_once()
    aggregates=[e for e in log.read_all() if e.type=='fanout.aggregate.completed' and e.payload.get('rebuild_of')==completed.id]
    published=[e for e in log.read_all() if e.type=='flow.discovery.completed' and e.payload.get('rebuild_of')==completed.id]
    assert len(aggregates)==len(published)==1
    assert published[0].causation_id==aggregates[0].id
    assert aggregates[0].payload['rebuild_publication_ref']
    assert len(transport.sent)==sends
    recovered._rebuild_reader_fanout_aggregate(rebuild)
    assert len([e for e in log.read_all() if e.type=='flow.discovery.completed' and e.payload.get('rebuild_of')==completed.id])==1


@pytest.mark.parametrize('native', [False, True])
def test_pending_rebuild_rejects_moved_candidate_or_moved_branch(tmp_path, native):
    import subprocess
    from types import SimpleNamespace
    from zf.runtime.fanout_aggregate_recovery import candidate_binding_current
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=tmp_path,text=True).strip()
    git('init','-q');git('config','user.name','Test');git('config','user.email','test@example.com')
    git('commit','--allow-empty','-qm','first')
    head=git('rev-parse','HEAD')
    body={'workflow_run_id':'run-1','candidate_head_commit':head,'candidate_ref':'HEAD',
          'task_map_generation':'gen-1','plan_artifact_package_digest':'p1'}
    if native:
        body['schema_version']='candidate-freeze-receipt.v1'
    candidate=ZfEvent(type='candidate.ready',actor='zf-cli',origin='kernel',correlation_id='run-1',payload=body)
    source=ZfEvent(type='flow.discovery.completed',payload=dict(body))
    runtime=SimpleNamespace(project_root=tmp_path)
    assert candidate_binding_current(runtime,[candidate,source],source,'run-1')
    if native:
        same_head=ZfEvent(type='candidate.ready',actor='zf-cli',origin='kernel',payload=dict(body))
        assert not candidate_binding_current(runtime,[candidate,source,same_head],source,'run-1')
        changed=ZfEvent(type='candidate.ready',actor='zf-cli',origin='kernel',payload={**body,'task_map_generation':'gen-2'})
        assert not candidate_binding_current(runtime,[candidate,source,changed],source,'run-1')
    git('commit','--allow-empty','-qm','second')
    assert not candidate_binding_current(runtime,[candidate,source],source,'run-1')
    changed=ZfEvent(type='candidate.ready',actor='zf-cli',origin='kernel',payload={**body,'candidate_head_commit':git('rev-parse','HEAD')})
    assert not candidate_binding_current(runtime,[candidate,source,changed],source,'run-1')


def test_schema_block_is_bound_once_and_never_a_publication_success(tmp_path):
    from types import SimpleNamespace
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter
    from zf.core.verification.event_schema import EventSchemaRegistry
    from zf.runtime.reader_aggregate_publication import freeze_publication,publish_prepared
    log=EventLog(tmp_path/'events.jsonl')
    writer=EventWriter(log,default_origin='kernel',schema_mode='blocking',
        schema_registry=EventSchemaRegistry.from_dict({'flow.discovery.completed':{'required':['required_missing']}}))
    runtime=SimpleNamespace(state_dir=tmp_path,event_log=log,event_writer=writer)
    shared={'fanout_id':'f1','rebuild_of':'source-1','rebuild_request_event_id':'request-1',
            'recovery_generation':1,'workflow_run_id':'run-1'}
    aggregate=ZfEvent(type='fanout.aggregate.completed',origin='kernel',correlation_id='run-1',
        payload={**shared,'success_event':'flow.discovery.completed'})
    publication=ZfEvent(type='flow.discovery.completed',origin='kernel',correlation_id='run-1',
        causation_id=aggregate.id,payload=shared)
    aggregate.payload['rebuild_publication_ref']=freeze_publication(runtime,publication)
    writer.append(aggregate)
    forged=ZfEvent(type='discriminator.failed',origin='worker',payload={'blocked_event_id':publication.id,
        'blocked_event_type':publication.type,'blocked_event_payload':publication.payload})
    writer.append(forged)
    result=publish_prepared(runtime,aggregate)
    assert result.type=='discriminator.failed' and result.origin=='kernel' and result.id!=forged.id
    assert publish_prepared(runtime,aggregate).id==result.id
    assert len([e for e in log.read_all() if e.type=='discriminator.failed' and e.origin=='kernel'])==1
    assert not [e for e in log.read_all() if e.type=='flow.discovery.completed']


@pytest.mark.parametrize('variant', ['valid', 'later_same_binding_pass', 'new_freeze', 'other_run', 'worker', 'missing', 'future'])
def test_rebuild_uses_original_evidence_freeze_before_fanout_start(tmp_path, monkeypatch, variant):
    from types import SimpleNamespace
    from zf.runtime import candidate_continuation
    from zf.runtime.fanout_aggregate_recovery import candidate_binding_current
    monkeypatch.setattr(candidate_continuation, '_git_commit', lambda *args: 'abc')
    body={'workflow_run_id':'run-1','candidate_head_commit':'abc','candidate_ref':'candidate',
          'schema_version':'candidate-freeze-receipt.v1','task_map_generation':'gen-1'}
    freeze=ZfEvent(type='candidate.ready', origin='kernel', payload=body)
    proof=ZfEvent(type='test.passed', origin='worker' if variant=='worker' else 'kernel',
        payload={**body, 'workflow_run_id':'run-2' if variant=='other_run' else 'run-1'})
    trigger={**body,'source_event_id':'absent' if variant=='missing' else proof.id}
    started=ZfEvent(type='fanout.started', origin='kernel', payload={'fanout_id':'f1','trigger_payload':trigger})
    source=ZfEvent(type='flow.discovery.completed', origin='kernel', payload={**body,'fanout_id':'f1'})
    events=[freeze] if variant=='future' else [freeze,proof]
    if variant=='new_freeze':
        events.append(ZfEvent(type='candidate.ready', origin='kernel', payload=dict(body)))
    events.append(started)
    if variant=='future':
        events.append(proof)
    if variant=='later_same_binding_pass':
        events.append(ZfEvent(type='test.passed', origin='kernel', payload=dict(body)))
    events.append(source)
    runtime=SimpleNamespace(project_root=tmp_path, _fanout_manifest=lambda _: {'trigger_payload':trigger})
    assert candidate_binding_current(runtime,events,source,'run-1') == (variant in {'valid','later_same_binding_pass'})
