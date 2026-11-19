"""Task audit host evidence must return to independent Verify, never mark done."""
from dataclasses import replace
import json
import subprocess

import pytest

from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.host_verification import reconcile_host_verification
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot, current_task_contract_identity, write_task_contract_snapshot,
)
from zf.runtime.task_host_manifest import git, preview
from zf.runtime.workflow_operation import reduce_workflow_operations
from tests.test_host_verification import policy


def audit_case(tmp_path, monkeypatch, *, verify_only=True, command_owner=None):
    def command(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    command('init', '-b', 'main')
    command('config', 'user.name', 'Audit Test')
    command('config', 'user.email', 'test@example.invalid')
    (tmp_path/'.gitignore').write_text('.state/\n')
    (tmp_path/'probe.py').write_text("print('verified')\n")
    command('add', '--', '.gitignore', 'probe.py')
    command('commit', '-m', 'test: frozen target')
    state = tmp_path/'.state'; root = state/'workdirs/task/project'
    command('worktree', 'add', '-b', 'worker-test', str(root))
    head = git(root, 'rev-parse', 'HEAD')
    owner = 'candidate_verify' if verify_only else 'task_verify'
    task = Task(id='T1', status='in_progress', assigned_to='verify',
        execution_binding=TaskExecutionBinding(workflow_run_id='run-1'),
        contract=TaskContract(behavior='audit frozen target',
            evidence_contract={'task_map_generation':'map-1', 'runtime_only':verify_only},
            acceptance_criteria=[{'id':'AC-1','statement':'probe passes',
                'verification_owner':owner,'verification_tier':'task_non_smoke',
                'verification_command_ids':['probe']}],
            validation={'commands':[{'id':'probe','command':'python3 probe.py',
                'owner':command_owner or owner,'tier':'task_non_smoke',
                'producer_task_id':'T1','timeout_seconds':30}]}))
    TaskStore(state/'kanban.json').add(task)
    snapshot = build_task_contract_snapshot(task, workflow_run_id='run-1',
        task_map_generation_id='map-1', base_commit=head, task_ref='task/T1')
    descriptor = write_task_contract_snapshot(state, snapshot)
    log = EventLog(state/'events.jsonl'); writer = EventWriter(log, default_origin='kernel')
    writer.emit('run.admission.admitted', payload={'workflow_run_id':'run-1'})
    identity = {'workflow_run_id':'run-1','task_id':'T1','task_map_generation':'map-1',
        'operation_id':'verify-1','operation_generation':1,'task_pipeline_stage':'verify',
        'active_attempt_id':'verify-attempt','dispatch_id':'dispatch-1','lease_id':'lease-1',
        'request_hash':'request-1','role_instance':'verify'}
    writer.append(ZfEvent(type='task.pipeline.generation.admitted', payload={**identity,
        'schema_version':'task-pipeline-generation.v1','task_ids':['T1']}))
    if not verify_only:
        impl = {**identity,'operation_id':'impl-1','task_pipeline_stage':'impl'}
        writer.append(ZfEvent(type='workflow.operation.requested',payload=impl))
        writer.append(ZfEvent(type='workflow.call.result.admitted',payload={**impl,
            'semantic_verdict':'passed','control_result_ref':{'source_event_id':'impl-done'}}))
        writer.append(ZfEvent(type='workflow.operation.settled',payload=impl))
    request = {'result_identity':{**current_task_contract_identity(task),
        'source_branch':'worker-test','contract_snapshot_ref':descriptor['ref'],
        'contract_snapshot_digest':descriptor['sha256']},
        'target_commit':head,'verification_owner':owner,'workdir_write_scopes':[]}
    ref = write_sidecar_json(state,'artifacts/request.json',{'request':request},
        kind='test',schema_version='test.v1',created_by='test')
    writer.append(ZfEvent(type='workflow.operation.requested',payload={**identity,'request_ref':ref}))
    writer.append(ZfEvent(type='task.pipeline.stage.dispatched',payload={**identity,
        'workdir':str(root),'source_branch':'worker-test'}))
    source = writer.append(ZfEvent(type='task.pipeline.verify.failed', origin='kernel',
        task_id='T1',payload=identity))
    result = write_sidecar_json(state,'artifacts/result.json',{
        **identity, **current_task_contract_identity(task), 'attempt_id':'verify-attempt',
        'schema_version':'verification-result.v1','execution_status':'failed','verdict':'abstained',
        'failure_class':'verifier_execution_failure','target_commit':head},
        kind='call_control_result',schema_version='verification-result.v1',
        created_by='test',source_event_id=source.id)
    writer.append(ZfEvent(type='workflow.call.result.admitted',payload={**identity,
        'semantic_verdict':'abstained','control_result_ref':result}))
    writer.append(ZfEvent(type='workflow.operation.settled',origin='kernel',payload=identity))
    (state/'refs').mkdir(); (state/'refs/task-index.json').write_text(
        json.dumps({'T1':{'source_commit':head}}))
    config = ZfConfig(); config.project.state_dir = '.state'
    config.runtime.workdirs.required_paths = []; config.runtime.workdirs.provision_paths = []
    config.runtime.host_verification = policy()
    service = ControlledActionService(state,writer,project_root=tmp_path,config=config,
        actor='run-manager',surface='runtime')
    calls = []
    def container(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, 'verified\n', '')
    monkeypatch.setattr('zf.runtime.host_verification._run_container', container)
    return service, root, calls


def test_audit_host_preview_uses_frozen_verification_owner(tmp_path, monkeypatch):
    service, root, _ = audit_case(tmp_path, monkeypatch)
    before = service.writer.event_log.path.read_bytes()
    manifest = preview(service,operation_id='verify-1',kind='verify',command_id='probe')
    assert manifest['command']['owner'] == 'candidate_verify'
    assert manifest['head'] == git(root,'rev-parse','HEAD')
    assert service.writer.event_log.path.read_bytes() == before


def test_standard_verify_cannot_run_candidate_command(tmp_path, monkeypatch):
    service, _, _ = audit_case(tmp_path,monkeypatch,verify_only=False,command_owner='candidate_verify')
    with pytest.raises(ValueError,match='different stage'):
        preview(service,operation_id='verify-1',kind='verify',command_id='probe')


@pytest.mark.parametrize('verify_only',[False,True])
def test_container_fact_resumes_verify_once_without_product_verdict(tmp_path,monkeypatch,verify_only):
    service, root, calls = audit_case(tmp_path,monkeypatch,verify_only=verify_only)
    before = git(root,'rev-parse','HEAD')
    assert reconcile_host_verification(service) == 1  # exact isolated execution
    assert reconcile_host_verification(service) == 1  # new Verify generation
    assert reconcile_host_verification(service) == 0
    assert len(calls) == 1 and git(root,'rev-parse','HEAD') == before
    events = service.writer.event_log.read_all()
    operation = reduce_workflow_operations(events)['verify-1']
    assert operation['status'] == 'superseded' and operation['semantic_verdict'] == 'abstained'
    assert 'preauthorized container' in operation['reason']
    assert TaskStore(service.state_dir/'kanban.json').get('T1').status == 'in_progress'
    assert not any(e.type in {'task.done','task.pipeline.verify.completed','dev.build.done'} for e in events)
    from tests.test_task_pipeline_reconciler import _policy
    from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
    tasks = TaskStore(service.state_dir/'kanban.json').list_all()
    plan = TaskPipelineReconciler().reconcile(policy=_policy(),tasks=tasks,
        operations=reduce_workflow_operations(events).values(),attempts=[])
    assert plan['dispatchable']['impl'] == []
    assert plan['dispatchable']['verify'][0]['operation_generation'] == '2'
    from zf.runtime.task_execution_evidence import capture_execution_evidence
    index = hydrate_sidecar_ref(service.state_dir,
        capture_execution_evidence(service.state_dir,operation,events)).payload
    assert len(index['entries']) == 1
    fact = hydrate_sidecar_ref(service.state_dir,index['entries'][0]['ref']).payload
    assert fact['output'] == 'verified\n' and fact['exit_code'] == 0
    assert fact['claims_status'] == 'host_execution_fact_not_product_verdict'


@pytest.mark.parametrize('fault',[
    'output_digest','receipt_digest','policy_changed','command_not_authorized',
    'paused','target_changed','dirty_workspace','active_turn','new_operation','duplicate_completion',
])
def test_invalid_or_stale_probe_does_not_restart_verify(tmp_path,monkeypatch,fault):
    service, root, calls = audit_case(tmp_path,monkeypatch)
    assert reconcile_host_verification(service) == 1
    log = service.writer.event_log
    done = next(e for e in log.read_all() if e.type == 'runtime.action.attempt.completed')
    descriptor = done.payload['receipt_ref']
    receipt = hydrate_sidecar_ref(service.state_dir,descriptor).payload
    if fault == 'output_digest':
        (service.state_dir/receipt['output_ref']['ref']).write_text('unbound')
    if fault == 'receipt_digest':
        (service.state_dir/descriptor['ref']).write_text('{}')
    if fault == 'policy_changed': service.config.runtime.host_verification.timeout_seconds += 1
    if fault == 'command_not_authorized': service.config.runtime.host_verification.commands = []
    if fault == 'paused':
        service.writer.emit('run.paused',payload={'workflow_run_id':'run-1'})
    if fault == 'target_changed':
        (service.state_dir/'refs/task-index.json').write_text(json.dumps({'T1':{'source_commit':'f'*40}}))
    if fault == 'dirty_workspace': (root/'probe.py').write_text('changed')
    if fault == 'active_turn':
        monkeypatch.setattr('zf.runtime.task_host_verification_handoff.active_codex_turn_from_events',
            lambda *args:{'turn_id':'active'})
    if fault == 'new_operation':
        service.writer.append(ZfEvent(type='workflow.operation.requested',payload={
            'operation_id':'new-verify','task_id':'T1','workflow_run_id':'run-1',
            'task_map_generation':'map-1','operation_generation':2,'task_pipeline_stage':'verify'}))
    if fault == 'duplicate_completion': service.writer.append(replace(done,id='duplicate-completion'))
    assert reconcile_host_verification(service) == 0
    assert len(calls) == 1
    assert not any(e.type == 'workflow.operation.superseded' for e in log.read_all())


def test_failed_probe_is_carried_to_independent_verify_without_becoming_passed(tmp_path,monkeypatch):
    service, _, _ = audit_case(tmp_path,monkeypatch)
    monkeypatch.setattr('zf.runtime.host_verification._run_container',
        lambda argv,**kwargs:subprocess.CompletedProcess(argv,7,'product assertion failed\n',''))
    assert reconcile_host_verification(service) == 1
    assert reconcile_host_verification(service) == 1
    from zf.runtime.task_execution_evidence import capture_execution_evidence
    events = service.writer.event_log.read_all()
    source = reduce_workflow_operations(events)['verify-1']
    index = hydrate_sidecar_ref(service.state_dir,
        capture_execution_evidence(service.state_dir,source,events)).payload
    fact = hydrate_sidecar_ref(service.state_dir,index['entries'][0]['ref']).payload
    assert fact['exit_code'] == 7 and fact['output'] == 'product assertion failed\n'
    assert not any(e.type == 'task.done' for e in events)


def test_actual_run_manager_tick_executes_then_hands_off_task_probe(tmp_path,monkeypatch):
    from zf.runtime.run_manager import run_manager_tick
    service, _, calls = audit_case(tmp_path,monkeypatch)
    for _ in range(2):
        run_manager_tick(state_dir=service.state_dir,writer=service.writer,
            config=service.config,project_root=service.project_root,auto_execute=True)
    assert len(calls) == 1
    assert reduce_workflow_operations(service.writer.event_log.read_all())['verify-1']['status'] == 'superseded'
