"""The actual result service cannot resolve retained duties with invented refs."""
import pytest

from tests.test_candidate_host_consumption import evidence, consumer_result
from zf.core.events import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize('fault', ['', 'original_contract', 'approved_owner', 'wrong_generation',
    'missing_file', 'foreign_ref', 'owner_recommendation', 'wrong_host_start', 'missing_row'])
def test_obligation_admission_binds_real_consumer_evidence(evidence, monkeypatch, fault):
    runtime, _, source, _, receipt, completed = evidence
    finding = {'finding_id': 'finding-original', 'operation_id': source.operation_id,
        'disposition': {'classification': 'execution_unknown'}, 'status': 'pending'}
    if fault in {'owner_recommendation', 'original_contract', 'approved_owner', 'wrong_generation'}:
        finding['disposition']['classification'] = 'contract_question'
    if fault == 'wrong_host_start': finding['original_start_event_id'] = 'different-host-start'
    if fault in {'approved_owner', 'wrong_generation'}:
        source_ref = {'ref': 'artifacts/question.json', 'sha256': 'a' * 64}
        finding['control_result_ref'] = source_ref
        runtime.event_writer.append(ZfEvent(type='workflow.call.result.admitted', origin='kernel',
            payload={'control_result_ref': source_ref}))
    monkeypatch.setattr('zf.runtime.candidate_finding_partition.retained_candidate_obligations',
                        lambda *a, **kw: [finding])
    _, payload, service, kwargs = consumer_result(evidence)
    # The obligations changed the contract; historical host fact reuse correctly
    # refuses this fixture. Supply the ordinary independent probe required by
    # its current contract, leaving the new obligation gate as the variable.
    from zf.runtime.task_pipeline_briefing import verification_result_template
    contract = hydrate_sidecar_ref(runtime.state_dir, {'ref': payload['contract_snapshot_ref'],
        'sha256': payload['contract_snapshot_digest']}).payload
    semantic = kwargs['semantic_result']
    semantic['host_execution_receipt_ids'] = []
    semantic['probe_receipts'] = verification_result_template(contract)['probe_receipts']
    for probe in semantic['probe_receipts']:
        probe['target_commit'] = payload['target_commit']
        probe['evidence_refs'] = ['event:independent-acceptance-report']
    ref = {'ref': payload['target_snapshot_ref'], 'sha256': payload['target_snapshot_digest']}
    row = {'finding_id': finding['finding_id'], 'status': 'resolved',
           'rationale': 'Current independent evaluation and bound evidence address the retained finding.',
           'evidence_refs': [ref], 'resolution_event_id': completed.id}
    semantic['recovery_obligation_results'] = [row]
    if fault == 'original_contract':
        row.pop('resolution_event_id')
        row['rationale'] = 'The current approved requirement already answers this question; no scope change.'
    if fault in {'approved_owner', 'wrong_generation'}:
        from zf.runtime.plan_approval_state import decide_pending_plan
        trigger = runtime.event_writer.append(ZfEvent(type='task_map.ready', origin='kernel'))
        runtime.event_writer.append(ZfEvent(type='plan.approval.requested', origin='kernel',
            payload={'plan_id': trigger.id, 'workflow_run_id': payload['workflow_run_id']}))
        decision, error = decide_pending_plan(runtime.event_writer, state_dir=runtime.state_dir,
            plan_id=trigger.id, event_type='plan.approved', payload={})
        assert decision and not error
        assert 'task_map_generation' not in decision.payload
        runtime.event_writer.append(ZfEvent(type='task.pipeline.generation.admitted', origin='kernel',
            payload={'workflow_run_id': payload['workflow_run_id'], 'trigger_event_id': trigger.id,
                     'task_map_generation': 'wrong' if fault == 'wrong_generation' else payload['task_map_generation']}))
        row['resolution_event_id'] = decision.id
    if fault == 'missing_row': semantic.pop('recovery_obligation_results')
    if fault == 'missing_file': (runtime.state_dir / ref['ref']).unlink()
    if fault == 'foreign_ref': row['evidence_refs'] = [receipt]
    if fault == 'owner_recommendation':
        decision = runtime.event_writer.append(ZfEvent(type='run.manager.agent.recommendation', origin='kernel',
            payload={'workflow_run_id': payload['workflow_run_id'], 'task_map_generation': payload['task_map_generation']}))
        row['resolution_event_id'] = decision.id
    validation = service.validate_plan_candidate(**kwargs)
    success = fault in {'', 'original_contract', 'approved_owner'}
    assert (validation['status'] == 'passed') is success, validation
    if fault == 'missing_row':
        issue, = validation['errors']
        assert 'candidate_recovery_obligations[].finding_id' in issue['message']
        assert 'result.recovery_obligation_results[]' in issue['message']
        assert all(k in issue['message'] for k in ('status', 'rationale', 'evidence_refs', 'ref', 'sha256'))
        assert 'recovery_obligation_results' not in semantic
    if success: assert service.submit(**kwargs).admitted_event_id


@pytest.mark.parametrize('evidence', ['', 'nonzero'], indirect=True)
@pytest.mark.parametrize('fault', ['', 'cross_generation', 'different_command', 'different_command_id', 'different_tier'])
def test_unknown_observation_resolution_preserves_product_verdict(evidence, monkeypatch, fault):
    from zf.runtime.candidate_obligation_evidence import validate_obligation_evidence
    from zf.runtime.sidecar_refs import write_sidecar_json
    runtime, _, _, _, receipt_ref, completed = evidence
    receipt = hydrate_sidecar_ref(runtime.state_dir, receipt_ref).payload
    finding = {'finding_id': 'host-unknown', 'original_start_event_id': completed.causation_id,
               'disposition': {'classification': 'execution_unknown'}}
    if fault:
        finding['original_start_event_id'] = 'prior-generation-start'
        original = dict(receipt['manifest'])
        if fault.startswith('different_'):
            field = fault.removeprefix('different_')
            original['command'] = {**original['command'], field: 'other-' + field}
        finding['original_manifest_ref'] = write_sidecar_json(runtime.state_dir,
            'artifacts/old-manifest.json', original, kind='task_host_manifest',
            schema_version='task-host-manifest.v1', created_by='kernel')
    monkeypatch.setattr('zf.runtime.candidate_finding_partition.retained_candidate_obligations',
                        lambda *a, **kw: [finding])
    _, payload, _, kwargs = consumer_result(evidence)
    contract = hydrate_sidecar_ref(runtime.state_dir, {'ref': payload['contract_snapshot_ref'],
        'sha256': payload['contract_snapshot_digest']}).payload
    result = {**payload, **kwargs['semantic_result'], 'attempt_id': 'attempt-consumer-submit',
        'verdict': 'passed', 'role_instance': 'consumer-submit', 'recovery_obligation_results': [{
            'finding_id': finding['finding_id'], 'status': 'resolved', 'rationale': 'A known observation is available.',
            'resolution_event_id': completed.id, 'evidence_refs': [{
                'ref': payload['target_snapshot_ref'], 'sha256': payload['target_snapshot_digest']}]}]}
    if fault.startswith('different_'):
        with pytest.raises(ValueError, match='exact original'):
            validate_obligation_evidence(runtime.state_dir, result, contract, runtime.event_log.read_all())
    else:
        validate_obligation_evidence(runtime.state_dir, result, contract, runtime.event_log.read_all())
    # This gate resolves missing execution knowledge only; the ordinary command
    # acceptance gate must still reject nonzero product observations.
    assert hydrate_sidecar_ref(runtime.state_dir, receipt_ref).payload['exit_code'] == receipt['exit_code']
