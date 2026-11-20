"""Completed reads and durable semantic progress do not request a full reset."""
import json

from tests.test_artifact_read_snapshot import IDENTITY, published
from tests.test_reader_work_record import reader, publish
from zf.cli.artifact_required_read import prepare_required_read
from zf.runtime.artifact_input_recovery import build_input_recovery
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_resume_apply import apply_workflow_resume
from zf.runtime.work_record import current_work_record


def test_completed_consumption_recovery_selects_context_gaps_not_full_read(tmp_path):
    manifest, required, _ = published(tmp_path)
    _, receipts = prepare_required_read(tmp_path, manifest=manifest, rows=required, identity=IDENTITY)
    result = build_input_recovery(state_dir=tmp_path, manifest=manifest, identity=IDENTITY,
        events=[], required=required, reads=receipts)
    assert result['historical_missing'] == 0
    assert result['next_action'] == 'assess_context_gaps_then_continue'
    assert 'required_read' not in result['commands']
    assert result['current_memory'] == 'unknown' and not result['authorizes_execution']
    assert result['required_scope_count'] == len(required)


def restarted_reader(tmp_path):
    values = reader(tmp_path)
    state, log, runtime, op, manifest, scratch, token, body = values
    runtime.event_writer.emit('run.started', payload={'workflow_run_id':op['workflow_run_id']})
    saved = publish(values)
    WorkflowOperationService(state_dir=state,event_log=log,event_writer=runtime.event_writer).interrupt(
        operation_id=op['operation_id'],request_hash=op['request_hash'],
        workflow_run_id=op['workflow_run_id'],task_id=op['task_id'],reason='graceful_stop')
    projection=build_workflow_resume_projection(state,runtime.config)
    checkpoint=next(c for c in projection['operation_checkpoints'] if c['operation_id']==op['operation_id'])
    assert apply_workflow_resume(state,runtime.config,checkpoint_id=checkpoint['checkpoint_id'])['applied']==1
    runtime.event_writer.emit('worker.launch_artifact.written',payload={'instance_id':'verify-1','launch_attempt':2})
    runtime.run_once(events=[])
    assert reduce_workflow_operations(log.read_all())[op['operation_id']]['status']=='running'
    return values,saved


def test_native_reader_checkpoint_survives_authorized_transport_redrive(tmp_path,monkeypatch,capsys):
    from zf.cli.main import main
    values,saved=restarted_reader(tmp_path)
    state,log,runtime,op,manifest,scratch,token,body=values
    restored=current_work_record(state,log.read_all(),manifest,'verify-1')
    assert restored['status']=='bound'
    assert restored['work_record_ref']==saved['work_record_ref']
    monkeypatch.setenv('ZF_ROLE_INSTANCE','verify-1')
    monkeypatch.setenv('ZF_RESULT_SUBMIT_TOKEN',token)
    assert main(['artifact','work-record','show','--operation',op['operation_id'],'--state-dir',str(state)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['sha256']==saved['work_record_ref']['sha256']
    assert not restored['authorizes_execution']
    assert not any(e.type=='workflow.call.result.admitted' for e in log.read_all())


def test_authenticated_recovery_after_complete_read_has_no_reset_command(tmp_path,monkeypatch,capsys):
    from tests.test_artifact_input_recovery import current_dispatch
    from tests.test_artifact_read_capability import _grant
    from zf.cli.main import main
    from zf.runtime.artifact_read_ledger import active_read_ledger_path
    state=tmp_path/'state'
    manifest,required,_=published(state)
    current_dispatch(state,required)
    _grant(state,monkeypatch,manifest=manifest,operation_id='op')
    cursor=None
    while True:
        args=['artifact','read','--attempt','a','--required','--state-dir',str(state)]
        if cursor: args += ['--cursor',cursor]
        assert main(args)==0
        page=json.loads(capsys.readouterr().out);cursor=page['next_cursor']
        if cursor is None:break
    ledger=active_read_ledger_path(state,'a').read_bytes()
    for _ in range(2):
        assert main(['artifact','recover','--attempt','a','--state-dir',str(state)])==0
        result=json.loads(capsys.readouterr().out)
        assert result['historical_missing']==0
        assert result['next_action']=='assess_context_gaps_then_continue'
        assert 'required_read' not in result['commands']
        assert result['current_memory']=='unknown'
    assert active_read_ledger_path(state,'a').read_bytes()==ledger


import pytest

@pytest.mark.parametrize('fault',['admission_missing','owner','decision','old_transport','old_attempt','corrupt_record','expired'])
def test_checkpoint_redrive_does_not_weaken_binding(tmp_path,fault):
    from dataclasses import replace
    from zf.runtime.task_attempt_runtime import task_attempt_store
    values,saved=restarted_reader(tmp_path)
    state,log,runtime,op,manifest,_,_,_=values
    events=log.read_all()
    if fault=='admission_missing':
        events=[e for e in events if e.type!='workflow.operation.redrive_admitted']
    elif fault in {'owner','decision'}:
        i=next(i for i,e in enumerate(events) if e.type=='workflow.operation.redrive_admitted')
        k='recovery_decision_owner' if fault=='owner' else 'recovery_decision_event_id'
        events[i]=replace(events[i],payload={**events[i].payload,k:'foreign'})
    elif fault in {'old_transport','old_attempt'}:
        kind='fanout.child.dispatched' if fault=='old_transport' else 'task.attempt.started'
        i=next(i for i,e in enumerate(events) if e.type==kind)
        events[i]=replace(events[i],payload={**events[i].payload,'lease_id':'foreign'})
    elif fault=='corrupt_record':(state/saved['work_record_ref']['ref']).write_text('{}')
    else:
        store=task_attempt_store(runtime);row=store.current_rows()[0]
        store.renew_lease(row['attempt_id'],lease_expires_at='2000-01-01T00:00:00+00:00',updated_at=row['updated_at'])
    assert current_work_record(state,events,manifest,'verify-1')['status']=='unavailable'


def test_new_progress_after_redrive_keeps_old_record_immutable(tmp_path):
    values,saved=restarted_reader(tmp_path)
    state,log,_,_,manifest,_,_,body=values
    path=state/saved['work_record_ref']['ref'];before=path.read_bytes()
    body['next_step']='提交新评估，保留未决问题'
    newer=publish(values,request_id='checkpoint-2')
    assert current_work_record(state,log.read_all(),manifest,'verify-1')['work_record_ref']==newer['work_record_ref']
    assert path.read_bytes()==before
    with pytest.raises(ValueError,match='idempotency conflict'):
        publish(values)
