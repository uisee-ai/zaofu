"""Citations expose legal predecessor observations without inventing a verdict."""
from dataclasses import replace
import shlex

import pytest

from tests.test_task_execution_evidence import observations
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_execution_evidence import capture_execution_evidence, read_execution_evidence
from zf.runtime.task_handoff_citations import cited_execution_evidence


def write(state, body):
    return write_immutable_json_sidecar(state, body, root='test', kind='test',
                                      schema_version='test.v1', created_by='test')


@pytest.fixture
def chain(tmp_path):
    source, dispatch, red, settled = observations(tmp_path)
    unrelated = replace(red, id='uncited', payload={**red.payload, 'tool_response': 'unrelated tool output'})
    index = capture_execution_evidence(tmp_path, source, [dispatch, red, unrelated, settled])
    result = write(tmp_path, {**{k: source[k] for k in ('workflow_run_id', 'task_id', 'operation_id')},
        'self_check': {'evidence_refs': ['event:' + red.id, 'event:trace-only']}})
    body = {'schema_version': 'task-handoff.v1',
        'target': {'workflow_run_id': 'r', 'task_id': 'T7', 'operation_id': 'middle'},
        'source': {'operation_id': 'old'}, 'objective': 'check historical evidence', 'contract_revision': 'r1',
        'remaining_acceptance': [], 'workspace': {},
        'admitted_results': [{'event_id': 'admission-old', 'ref': result}],
        'work_record': None, 'unknown_effects': [], 'next_action': 'inspect', 'references': [],
        'authorizes_execution': False, 'business_completed': False, 'execution_evidence_ref': index}
    previous = write(tmp_path, body)
    current = {**body, 'target': {**body['target'], 'operation_id': 'current'},
        'source': {'operation_id': 'middle'}, 'execution_evidence_ref': None,
        'admitted_results': [], 'previous_handoff_ref': previous}
    return tmp_path, current, body, red


def test_cited_original_observation_is_explicit_and_directly_readable_across_retry(chain):
    state, current, _, red = chain
    cited = cited_execution_evidence(state, current)
    assert [row['event_id'] for row in cited['entries']] == [red.id]
    assert [row['event_id'] for row in cited['other_event_refs']] == ['trace-only']
    row = cited['entries'][0]
    assert row['source_operation'] == 'old'
    assert row['cited_by'][0]['admission_event_id'] == 'admission-old'
    args = shlex.split(row['read_command'])
    assert args[args.index('--operation') + 1] == 'current'
    assert args[args.index('--page-size') + 1] == '8192'
    manifest = {'sources': [{**write(state, current), 'source_id': 'task-handoff'}]}
    ref, fact = read_execution_evidence(state, manifest, current['target'],
        args[args.index('--source-operation') + 1], args[args.index('--event') + 1])
    assert ref['sha256'] == row['observation_ref']['sha256']
    assert fact['event_id'] == red.id and '1 failed' in fact['output']
    assert not cited['authorizes_execution']
    assert cited['claims_status'] == 'cited_historical_observations_not_current_verdict'
    assert 'output' not in row  # The required index does not copy all tool bodies.


@pytest.mark.parametrize('corrupt', ['result_identity', 'index_identity', 'output_identity', 'cross_task', 'cycle', 'digest'])
def test_cited_history_rejects_unbound_or_corrupt_facts(chain, corrupt):
    state, current, old, _ = chain
    if corrupt == 'result_identity':
        result = hydrate_sidecar_ref(state, old['admitted_results'][0]['ref']).payload
        old['admitted_results'][0]['ref'] = write(state, {**result, 'task_id': 'foreign'})
    if corrupt in {'index_identity', 'output_identity'}:
        index = hydrate_sidecar_ref(state, old['execution_evidence_ref']).payload
        if corrupt == 'index_identity':
            index['binding']['workflow_run_id'] = 'foreign'
        else:
            row = index['entries'][0]
            output = hydrate_sidecar_ref(state, row['ref']).payload
            row['ref'] = write(state, {**output, 'event_id': 'foreign'})
        old['execution_evidence_ref'] = write(state, index)
    if corrupt == 'cross_task':
        old['target']['task_id'] = 'foreign'
    if corrupt == 'cycle':
        current['source']['operation_id'] = 'old'
        old['target']['operation_id'] = 'old'
    current['previous_handoff_ref'] = write(state, old)
    if corrupt == 'digest':
        (state / current['previous_handoff_ref']['ref']).write_text('{}')
    with pytest.raises(ValueError):
        cited_execution_evidence(state, current)
