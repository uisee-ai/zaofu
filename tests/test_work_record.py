import json
from pathlib import Path

import pytest
from zf.core.events import EventLog,EventWriter,ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task,TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.result_submit import provision_role_submit_credential,bind_operation_submit_capability
from zf.runtime.work_record import publish_work_record,current_work_record
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.artifact_input_recovery import current_task_input_recovery
from zf.runtime.recovery import build_recovery_briefing
from tests.test_artifact_read_snapshot import published


def setup(state:Path):
    manifest,required,_=published(state)
    workdir=state.parent/'work';workdir.mkdir()
    task=Task(id='T7',title='record',status='in_progress',assigned_to='dev-1',active_dispatch_id='a',execution_binding=TaskExecutionBinding(workflow_run_id='r'))
    TaskStore(state/'kanban.json').add(task)
    attempts=TaskAttemptStore(state/'task_attempts.json')
    attempts.ensure_for_dispatch(run_id='r',task_id='T7',dispatch_id='a',role='dev',instance_id='dev-1',operation_id='op',
        briefing_ref='',created_at='2026-11-02T00:00:00+00:00',lease_expires_at='2099-01-01T00:00:00+00:00',max_attempts=3)
    request=write_immutable_json_sidecar(state,{'request':{'role_instance':'dev-1','task_id':'T7','workdir':str(workdir),'task_pipeline_stage':'impl'}},
        root='requests',kind='request',schema_version='request.v1',created_by='kernel')
    log=EventLog(state/'events.jsonl');writer=EventWriter(log)
    payload={'workflow_run_id':'r','operation_id':'op','active_attempt_id':'a','dispatch_id':'a','role_instance':'dev-1',
        'lease_id':'operation-lease','request_ref':request,'required_reads':required}
    for type in ['workflow.operation.requested','workflow.operation.started','task.dispatched']:
        writer.append(ZfEvent(type=type,task_id='T7',payload=payload))
    token_path=provision_role_submit_credential(state,'dev-1')
    token=token_path.read_text().strip()
    bind_operation_submit_capability(state,operation_id='op',role_instance='dev-1',attempt_id='a',lease_id='operation-lease')
    (workdir/'tests.log').write_text('3 tests passed\n')
    record=workdir/'progress.json'
    record.write_text(json.dumps({'completed_actions':['实现校验'],'pending':['补边界测试'],'next_step':'检查重复请求','evidence_files':['tests.log']},ensure_ascii=False))
    return manifest,task,log,record,token


def publish(state,log,record,token,**extra):
    return publish_work_record(state_dir=state,event_log=log,operation_id='op',role='dev-1',credential=token,record_file=record,request_id='one',**extra)


def test_publish_replay_and_actual_recovery_without_task_completion(tmp_path,monkeypatch,capsys):
    from zf.cli.main import main
    state=tmp_path/'state'
    manifest,task,log,record,token=setup(state)
    monkeypatch.setenv('ZF_ROLE_INSTANCE','dev-1');monkeypatch.setenv('ZF_RESULT_SUBMIT_TOKEN',token)
    assert main(['artifact','work-record','publish','--operation','op','--file',str(record),'--request-id','one','--state-dir',str(state)])==0
    first=json.loads(capsys.readouterr().out)
    replay=publish(state,log,record,token)
    assert replay['replayed'] and first['event_id']==replay['event_id']
    events=log.read_all()
    assert len([e for e in events if e.type=='worker.work_record.published'])==1
    result=hydrate_sidecar_ref(state,first['work_record_ref']).payload
    assert result['claims_status']=='worker_report_unverified' and result['authorizes_execution'] is False
    assert TaskStore(state/'kanban.json').get('T7').status=='in_progress'
    pack=current_task_input_recovery(state,events,task)
    assert pack['work_record']['status']=='bound'
    assert 'work-record show --operation op' in pack['commands']['work_record_read']
    assert '检查重复请求' in build_recovery_briefing(state,'dev-1',task)
    assert main(['artifact','work-record','show','--operation','op','--state-dir',str(state)])==0
    assert json.loads(capsys.readouterr().out)['sha256']==first['work_record_ref']['sha256']
    # Rotate the role session credential; the same canonical record stays useful.
    new_token=provision_role_submit_credential(state,'dev-1').read_text().strip()
    assert publish(state,log,record,new_token)['replayed']
    body=json.loads(record.read_text());body['next_step']='different';record.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='idempotency'):
        publish(state,log,record,new_token)


@pytest.mark.parametrize('fault',['wrong_actor','stale_dispatch','expired','cancelled','outside','symlink','secret','corrupt'])
def test_work_record_rejects_stale_or_unsafe_publication(tmp_path,fault):
    state=tmp_path/'state';manifest,task,log,record,token=setup(state)
    if fault=='wrong_actor':token='wrong'
    if fault=='stale_dispatch':TaskStore(state/'kanban.json').update('T7',active_dispatch_id='new')
    if fault=='expired':
        store=TaskAttemptStore(state/'task_attempts.json');row=store.current(run_id='r',task_id='T7')
        store.renew_lease(row['attempt_id'],lease_expires_at='2000-01-01T00:00:00+00:00',updated_at='now')
    if fault=='cancelled':EventWriter(log).append(ZfEvent(type='workflow.operation.cancelled',payload={'operation_id':'op','workflow_run_id':'r'}))
    if fault=='outside':
        (tmp_path/'outside.json').write_bytes(record.read_bytes());record=tmp_path/'outside.json'
    if fault=='symlink':
        (record.parent/'tests.log').unlink();(tmp_path/'secret').write_text('outside');(record.parent/'tests.log').symlink_to(tmp_path/'secret')
    if fault=='secret':(record.parent/'tests.log').write_text('API_KEY=secret-credential-value')
    if fault=='corrupt':
        first=publish(state,log,record,token);(state/first['work_record_ref']['ref']).write_text('{}')
        assert current_work_record(state,log.read_all(),manifest,'dev-1')['status']=='unavailable'
        return
    with pytest.raises((ValueError,OSError)):
        publish(state,log,record,token)
    assert not any(e.type=='worker.work_record.published' for e in log.read_all())


def test_record_body_without_publication_is_never_recovered(tmp_path,monkeypatch):
    import zf.runtime.work_record as module
    state=tmp_path/'state';manifest,task,log,record,token=setup(state)
    original=EventWriter.append
    def interrupted(self,event):
        if event.type=='worker.work_record.published':raise OSError('interrupted')
        return original(self,event)
    monkeypatch.setattr(EventWriter,'append',interrupted)
    with pytest.raises(OSError,match='interrupted'):publish(state,log,record,token)
    assert current_work_record(state,log.read_all(),manifest,'dev-1')['status']=='unavailable'
    monkeypatch.setattr(EventWriter,'append',original)
    assert publish(state,log,record,token)['event_id']


def test_evidence_changes_and_reassigned_run_cannot_replay_old_record(tmp_path):
    state=tmp_path/'state';manifest,task,log,record,token=setup(state)
    publish(state,log,record,token)
    (record.parent/'tests.log').write_text('4 tests passed')
    with pytest.raises(ValueError,match='idempotency'):publish(state,log,record,token)
    TaskStore(state/'kanban.json').update('T7',execution_binding=TaskExecutionBinding(workflow_run_id='new'))
    assert current_work_record(state,log.read_all(),manifest,'dev-1')['status']=='unavailable'
    with pytest.raises(ValueError,match='Run'):publish(state,log,record,token)


def test_concurrent_retries_publish_one_event_and_race_leaves_only_orphan(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import zf.runtime.work_record as module
    state=tmp_path/'state';manifest,task,log,record,token=setup(state)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:publish(state,log,record,token),range(4)))
    assert len({result['event_id'] for result in results})==1
    assert sum(not result['replayed'] for result in results)==1
    original=module.write_immutable_json_sidecar
    def changed(*args,**kwargs):
        ref=original(*args,**kwargs)
        if kwargs.get('schema_version')==module.SCHEMA:
            TaskStore(state/'kanban.json').update('T7',active_dispatch_id='later')
        return ref
    monkeypatch.setattr(module,'write_immutable_json_sidecar',changed)
    with pytest.raises(ValueError):
        publish_work_record(state_dir=state,event_log=log,operation_id='op',role='dev-1',
                            credential=token,record_file=record,request_id='two')
    assert len([e for e in log.read_all() if e.type=='worker.work_record.published'])==1


def test_work_record_pages_pin_digest_and_survive_session_rotation(tmp_path,monkeypatch,capsys):
    from zf.cli.main import main
    state=tmp_path/'state';manifest,task,log,record,token=setup(state)
    body=json.loads(record.read_text());body['completed_actions']=['已读与验证'*150]*4
    record.write_text(json.dumps(body,ensure_ascii=False))
    first=publish(state,log,record,token)
    monkeypatch.setenv('ZF_ROLE_INSTANCE','dev-1');monkeypatch.setenv('ZF_RESULT_SUBMIT_TOKEN',token)
    command=['artifact','work-record','show','--operation','op','--state-dir',str(state)]
    offset=0; parts=[]
    while True:
        assert main([*command,'--page-start',str(offset),'--digest',first['work_record_ref']['sha256']])==0
        output=capsys.readouterr().out
        assert len(output.encode())<=8192
        page=json.loads(output);parts.append(page['content']);offset=page['next_page_start']
        if offset is None:break
    assert json.loads(''.join(parts))['completed_actions']==body['completed_actions']
    assert main([*command,'--page-start','1024'])==1
    capsys.readouterr()
    EventWriter(log).append(ZfEvent(type='workflow.operation.cancelled',payload={'operation_id':'op','workflow_run_id':'r'}))
    assert current_work_record(state,log.read_all(),manifest,'dev-1')['status']=='unavailable'


def test_immutable_evidence_and_actual_handoff_projection(tmp_path,monkeypatch,capsys):
    from zf.cli.main import main
    from zf.runtime.handoff_summary import project_handoff_summary
    state=tmp_path/'state';manifest,task,log,record,token=setup(state)
    publish(state,log,record,token)
    (record.parent/'tests.log').write_text('changed')
    monkeypatch.setenv('ZF_ROLE_INSTANCE','dev-1');monkeypatch.setenv('ZF_RESULT_SUBMIT_TOKEN',token)
    assert main(['artifact','work-record','show','--operation','op','--evidence','0','--state-dir',str(state)])==0
    assert json.loads(capsys.readouterr().out)['content']=='3 tests passed\n'
    summary=project_handoff_summary(state,'T7',task_events=list(enumerate(log.read_all(),1)))
    assert summary['work_record']['status']=='bound'
