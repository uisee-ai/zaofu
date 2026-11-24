"""Native mixed-generation freeze must feed Candidate preparation and admission."""
from copy import deepcopy
import json

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidates import CandidateRebuilder
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot, build_target_snapshot,
    write_task_contract_snapshot, write_target_snapshot,
)
from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
from zf.runtime.candidate_currentness import candidate_currentness_issues
from tests.test_candidate_freeze_receipts import mixed_generation_freeze_case


@pytest.fixture
def mixed(tmp_path, monkeypatch):
    original_integrate = CandidateRebuilder.integrate_task_pipeline_task
    def integrate_with_verified_task(self, **kw):
        from zf.core.task.store import TaskStore
        from tests.test_candidate_incremental import _git
        store = TaskStore(self.state_dir / 'kanban.json')
        task = store.get(kw['task_id'])
        task.contract.evidence_contract.update({'source_refs':{'task_map_generation':kw['task_map_generation']}})
        store.update(task.id, contract=task.contract)
        task = store.get(task.id)
        snapshot = build_task_contract_snapshot(task, workflow_run_id=kw['workflow_run_id'],
            task_map_generation_id=kw['task_map_generation'], base_commit=kw['dispatch_base_commit'],
            task_ref='task/' + task.id)
        contract = write_task_contract_snapshot(self.state_dir, snapshot)
        commit = _git(self.project_root, 'rev-parse', 'task/' + task.id)
        kw['event_writer'].append(ZfEvent(type='task.ref.updated', origin='kernel', task_id=task.id,
            correlation_id=kw['workflow_run_id'], payload={'workflow_run_id':kw['workflow_run_id'],
            'task_ref':'task/' + task.id, 'source_commit':commit}))
        target = write_target_snapshot(self.state_dir, build_target_snapshot(contract,
            target_commit=commit, contract_snapshot=snapshot))
        def sidecar(body):
            return write_immutable_json_sidecar(self.state_dir, body, root='repro', kind='test',
                schema_version=body['schema_version'], created_by='isolated-test')
        control = sidecar({'schema_version':'verification-result.v1', 'verdict':'passed'})
        envelope = sidecar({'schema_version':'call-result-envelope.v1'})
        identity = {'workflow_run_id':kw['workflow_run_id'], 'operation_id':'verify-'+task.id,
            'request_hash':'fixture-'+task.id, 'control_result_ref':control}
        admitted = kw['event_writer'].append(ZfEvent(type='workflow.call.result.admitted',
            origin='kernel', task_id=task.id, correlation_id=kw['workflow_run_id'],
            payload={**identity,'semantic_verdict':'passed'}))
        kw['event_writer'].append(ZfEvent(type='task.pipeline.verify.completed', origin='kernel',
            task_id=task.id, correlation_id=kw['workflow_run_id'], payload={**identity,
            'task_map_generation':kw['task_map_generation'], 'target_commit':commit,
            'contract_revision':snapshot['contract_revision'],
            'contract_snapshot_ref':contract['ref'], 'contract_snapshot_digest':contract['sha256'],
            'target_snapshot_ref':target['ref'], 'target_snapshot_digest':target['sha256'],
            'semantic_submit_admission_event_id':admitted.id, 'call_result_envelope_ref':envelope}))
        kw['contract_revision'] = snapshot['contract_revision']
        return original_integrate(self, **kw)
    monkeypatch.setattr(CandidateRebuilder, 'integrate_task_pipeline_task', integrate_with_verified_task)
    runtime = mixed_generation_freeze_case(tmp_path)
    from zf.core.state.session import SessionStore
    SessionStore(runtime.state_dir/'session.yaml').create(str(runtime.project_root))
    return runtime


def prepare(runtime):
    return prepare_candidate_verification_authority(runtime, payload={},
        workflow_run_id='run-1', task_id='FLOW-ANCHOR')


def currentness(runtime, fields):
    return candidate_currentness_issues(runtime.state_dir, runtime.event_log.read_all(),
        identity={'workflow_run_id':'run-1', **fields},
        contract_ref=fields['contract_snapshot_ref'], contract_digest=fields['contract_snapshot_digest'])


def test_mixed_native_freeze_preserves_task_evidence_and_new_candidate_identity(mixed):
    before = [e.id for e in mixed.event_log.read_all()]
    fields = prepare(mixed)
    snapshot = json.loads((mixed.state_dir / fields['contract_snapshot_ref']).read_text())
    children = {row['task_id']:row for row in snapshot['child_authorities']}
    assert fields['task_map_generation'] == 'map-g2'
    assert children['TASK-A']['task_map_generation'] == 'map-g1'
    assert children['TASK-B']['task_map_generation'] == 'map-g2'
    assert children['TASK-A']['retained_task_evidence']['verification_event_id'] == children['TASK-A']['verification_event_id']
    assert 'retained_task_evidence' not in children['TASK-B']
    assert currentness(mixed, fields) == []
    assert [e.id for e in mixed.event_log.read_all()] == before


def test_retained_input_changed_in_new_candidate_requires_refresh(mixed):
    from tests.test_candidate_incremental import _git
    root=mixed.project_root
    candidate=next(e for e in mixed.event_log.read_all() if e.type=='candidate.ready')
    snapshot_task=mixed.task_store.get('TASK-A')
    path=snapshot_task.contract.scope[0]
    _git(root,'checkout','-q',candidate.payload['candidate_ref'].removeprefix('refs/heads/'))
    (root/path).write_text('changed retained input')
    _git(root,'add',path)
    _git(root,'commit','-q','-m','test: change retained input')
    head=_git(root,'rev-parse','HEAD')
    body={k:v for k,v in candidate.payload.items() if k not in {'freeze_receipt_ref','freeze_receipt_digest'}}
    body.update(candidate_head=head,candidate_head_commit=head,target_commit=head)
    ref=write_immutable_json_sidecar(mixed.state_dir,body,root='repro',kind='test',
        schema_version=body['schema_version'],created_by='isolated-test')
    mixed.event_writer.append(ZfEvent(type='candidate.ready',origin='kernel',correlation_id='run-1',
        payload={**body,'freeze_receipt_ref':ref,'freeze_receipt_digest':ref['sha256']}))
    with pytest.raises(ValueError,match='declared input changed'):
        prepare(mixed)


@pytest.mark.parametrize('session', ['broken: [', '[]', 'project_root: [wrong]'])
def test_retained_currentness_rejects_unreadable_project_binding(mixed, session):
    fields=prepare(mixed)
    del mixed.project_root
    (mixed.state_dir/'session.yaml').write_text(session)
    with pytest.raises(ValueError): prepare(mixed)
    assert currentness(mixed,fields)


def test_retained_currentness_uses_session_project_with_external_state_dir(mixed,tmp_path):
    from shutil import copytree
    from zf.core.task.store import TaskStore
    external=tmp_path/'external-runtime'
    copytree(mixed.state_dir,external)
    mixed.state_dir=external
    mixed.task_store=TaskStore(external/'kanban.json')
    fields=prepare(mixed)
    assert not currentness(mixed,fields)


@pytest.mark.parametrize('fault', ['verify_result', 'integration_receipt', 'task_reopened', 'task_contract',
                                  'new_context', 'missing_admission', 'wrong_commit', 'forged_freeze', 'active_operation',
                                  'task_ref_index', 'task_git_ref', 'missing_git_ref', 'smoke_receipt', 'candidate_ancestry'])
def test_retained_authority_rejects_stale_or_unproven_task(mixed, fault):
    fields = prepare(mixed)
    events = mixed.event_log.read_all()
    verify = next(e for e in events if e.type == 'task.pipeline.verify.completed' and e.task_id == 'TASK-A')
    done = next(e for e in events if e.type == 'task.done' and e.task_id == 'TASK-A')
    if fault in {'task_ref_index','task_git_ref','missing_git_ref','smoke_receipt','candidate_ancestry'}:
        from tests.test_candidate_incremental import _git
        receipt=json.loads((mixed.state_dir/done.payload['integration_receipt_ref']['ref']).read_text())
        if fault=='task_ref_index':
            index_path=mixed.state_dir/'refs/task-index.json'
            index=json.loads(index_path.read_text())
            index['TASK-A']['source_commit']='0'*40
            index_path.write_text(json.dumps(index))
        elif fault=='task_git_ref':
            _git(mixed.project_root,'update-ref',receipt['task_ref'],receipt['expected_candidate_head'])
        elif fault=='missing_git_ref':
            _git(mixed.project_root,'update-ref','-d','refs/heads/'+receipt['task_ref'])
        elif fault=='smoke_receipt':
            (mixed.state_dir/receipt['rolling_smoke_receipt_refs'][0]['ref']).write_text('{}')
        else:
            _git(mixed.project_root,'update-ref','refs/heads/'+receipt['candidate_branch'],receipt['expected_candidate_head'])
    elif fault in {'verify_result','integration_receipt'}:
        ref = verify.payload['control_result_ref'] if fault == 'verify_result' else done.payload['integration_receipt_ref']
        (mixed.state_dir / ref['ref']).write_text('{}')
    elif fault == 'task_reopened':
        task = deepcopy(mixed.task_store.get('TASK-A')); task.status='backlog'
        mixed.task_store.reopen(task)
    elif fault == 'task_contract':
        from zf.runtime.task_contract_authority import TaskContractAuthorityService
        task = mixed.task_store.get('TASK-A'); contract = deepcopy(task.contract)
        contract.acceptance = 'changed obligation'
        TaskContractAuthorityService(task_store=mixed.task_store, event_writer=mixed.event_writer,
            state_dir=mixed.state_dir).replace(task, contract=contract, source='test', reopen_terminal=True,
            task_updates={'status':'backlog'})
        mixed.task_store.update(task.id,status='done')
    elif fault == 'new_context':
        mixed.event_writer.append(ZfEvent(type='task.pipeline.generation.admitted',origin='kernel',
            payload={'schema_version':'task-pipeline-generation.v1','workflow_run_id':'run-1',
                     'task_ids':['TASK-A'],'task_map_generation':'map-g3','generation_id':'g3'}))
    elif fault == 'active_operation':
        mixed.event_writer.append(ZfEvent(type='workflow.operation.requested',origin='kernel',task_id='TASK-A',
            payload={'workflow_run_id':'run-1','task_id':'TASK-A','operation_id':'new-A',
                     'request_hash':'new','operation_type':'task_pipeline_impl'}))
    elif fault in {'missing_admission','wrong_commit'}:
        # In-memory corrupted read view: never rewrite even this fixture's ledger.
        changed = deepcopy(events)
        if fault == 'missing_admission':
            changed = [e for e in changed if e.id != verify.payload['semantic_submit_admission_event_id']]
        else:
            next(e for e in changed if e.id == verify.id).payload['target_commit'] = '0'*40
        mixed.event_log.read_all = lambda: changed
    else:
        changed=deepcopy(events)
        candidate=next(e for e in changed if e.type=='candidate.ready')
        candidate.payload['freeze_id']='forged'
        mixed.event_log.read_all=lambda:changed
    assert currentness(mixed, fields)
    with pytest.raises(ValueError):
        prepare(mixed)


@pytest.mark.parametrize('omit_command', [False, True])
def test_mixed_candidate_real_submission_keeps_all_new_target_obligations(mixed, omit_command):
    from zf.core.task.schema import Task
    from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
    from zf.runtime.artifact_read_ledger import read_attempt_artifact
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.task_pipeline_briefing import verification_result_template
    from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential

    mixed.task_store.add(Task(id='FLOW-ANCHOR',status='in_progress'))
    mixed.config.workflow.flow_metadata={'flow_kind':'issue','artifact_package_mode':'shadow',
        'result_protocol':{'mode':'blocking','semantic_submit_profiles':{'candidate-verify':'blocking'}}}
    candidate=next(e for e in mixed.event_log.read_all() if e.type=='candidate.ready')
    token=provision_role_submit_credential(mixed.state_dir,'verify-lane-0').read_text().strip()
    payload={'workflow_run_id':'run-1','task_id':'FLOW-ANCHOR','fanout_id':'candidate-test',
        'child_id':'verify-lane-0','role_instance':'verify-lane-0','stage_id':'issue-lanes-verify',
        'output_profile_id':'candidate-verify','artifact_package_mode':'shadow',
        'target_commit':candidate.payload['candidate_head_commit'],
        'canonical_success_event':'test.passed','canonical_failure_event':'test.failed'}
    prepared=prepare_call_operation(mixed,payload=payload,operation_type='fanout_reader_child',
        operation_key='retained-test',stage_id='issue-lanes-verify',task_id='FLOW-ANCHOR',
        dispatch_id='candidate-test',causation_id=candidate.id)
    manifest=hydrate_sidecar_ref(mixed.state_dir,payload['attempt_source_manifest']).payload
    for requirement in payload['required_reads']:
        read_attempt_artifact(mixed.state_dir,manifest=manifest,source_id=requirement['source_id'],
            artifact_id=requirement['artifact_id'],json_path=requirement['json_path'])
    mark_call_operation_started(mixed,prepared,task_id='FLOW-ANCHOR',dispatch_id='candidate-test',causation_id=candidate.id)
    snapshot=json.loads((mixed.state_dir/payload['contract_snapshot_ref']).read_text())
    result=verification_result_template(snapshot)
    result.update(summary='Synthetic independent Candidate result for binding regression.',evidence_refs=['event:test-evidence'])
    for probe in result['probe_receipts']:
        probe.update(target_commit=candidate.payload['candidate_head_commit'],evidence_refs=['event:test-command'])
    for row in result['requirement_results']:
        row['evidence_refs']=['event:test-acceptance']
    if omit_command:
        result['probe_receipts'].pop()
    service=SemanticResultSubmitService(state_dir=mixed.state_dir,event_log=mixed.event_log,event_writer=mixed.event_writer)
    if omit_command:
        with pytest.raises(ValueError):
            service.submit(operation_id=prepared.operation_id,semantic_result=result,
                role_instance='verify-lane-0',credential=token)
    else:
        submitted=service.submit(operation_id=prepared.operation_id,semantic_result=result,
            role_instance='verify-lane-0',credential=token)
        assert submitted.admitted_event_id


def test_reader_and_judge_reject_unproved_completed_task_replacement(mixed):
    from dataclasses import replace
    from zf.runtime.candidate_child_authority import candidate_child_authority_issues

    events = mixed.event_log.read_all()
    original = next(e for e in events if e.type == 'candidate.ready')
    declared = {k: v for k, v in original.payload.items()
                if k not in {'freeze_receipt_ref', 'freeze_receipt_digest'}}
    declared['completed_task_replacements'] = {'TASK-A': [{'edges': {'TASK-A': ['TASK-B']}}]}
    declared['completed_task_ids'] = ['TASK-B']
    declared['superseded_task_ids'] = ['TASK-A']
    ref = write_immutable_json_sidecar(mixed.state_dir, declared, root='repro', kind='test',
                                       schema_version=declared['schema_version'], created_by='isolated-test')
    forged = mixed.event_writer.append(replace(original, id='evt-unproved-replacement',
        payload={**declared, 'freeze_receipt_ref': ref, 'freeze_receipt_digest': ref['sha256']}))
    with pytest.raises(ValueError, match='replacement'):
        prepare(mixed)
    issues = candidate_child_authority_issues(mixed.state_dir, mixed.event_log.read_all(), forged,
                                              [{'task_id': 'TASK-B'}], require_all=True)
    assert len(issues) == 1 and 'replacement' in issues[0]['message']
