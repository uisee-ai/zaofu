"""Host observations cannot evade their admission path as ordinary probes."""
import pytest

from tests.test_candidate_host_consumption import evidence, consumer_result
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.result_submit import ResultSubmitError


def probe_for(evidence, payload, ref, *, status='passed'):
    runtime = evidence[0]
    contract = hydrate_sidecar_ref(runtime.state_dir, {
        'ref': payload['contract_snapshot_ref'],
        'sha256': payload['contract_snapshot_digest'],
    }).payload
    command = contract['verification_commands'][0]
    return {'probe_id': 'reported-command', **{k: command[k] for k in (
        'command_id', 'command', 'command_digest')}, 'target_commit': payload['target_commit'],
        'status': status, 'exit_code': 0, 'evidence_refs': [ref]}


@pytest.mark.parametrize('evidence', ['', 'directory-output'], indirect=True)
@pytest.mark.parametrize('kind', ['bundle', 'fact', 'receipt', 'output', 'event', 'start-event',
                                 'source-id', 'execution-id', 'media', 'media-file',
                                 'absolute', 'fragment', 'file-uri', 'dot-path'])
def test_actual_preflight_and_submit_reject_host_facts_relabelled_as_probes(evidence, kind):
    runtime, _, _, _, original_ref, completed = evidence
    _, payload, service, kwargs = consumer_result(evidence)
    host_ref = payload['candidate_host_evidence_ref']
    binding = hydrate_sidecar_ref(runtime.state_dir, host_ref).payload
    receipt = hydrate_sidecar_ref(runtime.state_dir, original_ref).payload
    media = binding['receipts'][0]['media_refs'][0]
    media_file = hydrate_sidecar_ref(runtime.state_dir, media).payload['files'][0]
    refs = {
        'bundle': host_ref['ref'], 'fact': binding['receipts'][0]['fact_ref']['ref'],
        'receipt': original_ref['ref'], 'output': receipt['output_ref']['ref'],
        'event': 'event:' + completed.id,
        'start-event': 'event:' + original_ref['source_event_id'],
        'source-id': 'candidate-host-fact-' + completed.id,
        'execution-id': 'candidate-host-execution',
        'media': media['ref'], 'media-file': media_file['ref'],
        'absolute': str(runtime.state_dir / host_ref['ref']),
        'fragment': host_ref['ref'] + '#/receipts/0',
        'file-uri': (runtime.state_dir / host_ref['ref']).as_uri(),
        'dot-path': './' + host_ref['ref'],
    }
    result = kwargs['semantic_result']
    result.pop('host_execution_receipt_ids')
    result['probe_receipts'] = [probe_for(evidence, payload, refs[kind])]
    before = runtime.event_log.read_all()
    validation = service.validate_plan_candidate(**kwargs)
    assert validation['status'] == 'failed', validation
    assert any(e['code'] == 'verification_host_evidence_invalid' for e in validation['errors'])
    assert 'host_execution_receipt_ids' in str(validation['errors'])
    assert runtime.event_log.read_all() == before
    with pytest.raises(ResultSubmitError):
        service.submit(**kwargs)
    assert not any(e.type == 'workflow.call.result.admitted' for e in runtime.event_log.read_all())


def test_independent_probe_with_its_own_reference_is_still_admitted(evidence):
    _, payload, service, kwargs = consumer_result(evidence)
    kwargs['semantic_result'].pop('host_execution_receipt_ids')
    kwargs['semantic_result']['probe_receipts'] = [probe_for(
        evidence, payload, 'event:independent-current-reader-probe')]
    assert service.validate_plan_candidate(**kwargs)['status'] == 'passed'
    assert service.submit(**kwargs).admitted_event_id


@pytest.mark.parametrize('evidence', ['directory-output'], indirect=True)
def test_blocked_diagnostic_can_reference_unavailable_host_input(evidence):
    _, payload, service, kwargs = consumer_result(evidence)
    kwargs['semantic_result'].update(verdict='blocked', host_execution_receipt_ids=[],
        probe_receipts=[probe_for(evidence, payload,
            payload['candidate_host_evidence_ref']['ref'], status='blocked')])
    row = kwargs['semantic_result']['requirement_results'][0]
    row.update(status='blocked', findings=[{'message': 'Declared media is unavailable'}])
    kwargs['semantic_result']['rework_items'] = [{
        'rework_item_id': 'media-contract', 'acceptance_id': row['acceptance_id'],
        'status': 'blocked', 'expected': 'Bound required media',
        'observed': 'Unavailable producer path', 'required_delta': 'Resolve the producer contract',
        'done_when': 'Independent Reader can validate required media',
        'next_gate': 'candidate_verify', 'owner': 'contract_owner',
    }]
    validation = service.validate_plan_candidate(**kwargs)
    assert validation['status'] == 'passed', validation
    assert service.submit(**kwargs).admitted_event_id
