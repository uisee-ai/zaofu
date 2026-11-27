"""A frozen Reader can receive the first container outputs without granting shell access."""
import json
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from tests.test_candidate_reader_assignments import _scoped_project
from tests.test_host_verification import policy
from tests.test_reader_fanout_runtime import _config, _RecordingTransport
from zf.core.config.schema import RoleConfig
from zf.core.safety import write_workdir_owner_marker
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.host_verification import reconcile_host_verification
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_manifest import git
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.mark.parametrize('fault', ['', 'missing_read', 'unapproved', 'different_output_root', 'missing_output',
                                   'changed_target', 'damaged_signed_input', 'paused', 'verifier_obstruction', 'shared_output'])
def test_first_candidate_output_returns_to_only_its_reader(tmp_path, monkeypatch, fault):
    project = tmp_path / 'project'
    project.mkdir()
    git(project, 'init', '-b', 'main')
    git(project, 'config', 'user.email', 'test@example.invalid')
    git(project, 'config', 'user.name', 'ZaoFu Test')
    (project / '.gitignore').write_text('.zf/\n')
    git(project, 'add', '--', '.gitignore')
    git(project, 'commit', '-m', 'test: seed')
    head = git(project, 'rev-parse', 'HEAD')
    outputs = ['desktop.json', 'mobile.json']
    paths = ['ZF_EVIDENCE_DIR/' + name for name in outputs]
    if fault == 'shared_output':
        from tests import test_candidate_reader_assignments as assignment_fixture
        assignments = assignment_fixture._assignments()
        assignments[1]['acceptance'].append(assignments[0]['acceptance'][0])
        monkeypatch.setattr(assignment_fixture, '_assignments', lambda: assignments)
    seeded = _scoped_project(tmp_path, monkeypatch, seed_options={
        'candidate_head': head, 'producer_paths': outputs if fault == 'different_output_root' else paths})
    trigger = seeded.event_log.read_all()[-1]
    state = seeded.state_dir
    run = trigger.payload['workflow_run_id']
    config = _config()
    config.roles = [RoleConfig(name=role, backend='mock', role_kind='reader')
                    for role in ['reader-a', 'reader-b', 'reader-idle', 'discovery']]
    config.workflow.stages = seeded.config.workflow.stages
    config.workflow.flow_metadata = seeded.config.workflow.flow_metadata
    stage = config.workflow.stages[0]
    stage.aggregate.child_success_event = 'test.child.passed'
    stage.aggregate.child_failure_event = 'test.child.failed'
    stage.aggregate.failure_event = 'test.failed'
    config.runtime.host_verification = policy()
    probe = 'pytest -q tests/test_pulse.py'
    config.runtime.host_verification.commands = ['pytest tests/test_other.py']
    if fault != 'unapproved':
        config.runtime.host_verification.commands.append(probe)
    config.runtime.workdirs.required_paths = []
    config.runtime.workdirs.provision_paths = []
    transport = _RecordingTransport()
    runtime = Orchestrator(state, config, transport, project_root=project)
    runtime.event_writer.emit('run.started', payload={'workflow_run_id': run})
    token = provision_role_submit_credential(state, 'reader-a').read_text().strip()
    sibling_token = provision_role_submit_credential(state, 'reader-b').read_text().strip()
    runtime.run_once(events=[trigger])
    log = runtime.event_log
    dispatch = next(e for e in log.read_all() if e.type == 'fanout.child.dispatched' and e.payload['role_instance'] == 'reader-a')
    initial = dispatch.payload['payload']
    assert not initial.get('candidate_host_evidence_ref')
    service = ControlledActionService(state, runtime.event_writer, config=config, project_root=project, actor='run-manager')
    assert reconcile_host_verification(service) == 0
    submit = SemanticResultSubmitService(state_dir=state, event_log=log, event_writer=runtime.event_writer)
    def abstain(payload, role_name, credential):
        inputs = payload['required_reads']
        manifest = hydrate_sidecar_ref(state, payload['attempt_source_manifest']).payload
        for req in inputs[1:] if fault == 'missing_read' else inputs:
            read_attempt_artifact(state, manifest=manifest, source_id=req['source_id'],
                artifact_id=req['artifact_id'], json_path=req['json_path'], actor=role_name, role=role_name)
        return submit.submit(operation_id=payload['operation_id'], role_instance=role_name, credential=credential,
            semantic_result={'schema_version': 'verification-result.v1', 'execution_status': 'failed', 'verdict': 'abstained',
                'failure_class': 'verifier_execution_failure' if fault == 'verifier_obstruction' else 'reader_execution_failure',
                'summary': 'The requested evidence could not be read.', 'verification_owner': 'candidate_verify',
                'verification_tier': 'integration', 'evidence_refs': ['tool-output:exact-query'], 'requirement_results': [],
                'probe_receipts': [], 'reused_command_receipt_ids': [], 'rework_items': []})

    outcome = abstain(initial, 'reader-a', token)
    assert outcome.admitted_event_id
    runtime.run_once(events=[])
    config.runtime.workdirs.enabled = True
    config.runtime.workdirs.mode = 'worktree'
    config.runtime.workdirs.root = str(state / 'workdirs')
    def prepare_workdir(role_name):
        reader = state / 'workdirs' / role_name
        git(project, 'worktree', 'add', '--detach', str(reader / 'project'), head)
        write_workdir_owner_marker(reader, project_name='test', instance_id=role_name, project_root=project, created_by='test')
        return reader

    reader = prepare_workdir('reader-a')
    if fault == 'changed_target':
        git(reader / 'project', 'commit', '--allow-empty', '-m', 'test: stale reader workspace')
    if fault == 'damaged_signed_input':
        manifest = hydrate_sidecar_ref(state, initial['attempt_source_manifest']).payload
        source = next(row for row in manifest['sources'] if row['source_id'] == 'plan-port-goal_claim_set')
        (state / source['ref']).write_text('{}')
    if fault == 'paused':
        runtime.event_writer.emit('run.paused', payload={'workflow_run_id': run})
    calls = []
    def docker(argv, *, timeout):
        assert argv[:4] == ['docker', 'run', '--pull=never', '--rm']
        assert argv[-1] in config.runtime.host_verification.commands and 'docker.sock' not in str(argv)
        target = Path(next(v for v in argv if v.endswith(',dst=/evidence')).split('src=')[1].split(',')[0])
        for name in (outputs[:1] if fault == 'missing_output' else outputs):
            (target / name).write_text('{}')
        calls.append(argv[-1])
        return CompletedProcess(argv, 0, 'probe ok', '')
    monkeypatch.setattr('zf.runtime.host_verification._run_container', docker)
    success = fault in {'', 'verifier_obstruction', 'shared_output'}
    allowed = success or fault == 'missing_output'
    assert bool(reconcile_host_verification(service)) is allowed
    assert calls == ([probe] if allowed else [])
    assert bool(reconcile_host_verification(service)) is success
    assert reconcile_host_verification(service) == 0
    if not success:
        assert not any(e.type == 'repair.action.requested' for e in log.read_all())
        return
    repairs = [e for e in log.read_all() if e.type == 'repair.action.requested']
    assert len(repairs) == 1
    runtime.run_once(events=repairs)
    dispatches = [e.payload for e in log.read_all() if e.type == 'fanout.child.dispatched']
    assert [e['role_instance'] for e in dispatches] == ['reader-a', 'reader-b', 'reader-a']
    successor = dispatches[-1]['payload']
    assert successor['operation_id'] != initial['operation_id']
    body = hydrate_sidecar_ref(state, successor['candidate_host_evidence_ref']).payload
    assert body['consumer']['target_commit'] == head
    fact, = body['receipts']
    assert fact['source_operation_id'] == initial['operation_id']
    assert hydrate_sidecar_ref(state, fact['receipt_ref']).payload['command'] == probe
    assert [p['path'] for p in hydrate_sidecar_ref(state, fact['media_refs'][0]).payload['files']] == outputs
    assert reduce_workflow_operations(log.read_all())[initial['operation_id']]['semantic_verdict'] == 'abstained'
    if fault == 'shared_output':
        sibling, = [e['payload'] for e in dispatches if e['role_instance'] == 'reader-b']
        assert abstain(sibling, 'reader-b', sibling_token).admitted_event_id
        runtime.run_once(events=[])
        prepare_workdir('reader-b')
        assert reconcile_host_verification(service) == 1  # only the other, independently declared command
        assert reconcile_host_verification(service) == 1  # scoped Reader rebuild
        assert reconcile_host_verification(service) == 0
        latest_repair = next(e for e in reversed(log.read_all()) if e.type == 'repair.action.requested')
        runtime.run_once(events=[latest_repair])
        latest = next(e for e in reversed(log.read_all()) if e.type == 'fanout.child.dispatched')
        assert latest.payload['role_instance'] == 'reader-b'
        shared = hydrate_sidecar_ref(state, latest.payload['payload']['candidate_host_evidence_ref']).payload
        assert len(shared['receipts']) == 2
        assert fact['receipt_id'] in {row['receipt_id'] for row in shared['receipts']}
    successor_manifest = hydrate_sidecar_ref(state, successor['attempt_source_manifest']).payload
    for requirement in successor['required_reads']:
        read_attempt_artifact(state, manifest=successor_manifest, source_id=requirement['source_id'],
            artifact_id=requirement['artifact_id'], json_path=requirement['json_path'], actor='reader-a', role='reader-a')
    semantic = json.loads((state / successor['result_scratch_ref']).read_text())
    semantic.update(summary='Mock Reader checked its frozen assertions against the mock observations',
                    host_execution_receipt_ids=[fact['receipt_id']], evidence_refs=[fact['fact_ref']['ref']])
    for item in semantic['requirement_results']:
        item['evidence_refs'] = [fact['fact_ref']['ref']]
    journey, = semantic['product_acceptance_report']['journey_results']
    evidence = [{k: fact['media_refs'][0][k] for k in ('ref', 'sha256')}]
    journey['evidence_refs'] = evidence
    for assertion in journey['assertion_results']:
        assertion['evidence_refs'] = evidence
    accepted = submit.submit(operation_id=successor['operation_id'], role_instance='reader-a',
                             credential=token, semantic_result=semantic)
    admitted = next(e for e in log.read_all() if e.id == accepted.admitted_event_id)
    assert admitted.payload['semantic_verdict'] == 'passed'
    assert calls == ([probe, 'pytest tests/test_other.py'] if fault == 'shared_output' else [probe])
