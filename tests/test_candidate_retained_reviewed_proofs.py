"""A new delta Candidate retains exact prior reviewed Task evidence only."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tests.test_plan_completion_reuse import recovery, action, reviewed
from zf.core.task.schema import Task, TaskContract
from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
from zf.runtime.candidate_currentness import candidate_currentness_issues
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from zf.runtime.run_contract import write_run_contract_snapshot
from zf.runtime.task_contract_snapshot import (build_task_contract_snapshot, write_task_contract_snapshot,
    build_target_snapshot, write_target_snapshot)


@pytest.fixture
def retained(recovery, monkeypatch, request):
    r = recovery
    state = r.runtime.state_dir
    # This fixture mocks Git only; receipt, snapshot and admission validation are real.
    from zf.core.state.session import SessionStore
    from zf.runtime import candidate_incremental, workflow_git_readiness
    SessionStore(state / 'session.yaml').create(str(r.runtime.project_root))
    monkeypatch.setattr(candidate_incremental, '_resolve_commit',
        lambda root, ref: 'b'*40 if ref == 'task/TASK-1' else 'e'*40)
    monkeypatch.setattr(candidate_incremental, 'subprocess', SimpleNamespace(
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0)))
    monkeypatch.setattr(workflow_git_readiness, 'git_read',
        lambda *args, **kwargs: SimpleNamespace(returncode=0))
    row_change=dict(getattr(request,'param',{}))
    chain=row_change.pop('_reuse_chain',False)
    if chain:
        from tests.test_plan_completion_reuse_chain import next_revision
        next_revision(r)
    assert action(r, reviewed_digest=reviewed(r), approval_ref='owner:reviewed',
        reuse_rationale='Exact unchanged Task contract.')["ok"]

    def freeze(body):
        body = {key:value for key,value in body.items() if key not in {'freeze_receipt_ref','freeze_receipt_digest'}}
        ref = r.sidecar(body)
        return r.emit('candidate.ready',{**body,'freeze_receipt_ref':ref,'freeze_receipt_digest':ref['sha256']})

    receipt_a = r.done.payload['integration_receipt_ref']
    r.emit('integration.queue.integrated',{'task_map_generation':'generation-1','receipt_ref':receipt_a},'TASK-1')
    previous_generation='generation-3' if chain else 'new'
    generation='generation-4' if chain else 'generation-3'
    prior = freeze({**r.candidate.payload,**r.source.payload,'generation_id':previous_generation,'freeze_id':'freeze-2',
        'task_integration_receipt_refs':[receipt_a]})
    task_map = r.sidecar({'tasks':[{'task_id':'TASK-1',
        'verification_read_paths':['src/pulse.py','tests/test_pulse.py'], **row_change},
        {'task_id':'TASK-2'}],'target_commit':'c'*40})
    package = build_plan_artifact_package(workflow_run_id=r.source.correlation_id,flow_kind='issue',
        producer_stage_id='plan',run_contract=write_run_contract_snapshot(state,{'schema_version':'run-contract.v1'}),
        plan_revision=generation,task_map_generation=generation,
        produced=[{**task_map,'logical_name':'task_map'}],required_ports=['task_map'])
    pkg = write_plan_artifact_package(state,package)
    bindings={'task_map_ref':task_map['ref'],'task_map_generation':generation,
        'plan_artifact_package_ref':pkg['ref'],'plan_artifact_package_digest':pkg['sha256']}
    r.emit('plan.artifact_package.admitted',{'package_ref':pkg['ref'],'package_digest':pkg['sha256']})
    source = r.emit('task_map.ready',bindings)
    r.emit('plan.approved',{'plan_id':source.id})
    r.emit('task.pipeline.generation.admitted',{**bindings,'schema_version':'task-pipeline-generation.v1',
        'generation_id':'delta','trigger_event_id':source.id,'task_ids':['TASK-2'],'dispatch_base_commit':'c'*40})
    task = Task(id='TASK-2',status='done',contract_authority_revision='delta-authority',contract=TaskContract(
        evidence_contract={'workflow_run_id':r.source.correlation_id,'source_refs':bindings},
        validation={'commands':[{'id':'new-check','command':'pytest tests/new.py','owner':'task_verify','tier':'task_non_smoke'}]}))
    r.runtime.task_store.add(task)
    snapshot = build_task_contract_snapshot(task,workflow_run_id=r.source.correlation_id,
        task_map_generation_id=generation,base_commit='c'*40,task_ref='task/TASK-2')
    contract = write_task_contract_snapshot(state,snapshot)
    target = write_target_snapshot(state,build_target_snapshot(contract,target_commit='d'*40,contract_snapshot=snapshot))
    result = r.sidecar({'schema_version':'verification-result.v1','verdict':'passed'})
    envelope = r.sidecar({'schema_version':'call-result-envelope.v1'})
    identity={'operation_id':'verify-new','request_hash':'new-hash','control_result_ref':result}
    admission=r.emit('workflow.call.result.admitted',{**identity,'semantic_verdict':'passed'})
    r.emit('task.pipeline.verify.completed',{**identity,'task_map_generation':generation,'target_commit':'d'*40,
        'contract_revision':snapshot['contract_revision'],'contract_snapshot_ref':contract['ref'],
        'contract_snapshot_digest':contract['sha256'],'target_snapshot_ref':target['ref'],
        'target_snapshot_digest':target['sha256'],'semantic_submit_admission_event_id':admission.id,
        'call_result_envelope_ref':envelope},'TASK-2')
    r.emit('task.ref.updated',{'source_commit':'d'*40},'TASK-2')
    receipt_b=r.sidecar({'schema_version':'task-integration-receipt.v1','workflow_run_id':r.source.correlation_id,
        'task_id':'TASK-2','task_map_generation':generation,'task_commit':'d'*40,
        'new_candidate_head':'e'*40,'contract_revision':snapshot['contract_revision'],'status':'integrated'})
    r.emit('integration.queue.integrated',{'task_map_generation':generation,'receipt_ref':receipt_b},'TASK-2')
    r.emit('task.done',{'task_map_generation':generation,'candidate_head':'e'*40,'integration_receipt_ref':receipt_b},'TASK-2')
    candidate=freeze({**prior.payload,**bindings,'generation_id':'delta','freeze_id':'freeze-3',
        'candidate_head':'e'*40,'candidate_head_commit':'e'*40,'target_commit':'e'*40,'candidate_base_commit':'c'*40,
        'task_ids':['TASK-1','TASK-2'],'completed_task_ids':['TASK-1','TASK-2'],
        'task_integration_receipt_refs':[receipt_a,receipt_b]})
    r.prior=prior; r.current=candidate
    return r


def prepare(r):
    return prepare_candidate_verification_authority(r.runtime,payload={},
        workflow_run_id=r.source.correlation_id,task_id='FLOW-ANCHOR')


def test_changed_candidate_target_preserves_only_original_reviewed_task_proof(retained):
    r=retained
    fields=prepare(r)
    snapshot=json.loads((r.runtime.state_dir/fields['contract_snapshot_ref']).read_text())
    children={row['task_id']:row for row in snapshot['child_authorities']}
    assert fields['task_map_generation']=='generation-3' and fields['target_commit']=='e'*40
    assert children['TASK-1']['task_map_generation']=='generation-1'
    assert children['TASK-1']['verification_event_id']==r.verify.id
    assert children['TASK-1']['retained_task_evidence']['reviewed_candidate_event_id']==r.prior.id
    assert children['TASK-1']['completion_reuse_ref']
    assert not r.runtime.task_store.get('TASK-1').contract.scope
    assert children['TASK-2']['task_map_generation']=='generation-3'
    assert not candidate_currentness_issues(r.runtime.state_dir,r.runtime.event_log.read_all(),
        identity={'workflow_run_id':r.source.correlation_id,**fields},
        contract_ref=fields['contract_snapshot_ref'],contract_digest=fields['contract_snapshot_digest'])


@pytest.mark.parametrize('fault',['reviewed_freeze','reviewed_receipt','original_verify','prior_task_map'])
def test_retained_reviewed_sources_are_revalidated(retained,fault):
    r=retained
    fields=prepare(r)
    snapshot=json.loads((r.runtime.state_dir/fields['contract_snapshot_ref']).read_text())
    child=next(row for row in snapshot['child_authorities'] if row['task_id']=='TASK-1')
    if fault=='reviewed_freeze': ref=r.prior.payload['freeze_receipt_ref']
    elif fault=='reviewed_receipt': ref=child['completion_reuse_ref']
    elif fault=='original_verify': ref=r.verify.payload['control_result_ref']
    else:
        package=json.loads((r.runtime.state_dir/r.source.payload['plan_artifact_package_ref']).read_text())
        ref=next(row for row in package['produced'] if row['logical_name']=='task_map')
    (r.runtime.state_dir/ref['ref']).write_text('{}')
    with pytest.raises(ValueError): prepare(r)
    assert candidate_currentness_issues(r.runtime.state_dir,r.runtime.event_log.read_all(),
        identity={'workflow_run_id':r.source.correlation_id,**fields},
        contract_ref=fields['contract_snapshot_ref'],contract_digest=fields['contract_snapshot_digest'])


@pytest.mark.parametrize('retained', [
    {'execution_mode':'standard'}, {'scope':['different.py']},
    {'acceptance':'new obligation'}, {'commands':[{'owner':'task_verify'}]},
], indirect=True)
def test_retained_task_rejects_valid_new_plan_with_changed_row(retained):
    with pytest.raises(ValueError, match='differs from the approved Plan'):
        prepare(retained)


def test_scoped_review_does_not_validate_changed_sibling_as_old_task(retained):
    from zf.core.events.model import ZfEvent
    from zf.runtime.candidate_continuation import digest
    from zf.runtime.plan_completion_reuse import current_proofs

    r=retained
    events=r.runtime.event_log.read_all()
    events=deepcopy(events[:events.index(r.prior)+1])
    restored=next(e for e in events if e.type=='task.status_changed')
    receipt=json.loads((r.runtime.state_dir/restored.payload['completion_reuse_ref']['ref']).read_text())
    # A historical whole-set receipt, with a sibling whose current authority changed.
    sibling={**deepcopy(receipt['manifest']['tasks'][0]),'task_id':'TASK-2'}
    receipt['manifest']['tasks'].append(sibling)
    manifest=receipt['manifest']
    manifest['manifest_digest']=digest({k:v for k,v in manifest.items() if k!='manifest_digest'})
    descriptor=r.sidecar(receipt)
    restored.payload['completion_reuse_ref']=descriptor
    events.insert(-1,ZfEvent(type='task.status_changed',origin='kernel',task_id='TASK-2',
        payload={**restored.payload}))
    with pytest.raises(ValueError, match='Task authority changed'):
        current_proofs(r.runtime,events,events[-1])
    proofs=current_proofs(r.runtime,events,events[-1],task_ids={'TASK-1'})
    assert set(proofs)=={'TASK-1'} and proofs['TASK-1']['verification_event_id']==r.verify.id


@pytest.mark.parametrize('fault', ['changed_read_path', 'diff_timeout', 'diff_error'])
def test_retained_read_only_input_cannot_be_assumed_unchanged(retained, monkeypatch, fault):
    from subprocess import TimeoutExpired
    from zf.runtime import workflow_git_readiness
    r=retained
    fields=prepare(r)
    def changed(root, *args):
        assert args[-2:]==('src/pulse.py','tests/test_pulse.py')
        if fault=='diff_timeout': raise TimeoutExpired('git',15)
        return SimpleNamespace(returncode=128 if fault=='diff_error' else 1)
    monkeypatch.setattr(workflow_git_readiness,'git_read',changed)
    with pytest.raises(ValueError): prepare(r)
    assert candidate_currentness_issues(r.runtime.state_dir,r.runtime.event_log.read_all(),
        identity={'workflow_run_id':r.source.correlation_id,**fields},
        contract_ref=fields['contract_snapshot_ref'],contract_digest=fields['contract_snapshot_digest'])


@pytest.mark.parametrize('retained',[{'_reuse_chain':True}],indirect=True)
def test_retained_task_keeps_multihop_predecessor_and_rejects_damage(retained):
    r=retained
    fields=prepare(r)
    assert fields['task_map_generation']=='generation-4'
    snapshot=json.loads((r.runtime.state_dir/fields['contract_snapshot_ref']).read_text())
    child=next(row for row in snapshot['child_authorities'] if row['task_id']=='TASK-1')
    assert child['verification_event_id']==r.verify.id
    assert not candidate_currentness_issues(r.runtime.state_dir,r.runtime.event_log.read_all(),
        identity={'workflow_run_id':r.source.correlation_id,**fields},
        contract_ref=fields['contract_snapshot_ref'],contract_digest=fields['contract_snapshot_digest'])
    receipt=json.loads((r.runtime.state_dir/child['completion_reuse_ref']['ref']).read_text())
    previous=receipt['manifest']['tasks'][0]['predecessor_reuse_ref']
    (r.runtime.state_dir/previous['ref']).write_text('{}')
    with pytest.raises(ValueError): prepare(r)


def test_partial_plan_binding_cannot_fall_back_to_legacy(retained):
    r=retained
    body={k:v for k,v in r.current.payload.items() if k not in
        {'freeze_receipt_ref','freeze_receipt_digest','plan_artifact_package_ref'}}
    ref=r.sidecar(body)
    r.emit('candidate.ready',{**body,'freeze_receipt_ref':ref,'freeze_receipt_digest':ref['sha256']})
    with pytest.raises(ValueError,match='Plan package binding is incomplete'): prepare(r)
