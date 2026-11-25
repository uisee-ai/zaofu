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
from zf.runtime.task_host_manifest import digest, dependency_digest
from zf.runtime.task_host_evidence import IDENTITY
from zf.runtime.workflow_operation import reduce_workflow_operations


def prepare(runtime, seeded, role, *, parent_operation_id=None):
    payload = {'workflow_run_id': seeded['run_id'], 'task_id': 'FLOW-ANCHOR',
        'fanout_id': 'fanout-candidate', 'child_id': role, 'role_instance': role,
        'stage_id': 'issue-lanes-verify', 'output_profile_id': 'candidate-verify',
        'artifact_package_mode': 'shadow', 'target_commit': seeded['candidate_head'],
        'canonical_success_event': 'test.passed', 'canonical_failure_event': 'test.failed'}
    if parent_operation_id:
        payload['parent_operation_id'] = parent_operation_id
    prepared = prepare_call_operation(runtime, payload=payload, operation_type='fanout_reader_child',
        operation_key=role, stage_id='issue-lanes-verify', task_id='FLOW-ANCHOR',
        dispatch_id='attempt-' + role, causation_id='evt-candidate-ready')
    return prepared, payload


@pytest.fixture
def evidence(tmp_path, request, monkeypatch):
    fault = getattr(request, 'param', '')
    runtime = _runtime(tmp_path)
    extra = {}
    if fault == 'scoped':
        from tests import test_product_acceptance as product
        from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, package_event_payload

        spec = product._spec()
        spec["candidate_reader_assignments"] = [
            {"role_instance": role, "acceptance": [{"task_id": "TASK-1", "acceptance_id": ac_id}],
             "journey_ids": journeys}
            for role, ac_id, journeys in (("source-reader", "AC-1", ["journey-create"]),
                                          ("consumer-reader", "AC-2", []), ("other-reader", "AC-3", []),
                                          ("refreeze-consumer", "AC-2", []))
        ]
        monkeypatch.setattr(product, "_spec", lambda **kw: spec)
        package, _ = product._package(runtime.state_dir)
        admitted = package_event_payload(hydrate_plan_artifact_package(runtime.state_dir, package),
                                         package, status="admitted")
        runtime.event_log.append(ZfEvent(type="plan.artifact_package.admitted", origin="kernel",
                                         correlation_id=product.RUN_ID, payload=admitted))
        extra = dict(run_id=product.RUN_ID, generation=product.GENERATION,
                     additional_checks=[("AC-2", "cmd-2", "pytest -q tests/test_pulse.py"),
                                        ("AC-3", "cmd-3", "pytest -q tests/test_other.py")],
                     package_fields={"plan_artifact_package_id": package["package_id"],
                                     "plan_artifact_package_ref": package["ref"],
                                     "plan_artifact_package_digest": package["sha256"]})
    seeded = _seed_frozen_candidate(runtime, **extra, producer_paths={
        'output-root': ['ZF_EVIDENCE_DIR/artifacts/probe.json'],
        'directory-output': ['artifacts/evidence/browser/'],
    }.get(fault))
    configured = policy()
    configured.commands = [seeded['command']]
    runtime.config.runtime = SimpleNamespace(host_verification=configured,
        workdirs=SimpleNamespace(required_paths=[], provision_paths=[]))
    if fault == 'declared-dependency':
        dependency = runtime.project_root / 'node_modules' / 'tool.js'
        dependency.parent.mkdir()
        dependency.write_text('original dependency')
        runtime.config.runtime.workdirs.required_paths = ['node_modules']
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
        'command': command, 'files': [], 'dependency_digest': dependency_digest(runtime, runtime.project_root)}
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


@pytest.mark.parametrize("evidence,probe_count", [("", 0), ("bad-media-sha", 1)], indirect=["evidence"])
def test_candidate_draft_keeps_only_uncovered_local_probes(evidence, probe_count):
    import json

    from zf.core.config.schema import FanoutAggregateConfig, ProjectConfig, RoleConfig, ZfConfig
    from zf.runtime.fanout import FanoutContext
    from zf.runtime.orchestrator import Orchestrator

    runtime, seeded, _, _, _, _ = evidence
    role = "consumer-submit"
    prepared, payload = prepare(runtime, seeded, role)
    orch = Orchestrator.__new__(Orchestrator)
    orch.state_dir, orch.project_root, orch.event_log = runtime.state_dir, runtime.project_root, runtime.event_log
    orch.config = ZfConfig(project=ProjectConfig(name="reader-draft"))
    payload.update(operation_id=prepared.operation_id, semantic_result_submit_mode="blocking",
                   attempt_id="attempt-" + role)
    context = FanoutContext("fanout-candidate", payload["stage_id"], "fanout_reader",
                            seeded["run_id"], "evt-candidate-ready", payload.get("target_ref", ""))
    briefing = orch._write_fanout_briefing(
        role=RoleConfig(name=role, instance_id=role, backend="mock", role_kind="reader"),
        context=context, child_id=role, run_id=payload["attempt_id"],
        aggregate=FanoutAggregateConfig(mode="wait_for_all", child_success_event="verify.child.completed",
            child_failure_event="verify.child.failed", success_event="test.passed", failure_event="test.failed"),
        child_payload=payload,
    )
    draft = json.loads((runtime.state_dir / prepared.result_scratch_ref).read_bytes())
    assert len(draft["probe_receipts"]) == probe_count
    assert draft["host_execution_receipt_ids"] == []
    assert "host_execution_receipt_ids" in briefing.read_text()


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


@pytest.mark.parametrize('evidence', ['', 'output-root'], indirect=True)
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


@pytest.mark.parametrize("independent_probe", [False, True])
def test_missing_candidate_coverage_points_back_to_bound_host_index(evidence, independent_probe):
    _, payload, service, kwargs = consumer_result(evidence)
    semantic = kwargs["semantic_result"]
    ids = semantic.pop("host_execution_receipt_ids")
    if independent_probe:
        semantic["probe_receipts"] = [{
            "probe_id": "independent-review", "status": "passed",
            "evidence_refs": ["event:independent-review"],
        }]
    original = service.validate_plan_candidate(**kwargs)
    [issue] = original["errors"]
    assert issue["field"] == "control_result"
    assert "host_execution_receipt_ids" in issue["message"]
    assert issue["recovery_owner"] == "candidate_verify"
    assert "candidate-host-execution" in issue["message"]
    assert "probe_receipts" in issue["message"]
    assert payload["candidate_host_evidence_ref"]
    semantic["host_execution_receipt_ids"] = ids
    assert service.validate_plan_candidate(**kwargs)["status"] == "passed"


@pytest.mark.parametrize('fault', ['unknown-id', 'manifest-digest', 'duplicate-id', 'missing-output', 'changed-receipt',
                                  'missing-media', 'changed-media', 'duplicate-completion', 'no-ac'])
def test_preflight_rejects_unbound_or_incomplete_host_claims(evidence, fault):
    runtime, _, _, _, receipt_ref, completed = evidence
    _, payload, service, kwargs = consumer_result(evidence)
    semantic = kwargs['semantic_result']
    receipt = hydrate_sidecar_ref(runtime.state_dir, receipt_ref).payload
    if fault == 'unknown-id':
        semantic['host_execution_receipt_ids'] = ['evt-unbound']
    elif fault == 'manifest-digest':
        semantic['host_execution_receipt_ids'] += [receipt['manifest_digest'], 'foreign-value']
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
    if fault == 'manifest-digest':
        issue, = result['errors']
        assert 'result.host_execution_receipt_ids[1]' in issue['message'] and '2 unbound' in issue['message']
        assert 'candidate-host-execution.receipts[].receipt_id' in issue['message'] and 'manifest_digest' in issue['message']
        assert completed.id not in issue['message'] and semantic['host_execution_receipt_ids'][1] == receipt['manifest_digest']
        semantic['host_execution_receipt_ids'] = [completed.id]
        assert service.validate_plan_candidate(**kwargs)['status'] == 'passed'


def test_duplicate_receipts_in_frozen_binding_are_diagnosed_separately_from_invalid_draft(evidence):
    from zf.runtime.candidate_host_consumption import admitted_host_commands

    runtime, seeded, _, _, _, completed = evidence
    prepared, payload = prepare(runtime, seeded, 'consumer-index')
    events = runtime.event_log.read_all()
    op = reduce_workflow_operations(events)[prepared.operation_id]
    envelope = hydrate_sidecar_ref(runtime.state_dir, op['request_ref']).payload
    ref = payload['candidate_host_evidence_ref']
    binding = hydrate_sidecar_ref(runtime.state_dir, ref).payload
    corrupt = write_sidecar_json(runtime.state_dir, 'artifacts/invalid-binding.json',
        {**binding, 'receipts': binding['receipts'] * 2}, kind=ref['kind'], schema_version=ref['schema_version'],
        created_by='test', access_scope=ref['access_scope'])
    bad_request = write_sidecar_json(runtime.state_dir, 'artifacts/invalid-request.json',
        {**envelope, 'request': {**envelope['request'], 'candidate_host_evidence_ref': corrupt}},
        kind='request', schema_version='request.v1', created_by='test')
    result = {**payload, 'host_execution_receipt_ids': [completed.id], 'request_hash': prepared.request_hash}
    with pytest.raises(ValueError, match='frozen candidate-host-execution binding contains duplicate') as exc:
        admitted_host_commands(runtime.state_dir, result, operation={**op, 'request_ref': bad_request}, events=events)
    assert 'result.host_execution_receipt_ids[' not in str(exc.value)


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


@pytest.mark.parametrize("evidence, scoped", [("", False), ("scoped", True)], indirect=["evidence"])
def test_source_refreeze_reuse_requires_the_same_execution_authority(evidence, scoped):
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.candidate_host_consumption import admitted_host_commands

    runtime, seeded, source, original_payload, receipt, completed = evidence
    original = next(e for e in runtime.event_log.read_all() if e.type == 'candidate.ready')
    body = {k: v for k, v in original.payload.items()
            if k not in {'target_commit', 'freeze_receipt_ref', 'freeze_receipt_digest'}}
    body.update(freeze_id='freeze-2', candidate_generation='candidate-generation-2')
    ref = write_immutable_json_sidecar(runtime.state_dir, body, root='candidate-freeze-receipts',
        kind='candidate_freeze_receipt', schema_version='candidate-freeze-receipt.v1', created_by='kernel')
    runtime.event_writer.append(ZfEvent(type='candidate.ready', origin='kernel',
        correlation_id=seeded['run_id'], payload={**body, 'target_commit': seeded['candidate_head'],
            'freeze_receipt_ref': ref, 'freeze_receipt_digest': ref['sha256']}))
    prepared, payload = prepare(runtime, seeded, 'refreeze-consumer')
    assert payload['contract_snapshot_digest'] != original_payload['contract_snapshot_digest']
    if scoped:
        assert not payload.get('candidate_host_evidence_ref')
        return
    binding = hydrate_sidecar_ref(runtime.state_dir, payload['candidate_host_evidence_ref']).payload
    assert binding['receipts'][0]['source_operation_id'] == source.operation_id
    assert binding['receipts'][0]['receipt_ref'] == receipt
    op = reduce_workflow_operations(runtime.event_log.read_all())[prepared.operation_id]
    result = {**payload, 'host_execution_receipt_ids': [completed.id],
              'request_hash': prepared.request_hash, 'verdict': 'passed'}
    assert admitted_host_commands(runtime.state_dir, result, operation=op, events=runtime.event_log.read_all())
    assert len([e for e in runtime.event_log.read_all() if e.type == 'runtime.action.attempt.started']) == 1


def test_changed_execution_environment_does_not_receive_old_fact(evidence):
    runtime, seeded, *_ = evidence
    runtime.config.runtime.host_verification.image = 'different-runtime:version'
    _, payload = prepare(runtime, seeded, 'environment-consumer')
    assert not payload.get('candidate_host_evidence_ref')


@pytest.mark.parametrize('evidence', ['declared-dependency'], indirect=True)
def test_unchanged_commit_with_changed_dependency_bytes_cannot_reuse(evidence, monkeypatch):
    runtime, seeded, *_ = evidence
    from zf.core.config.schema import RoleConfig
    runtime.config.roles = [RoleConfig(name='verify', instance_id=name, role_kind='reader')
        for name in ('consumer-dependency-before', 'consumer-dependency-after')]
    plan = SimpleNamespace(enabled=True, mode='worktree', role_kind='reader', project_path=runtime.project_root)
    monkeypatch.setattr('zf.runtime.workdirs.WorkdirManager', lambda **kw: SimpleNamespace(plan=lambda role: plan))
    monkeypatch.setattr('zf.runtime.task_host_manifest.git', lambda *args: seeded['candidate_head'])
    _, before = prepare(runtime, seeded, 'consumer-dependency-before')
    assert before.get('candidate_host_evidence_ref')
    (runtime.project_root / 'node_modules' / 'tool.js').write_text('changed dependency without commit')
    _, after = prepare(runtime, seeded, 'consumer-dependency-after')
    assert not after.get('candidate_host_evidence_ref')

@pytest.mark.parametrize("evidence", ["scoped"], indirect=True)
def test_scoped_readers_share_only_identical_command_execution_facts(evidence):
    from zf.runtime.candidate_host_consumption import admitted_host_commands

    runtime, seeded, source, source_payload, receipt, completed = evidence
    prepared, consumer = prepare(runtime, seeded, "consumer-reader")
    assert source_payload["contract_snapshot_digest"] != consumer["contract_snapshot_digest"]
    assert source_payload["candidate_reader_scope"]["acceptance_ids"] != consumer["candidate_reader_scope"]["acceptance_ids"]
    binding = hydrate_sidecar_ref(runtime.state_dir, consumer["candidate_host_evidence_ref"]).payload
    assert [(row["source_operation_id"], row["receipt_ref"]) for row in binding["receipts"]] == [
        (source.operation_id, receipt)]
    assert not binding["authorizes_execution"]
    events = runtime.event_log.read_all()
    op = reduce_workflow_operations(events)[prepared.operation_id]
    result = {**consumer, "host_execution_receipt_ids": [completed.id], "request_hash": prepared.request_hash,
              "verdict": "passed"}
    assert admitted_host_commands(runtime.state_dir, result, operation=op, events=events)
    _, other = prepare(runtime, seeded, "other-reader")
    assert not other.get("candidate_host_evidence_ref")
