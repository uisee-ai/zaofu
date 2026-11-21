"""Independent Candidate readers can consume authenticated execution facts."""
import base64
from dataclasses import asdict
import hashlib
from types import SimpleNamespace

import pytest

from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from tests.test_host_verification import policy
from zf.core.events import ZfEvent
from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
from zf.runtime.sidecar_refs import write_sidecar_json, write_sidecar_text, hydrate_sidecar_ref
from zf.runtime.task_host_manifest import digest
from zf.runtime.task_host_evidence import IDENTITY
from zf.runtime.workflow_operation import reduce_workflow_operations


def prepare(runtime, seeded, role):
    payload = {'workflow_run_id': seeded['run_id'], 'task_id': 'FLOW-ANCHOR',
        'fanout_id': 'fanout-candidate', 'child_id': role, 'role_instance': role,
        'stage_id': 'issue-lanes-verify', 'output_profile_id': 'candidate-verify',
        'artifact_package_mode': 'shadow', 'target_commit': seeded['candidate_head'],
        'canonical_success_event': 'test.passed', 'canonical_failure_event': 'test.failed'}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type='fanout_reader_child',
        operation_key=role, stage_id='issue-lanes-verify', task_id='FLOW-ANCHOR',
        dispatch_id='attempt-' + role, causation_id='evt-candidate-ready')
    return prepared, payload


@pytest.fixture
def evidence(tmp_path, request):
    fault = getattr(request, 'param', '')
    runtime = _runtime(tmp_path)
    seeded = _seed_frozen_candidate(runtime)
    configured = policy()
    configured.commands = [seeded['command']]
    runtime.config.runtime = SimpleNamespace(host_verification=configured)
    prepared, payload = prepare(runtime, seeded, 'source-reader')
    mark_call_operation_started(runtime, prepared, task_id='FLOW-ANCHOR',
        dispatch_id='attempt-source-reader', causation_id='evt-candidate-ready')
    op = reduce_workflow_operations(runtime.event_log.read_all())[prepared.operation_id]
    identity = {k: op.get(k) for k in IDENTITY}
    identity['operation_generation'] = 0
    runtime.event_writer.append(ZfEvent(type='workflow.operation.settled', origin='kernel',
        task_id='FLOW-ANCHOR', correlation_id=seeded['run_id'], payload={
            **identity, 'semantic_verdict': 'blocked'}))
    contract = hydrate_sidecar_ref(runtime.state_dir, {
        'ref': payload['contract_snapshot_ref'], 'sha256': payload['contract_snapshot_digest']}).payload
    command = contract['verification_commands'][0]
    if fault == 'wrong-command':
        command = {**command, 'command_digest': 'f' * 64}
    manifest = {'schema_version': 'task-host-manifest.v1', 'kind': 'verify',
        'operation_id': prepared.operation_id, 'identity': {k: v for k, v in identity.items() if k != 'operation_id'},
        'contract_digest': payload['contract_snapshot_digest'], 'head': seeded['candidate_head'],
        'command': command, 'files': [], 'dependency_digest': 'b' * 64}
    manifest['manifest_digest'] = digest(manifest)
    if fault == 'wrong-contract':
        manifest['contract_digest'] = 'e' * 64
        manifest['manifest_digest'] = digest({k: v for k, v in manifest.items() if k != 'manifest_digest'})
    action = {**identity, 'action': 'task-host-assist', 'kind': 'verify',
        'approval_ref': configured.approval_ref, 'manifest_digest': manifest['manifest_digest']}
    started = runtime.event_writer.append(ZfEvent(type='runtime.action.attempt.started',
        origin='kernel', actor='run-manager', task_id='FLOW-ANCHOR',
        correlation_id=seeded['run_id'], payload=action))
    output = write_sidecar_text(runtime.state_dir, 'artifacts/host-output.txt', 'one command passed\n',
        kind='host_probe_output', schema_version='host-probe-output.v1',
        created_by='run-manager', source_event_id=started.id)
    if fault in {'allowed-scope', 'denied-scope'}:
        output['access_scope'] = {'roles': ['consumer-submit' if fault == 'allowed-scope' else 'different-role'],
                                 'purposes': ['candidate-verify']}
    if fault == 'partial-scope':
        output['allowed_paths'] = ['$.limited']
    file_bytes = b'{"ok": true}'
    media = write_sidecar_json(runtime.state_dir, 'artifacts/host-media.json', {
        'image': configured.image, 'target_commit': seeded['candidate_head'], 'files': [{
            'path': 'artifacts/probe.json', 'sha256': ('0' * 64 if fault == 'bad-media-sha'
                                                     else hashlib.sha256(file_bytes).hexdigest()),
            'base64': base64.b64encode(file_bytes).decode()}]},
        kind='container_evidence', schema_version='container-evidence.v1', created_by='run-manager')
    receipt = write_sidecar_json(runtime.state_dir, 'artifacts/host-receipt.json', {
        **action, 'schema_version': 'task-host-receipt.v1', 'status': 'completed',
        'exit_code': 1 if fault == 'nonzero' else 0,
        'source_stable': True, 'target_commit': seeded['candidate_head'],
        'command_id': command['command_id'], 'command': command['command'],
        'command_digest': command['command_digest'], 'execution_origin': 'preauthorized_container',
        'image': configured.image, 'policy_digest': digest(asdict(configured)),
        'manifest': manifest, 'output_ref': output, 'evidence_ref': media,
        'product_verdict': 'not_evaluated'}, kind='task_host_receipt',
        schema_version='task-host-receipt.v1', created_by='run-manager', source_event_id=started.id)
    completed = runtime.event_writer.append(ZfEvent(type='runtime.action.attempt.completed',
        origin='kernel', actor='run-manager', task_id='FLOW-ANCHOR', correlation_id=seeded['run_id'],
        causation_id=started.id, payload={**action, 'status': 'completed', 'receipt_ref': receipt}))
    return runtime, seeded, prepared, payload, receipt, completed


def test_new_consumers_receive_bound_facts_without_reexecuting_or_mutating_source(evidence):
    runtime, seeded, source_op, source_payload, receipt, completed = evidence
    original = (runtime.state_dir / receipt['ref']).read_bytes()
    before = sum(e.type == 'runtime.action.attempt.started' for e in runtime.event_log.read_all())
    for role in ('consumer-one', 'consumer-two'):
        prepared, payload = prepare(runtime, seeded, role)
        ref = payload.get('candidate_host_evidence_ref')
        assert ref, 'actual Reader call preparation must freeze the host consumption authority'
        binding = hydrate_sidecar_ref(runtime.state_dir, ref).payload
        assert binding['consumer']['operation_id'] == prepared.operation_id
        assert binding['consumer']['attempt_id'] == 'attempt-' + role
        assert binding['receipts'][0]['receipt_id'] == completed.id
        assert binding['receipts'][0]['source_operation_id'] == source_op.operation_id
        assert binding['receipts'][0]['receipt_ref'] == receipt
        assert binding['authorizes_execution'] is False
        reads = {row['source_id'] for row in payload['required_reads']}
        assert 'candidate-host-execution' in reads
        assert any(name.startswith('candidate-host-fact-') for name in reads)
        replay, replay_payload = prepare(runtime, seeded, role)
        assert replay.replay_hit and replay.request_hash == prepared.request_hash
        assert replay_payload['candidate_host_evidence_ref'] == ref
    source_replay, original_payload = prepare(runtime, seeded, 'source-reader')
    assert source_replay.replay_hit and source_replay.request_hash == source_op.request_hash
    assert not original_payload.get('candidate_host_evidence_ref')
    assert (runtime.state_dir / receipt['ref']).read_bytes() == original
    assert sum(e.type == 'runtime.action.attempt.started' for e in runtime.event_log.read_all()) == before
    assert not any(e.type in {'test.passed', 'task.done'} for e in runtime.event_log.read_all())


def test_shared_host_fact_validation_is_read_only_and_accepts_generation_zero(evidence):
    from zf.runtime.task_host_evidence import host_execution_facts
    runtime, _, source_op, _, _, completed = evidence
    events = runtime.event_log.read_all()
    source = reduce_workflow_operations(events)[source_op.operation_id]
    before = {p: p.read_bytes() for p in runtime.state_dir.rglob('*') if p.is_file()}
    facts = host_execution_facts(runtime.state_dir, source, events)
    assert [f['event_id'] for f in facts] == [completed.id]
    assert facts[0]['status'] == 'available'
    assert before == {p: p.read_bytes() for p in runtime.state_dir.rglob('*') if p.is_file()}


def consumer_result(evidence, role='consumer-submit', *, read_host=True):
    from zf.runtime.artifact_read_ledger import read_attempt_artifact
    from zf.runtime.task_pipeline_briefing import verification_result_template
    from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential

    runtime, seeded, _, _, _, completed = evidence
    token = provision_role_submit_credential(runtime.state_dir, role).read_text().strip()
    prepared, payload = prepare(runtime, seeded, role)
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload['attempt_source_manifest']).payload
    for required in payload['required_reads']:
        if not read_host and required['source_id'].startswith('candidate-host-'):
            continue
        read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=required['source_id'],
            artifact_id=required['artifact_id'], json_path=required['json_path'], role=role, actor=role)
    mark_call_operation_started(runtime, prepared, task_id='FLOW-ANCHOR',
        dispatch_id='attempt-' + role, causation_id='evt-candidate-ready')
    contract = hydrate_sidecar_ref(runtime.state_dir, {
        'ref': payload['contract_snapshot_ref'], 'sha256': payload['contract_snapshot_digest']}).payload
    semantic = verification_result_template(contract)
    semantic.update(summary='independent acceptance evaluated with authenticated host execution facts',
        probe_receipts=[], reused_command_receipt_ids=[], host_execution_receipt_ids=[completed.id],
        evidence_refs=['event:independent-acceptance-report'])
    for row in semantic['requirement_results']:
        row['evidence_refs'] = ['event:independent-acceptance-report']
    service = SemanticResultSubmitService(state_dir=runtime.state_dir,
        event_log=runtime.event_log, event_writer=runtime.event_writer)
    kwargs = dict(operation_id=prepared.operation_id, semantic_result=semantic,
                  role_instance=role, credential=token)
    return prepared, payload, service, kwargs


def test_actual_preflight_and_admission_consume_same_receipt_independently(evidence):
    runtime, _, source_op, _, receipt, _ = evidence
    for role in ('consumer-one', 'consumer-two'):
        prepared, _, service, kwargs = consumer_result(evidence, role)
        before = {p: p.read_bytes() for p in runtime.state_dir.rglob('*') if p.is_file()}
        validation = service.validate_plan_candidate(**kwargs)
        assert validation['status'] == 'passed', validation
        assert before == {p: p.read_bytes() for p in runtime.state_dir.rglob('*') if p.is_file()}
        submitted = service.submit(**kwargs)
        assert submitted.admitted_event_id
        assert prepared.operation_id != source_op.operation_id
    body = hydrate_sidecar_ref(runtime.state_dir, receipt).payload
    assert body['operation_id'] == source_op.operation_id
    assert sum(e.type == 'runtime.action.attempt.started' for e in runtime.event_log.read_all()) == 1


@pytest.mark.parametrize('fault', ['unknown-id', 'duplicate-id', 'missing-output', 'changed-receipt',
                                  'missing-media', 'changed-media', 'duplicate-completion', 'no-ac'])
def test_preflight_rejects_unbound_or_incomplete_host_claims(evidence, fault):
    runtime, _, _, _, receipt_ref, completed = evidence
    _, payload, service, kwargs = consumer_result(evidence)
    semantic = kwargs['semantic_result']
    receipt = hydrate_sidecar_ref(runtime.state_dir, receipt_ref).payload
    if fault == 'unknown-id':
        semantic['host_execution_receipt_ids'] = ['evt-unbound']
    elif fault == 'duplicate-id':
        semantic['host_execution_receipt_ids'] *= 2
    elif fault == 'missing-output':
        (runtime.state_dir / receipt['output_ref']['ref']).unlink()
    elif fault == 'changed-receipt':
        (runtime.state_dir / receipt_ref['ref']).write_text('{}')
    elif fault == 'missing-media':
        (runtime.state_dir / receipt['evidence_ref']['ref']).unlink()
    elif fault == 'changed-media':
        binding = hydrate_sidecar_ref(runtime.state_dir, payload['candidate_host_evidence_ref']).payload
        index = hydrate_sidecar_ref(runtime.state_dir, binding['receipts'][0]['media_refs'][0]).payload
        target = runtime.state_dir / index['files'][0]['ref']
        target.chmod(0o644)
        target.write_bytes(b'changed')
    elif fault == 'duplicate-completion':
        runtime.event_writer.append(ZfEvent(type=completed.type, origin='kernel', actor=completed.actor,
            task_id=completed.task_id, correlation_id=completed.correlation_id,
            causation_id=completed.causation_id, payload=completed.payload))
    elif fault == 'no-ac':
        semantic['requirement_results'] = []
    result = service.validate_plan_candidate(**kwargs)
    assert result['status'] == 'failed', result
    assert not any(e.type == 'workflow.call.result.admitted' for e in runtime.event_log.read_all())


@pytest.mark.parametrize('field', ['operation_id', 'attempt_id', 'request_hash', 'workflow_run_id',
                                  'task_id', 'task_map_generation', 'target_commit',
                                  'contract_snapshot_digest', 'target_snapshot_digest'])
def test_consumption_rejects_wrong_identity(evidence, field):
    from zf.runtime.candidate_host_consumption import admitted_host_commands
    runtime, _, _, _, _, completed = evidence
    prepared, payload = prepare(runtime, evidence[1], 'consumer-identity')
    op = reduce_workflow_operations(runtime.event_log.read_all())[prepared.operation_id]
    result = {**payload, 'host_execution_receipt_ids': [completed.id],
              'request_hash': prepared.request_hash, 'verdict': 'passed'}
    result[field] = 'wrong'
    with pytest.raises(ValueError, match='consumer binding mismatch'):
        admitted_host_commands(runtime.state_dir, result, operation=op, events=runtime.event_log.read_all())


def test_legacy_source_cannot_claim_later_receipt(evidence):
    from zf.runtime.candidate_host_consumption import admitted_host_commands
    runtime, _, source, payload, _, completed = evidence
    op = reduce_workflow_operations(runtime.event_log.read_all())[source.operation_id]
    with pytest.raises(ValueError, match='no frozen host evidence'):
        admitted_host_commands(runtime.state_dir, {**payload, 'host_execution_receipt_ids': [completed.id]},
            operation=op, events=runtime.event_log.read_all())


@pytest.mark.parametrize('evidence', ['wrong-command', 'wrong-contract', 'denied-scope', 'partial-scope'], indirect=True)
def test_inapplicable_source_receives_no_consumer_grant(evidence):
    runtime, seeded, *_ = evidence
    _, payload = prepare(runtime, seeded, 'consumer-submit')
    assert not payload.get('candidate_host_evidence_ref')
    assert not any(s['source_id'].startswith('candidate-host-') for s in payload['input_refs'])


@pytest.mark.parametrize('evidence', ['nonzero', 'bad-media-sha'], indirect=True)
def test_nonzero_or_corrupt_media_never_passes(evidence):
    _, _, service, kwargs = consumer_result(evidence)
    result = service.validate_plan_candidate(**kwargs)
    assert result['status'] == 'failed'
    assert any(e['code'] == 'verification_host_evidence_invalid' for e in result['errors'])


@pytest.mark.parametrize('evidence', ['allowed-scope'], indirect=True)
def test_derived_evidence_preserves_and_enforces_source_scope(evidence):
    from zf.runtime.artifact_read_ledger import read_attempt_artifact, ArtifactReadError
    runtime, seeded, *_ = evidence
    _, payload = prepare(runtime, seeded, 'consumer-submit')
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload['attempt_source_manifest']).payload
    for source in manifest['sources']:
        if source['source_id'].startswith('candidate-host-'):
            assert source['access_scope']['roles'] == ['consumer-submit']
    fact = next(s for s in manifest['sources'] if s['source_id'].startswith('candidate-host-fact-'))
    with pytest.raises(ArtifactReadError, match='not authorized'):
        read_attempt_artifact(runtime.state_dir, manifest=manifest, source_id=fact['source_id'],
            artifact_id=fact['artifact_id'], role='intruder', actor='intruder')
    _, _, service, kwargs = consumer_result(evidence)
    assert service.validate_plan_candidate(**kwargs)['status'] == 'passed'


@pytest.mark.parametrize('semantic_submit', [True, False])
def test_unbound_legacy_host_ids_do_not_bypass_typed_admission(tmp_path, semantic_submit):
    from zf.runtime.typed_result_admission import typed_result_admission_issues
    issues = typed_result_admission_issues(tmp_path, schema_version='verification-result.v1',
        result={'host_execution_receipt_ids': ['evt-any'], 'verdict': 'passed'}, semantic_submit=semantic_submit)
    assert issues[0]['code'] == 'verification_host_evidence_invalid'


def test_result_admission_still_requires_consuming_host_inputs(evidence):
    from zf.runtime.result_submit import ResultSubmitError
    runtime, *_ = evidence
    _, _, service, kwargs = consumer_result(evidence, read_host=False)
    with pytest.raises(ResultSubmitError):
        service.submit(**kwargs)
    assert not any(e.type == 'workflow.call.result.admitted' for e in runtime.event_log.read_all())


@pytest.mark.parametrize('field', ['target_commit', 'contract_snapshot_digest', 'target_snapshot_digest',
                                  'task_map_generation'])
def test_new_target_or_authority_does_not_receive_old_host_facts(evidence, field):
    from zf.runtime.candidate_host_consumption import prepare_candidate_host_consumption
    runtime, seeded, _, original_payload, *_ = evidence
    payload = {**original_payload, field: 'f' * (40 if field == 'target_commit' else 64)}
    if field == 'contract_snapshot_digest':
        old = hydrate_sidecar_ref(runtime.state_dir, {
            'ref': original_payload['contract_snapshot_ref'],
            'sha256': original_payload['contract_snapshot_digest']}).payload
        changed = write_sidecar_json(runtime.state_dir, 'artifacts/new-contract.json',
            {**old, 'behavior': 'a newly approved behavior'}, kind='task_contract_snapshot',
            schema_version='task-contract-snapshot.v1', created_by='kernel')
        payload.update(contract_snapshot_ref=changed['ref'], contract_snapshot_digest=changed['sha256'])
    prepare_candidate_host_consumption(runtime, payload=payload,
        operation_id='new-authority-operation', attempt_id='new-authority-attempt')
    assert not payload.get('candidate_host_evidence_ref')
