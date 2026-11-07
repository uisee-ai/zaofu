"""An admitted failed Impl must hand its committed work to semantic recovery."""
from types import SimpleNamespace
import json
import subprocess

import pytest

from zf.core.events import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.config.schema import RoleConfig, WorkflowStageConfig
from zf.runtime.call_result_adapters import ControlResultAdapterRegistry
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.worker_execution_envelope import build_worker_execution_envelope
from zf.runtime.workflow_operation import WorkflowOperationService
from zf.runtime.semantic_replan import enrich_semantic_replan_action, SEMANTIC_REPLAN_ACTION
from zf.runtime.run_manager import run_manager_tick
from tests.test_run_manager_rework_triage import _state, _config


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def setup(tmp_path, fault=''):
    git(tmp_path, 'init', '-q', '-b', 'worker/partial')
    git(tmp_path, 'config', 'user.name', 'ZaoFu Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    product = tmp_path / 'product.txt'
    product.write_text('base\n')
    git(tmp_path, 'add', '--', 'product.txt')
    git(tmp_path, 'commit', '-qm', 'base')
    base = git(tmp_path, 'rev-parse', 'HEAD')
    product.write_text('partial implementation\n')
    git(tmp_path, 'add', '--', 'product.txt')
    git(tmp_path, 'commit', '-qm', 'partial')
    head = git(tmp_path, 'rev-parse', 'HEAD')
    state, log, writer = _state(tmp_path)
    task_map = state / 'task-map.json'
    task_map.write_text(json.dumps({'tasks': [{'task_id': 'PART'}]}))
    task = Task(id='PART', title='partial', status='in_progress', assigned_to='dev',
                contract=TaskContract(feature_id='FEATURE', behavior='complete partial work',
                    acceptance_criteria=['complete'], evidence_contract={
                        'workflow_run_id': 'run', 'source_refs': {'task_map_ref': str(task_map)}}))
    TaskStore(state/'kanban.json').add(task)
    identity = current_task_contract_identity(task, task_map_ref=str(task_map))
    writer.emit('task_map.ready', payload={**identity, 'workflow_run_id': 'run',
        'pdd_id': 'FEATURE', 'task_map_ref': str(task_map), 'source_commit': base})
    p = {**identity, 'workflow_run_id': 'run', 'task_id': 'PART', 'operation_id': 'impl',
         'task_pipeline_stage': 'impl', 'operation_generation': 1, 'workspace_generation': 1,
         'placement_epoch': 1, 'task_stage_session_binding': 'binding', 'role_instance': 'dev',
         'attempt_id': 'attempt', 'dispatch_id': 'dispatch', 'lease_id': 'lease',
         'workdir': str(tmp_path), 'source_branch': 'worker/partial', 'base_commit': base,
         'target_ref': base, 'canonical_success_event': 'dev.build.done',
         'canonical_failure_event': 'dev.blocked', 'output_profile_id': 'implementation',
         'output_profile_revision': '1'}
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=writer)
    ensured = service.ensure_operation(workflow_run_id='run', operation_id='impl',
        operation_type='task-stage', request=p, task_id='PART', role_instance='dev',
        active_attempt_id='attempt', lease_id='lease')
    request_hash = ensured.request_hash
    writer.append(ZfEvent(type='workflow.operation.started', origin='kernel', task_id='PART',
        payload={**p, 'request_hash': request_hash, 'active_attempt_id': 'attempt'}))
    p['worker_execution_envelope'] = build_worker_execution_envelope(payload=p,
        role=SimpleNamespace(instance_id='dev', name='dev', backend='mock', skills=[]),
        execution_profile=SimpleNamespace(profile_id='bounded-direct-v1', profile_digest='profile'))
    dispatch = dict(p)
    if fault == 'dispatch_origin': origin = 'worker'
    else: origin = 'kernel'
    if fault == 'dispatch_digest': dispatch['worker_execution_envelope']['workspace']['branch'] = 'foreign'
    writer.append(ZfEvent(type='task.pipeline.stage.dispatched', origin=origin,
        task_id='PART', payload=dispatch))
    failed_payload = {**p, 'request_hash': request_hash, 'source_commit': head,
                      'failure_class': 'upstream_contract_gap'}
    if fault in {'attempt_id','workflow_run_id','task_map_generation','contract_revision','source_branch'}:
        failed_payload[fault] = 'foreign'
    blocked = writer.emit('dev.blocked', task_id='PART', actor='dev', payload=failed_payload)
    _, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        state, profile_id='implementation', revision='1', event_type='dev.blocked',
        semantic_result={'target_commit':head,'verdict':'blocked'},
        identity={k:v for k,v in failed_payload.items() if k not in
                  {'source_commit','worker_execution_envelope','workdir'}},
        source_event_id=blocked.id, actor='dev', task_id='PART', correlation_id='run')
    assert not adapted.issues
    assert adapted.payload['target_commit']==head and 'source_commit' not in adapted.payload
    ref=adapted.descriptor
    if fault != 'missing_admission':
        writer.append(ZfEvent(type='workflow.call.result.admitted',
            origin='worker' if fault=='admission_origin' else '', task_id='PART',
            payload={'workflow_run_id':'run','operation_id':'impl','request_hash':request_hash,
                     'control_result_ref':ref,'semantic_verdict':'blocked',
                     'admission_status':'admitted','source_event_id':blocked.id}))
    service.settle(workflow_run_id='run',operation_id='impl',request_hash=request_hash,
                   admitted_call_result_ref=ref,task_id='PART')
    writer.emit('task.rework.triage.completed',task_id='PART',payload={
        'task_id':'PART','failed_event_id':blocked.id,'failed_event_type':'dev.blocked',
        'classification':'design_issue','recommended_action':'request_replan',
        'retryable':False,'is_terminal':False})
    if fault == 'corrupt_result': (state/ref['ref']).write_text('{}')
    if fault == 'branch_moved':
        product.write_text('later unrelated modification\n');git(tmp_path,'add','--','product.txt')
        git(tmp_path,'commit','-qm','later')
    if fault == 'newer_impl':
        service.ensure_operation(workflow_run_id='run',operation_id='newer',operation_type='task-stage',
            task_id='PART',request={**p,'operation_generation':2})
    config = _config()
    config.roles.append(RoleConfig(name='discovery',backend='mock'))
    config.workflow.stages.append(WorkflowStageConfig(id='discovery',trigger='flow.discovery.requested',
        topology='fanout_reader',roles=['discovery'],flow_kind='prd'))
    action={'action':SEMANTIC_REPLAN_ACTION,'task_id':'PART','source_event_id':blocked.id,
            'failure_event_ids':[blocked.id]}
    return state,log,writer,config,action,base,head


def test_run_manager_hands_off_admitted_impl_partial_commit(tmp_path):
    state,log,writer,config,_,base,head=setup(tmp_path)
    result=run_manager_tick(state_dir=state,event_log=log,writer=writer,config=config,
                            project_root=tmp_path,spawn_repairs=False)
    assert result.actions_applied == 1
    request=next(e for e in log.read_all() if e.type=='flow.discovery.requested')
    assert request.payload['handoff_kind']=='partial_task_continuation'
    assert request.payload['target_ref']==head
    assert request.payload['continuation_commit']==head
    assert request.payload['base_commit']==base
    assert request.payload['source_commit']==base


@pytest.mark.parametrize('fault',['missing_admission','attempt_id','workflow_run_id',
    'task_map_generation','contract_revision','source_branch','dispatch_origin','dispatch_digest',
    'corrupt_result','branch_moved','newer_impl','admission_origin'])
def test_unbound_or_stale_partial_claim_is_not_recovered(tmp_path,fault):
    state,log,_,config,action,base,_=setup(tmp_path,fault)
    enriched=enrich_semantic_replan_action(action,state_dir=state,events=log.read_all(),
        config=config,project_root=tmp_path)
    assert not enriched.get('continuation_commit')
    assert enriched.get('target_ref')==base


def test_uncommitted_edits_are_preserved_but_not_the_scan_target(tmp_path):
    state,log,_,config,action,_,head=setup(tmp_path)
    (tmp_path/'product.txt').write_text('uncommitted work\n')
    enriched=enrich_semantic_replan_action(action,state_dir=state,events=log.read_all(),
        config=config,project_root=tmp_path)
    assert enriched['target_ref']==head
    assert git(tmp_path,'show',head+':product.txt')=='partial implementation'
    assert (tmp_path/'product.txt').read_text()=='uncommitted work\n'
    assert enriched.get('partial_worktree_clean') is not True
