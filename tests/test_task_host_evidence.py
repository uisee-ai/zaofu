"""A successor can inspect bound host diagnostics produced after settlement."""
from dataclasses import replace

import pytest

from tests.test_task_execution_evidence import observations
from zf.core.events import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json, write_sidecar_text
from zf.runtime.task_execution_evidence import capture_execution_evidence
from zf.runtime.task_host_manifest import digest


def host_probe(state, source, output_text='assertion RED: TODO\nAPI_KEY=private-value', *, task_map_generation='map-1'):
    source.update(lease_id='lease-old', dispatch_id='dispatch-old', request_hash='request-old',
                  task_map_generation=task_map_generation, operation_generation=1)
    keys = ('workflow_run_id', 'task_id', 'operation_id', 'active_attempt_id',
            'lease_id', 'dispatch_id', 'request_hash', 'task_map_generation', 'operation_generation')
    identity = {k: source[k] for k in keys}
    command = {'command_id': 'test-count', 'command': 'npm test', 'command_digest': 'command-digest'}
    manifest = {'schema_version': 'task-host-manifest.v1', 'kind': 'verify',
                'operation_id': source['operation_id'],
                'identity': {k: v for k, v in identity.items() if k != 'operation_id'},
                'head': 'published-test-only-commit', 'command': command}
    manifest['manifest_digest'] = digest(manifest)
    fields = {**identity, 'action': 'task-host-assist', 'kind': 'verify',
              'manifest_digest': manifest['manifest_digest'], 'approval_ref': 'owner:probe'}
    start = ZfEvent(type='runtime.action.attempt.started', origin='kernel', actor='run-manager',
                    task_id=source['task_id'], correlation_id=source['workflow_run_id'], payload=fields)
    output = write_sidecar_text(state, 'artifacts/host-output.txt', output_text, kind='host_probe_output',
        schema_version='host-probe-output.v1', created_by='run-manager', source_event_id=start.id)
    receipt = {**fields, **command, 'schema_version': 'task-host-receipt.v1', 'manifest': manifest,
               'output_ref': output, 'status': 'failed', 'exit_code': 1, 'source_stable': True,
               'target_commit': manifest['head'], 'execution_origin': 'preauthorized_container',
               'product_verdict': 'not_evaluated'}
    ref = write_sidecar_json(state, 'artifacts/host-receipt.json', receipt,
        kind='task_host_receipt', schema_version='task-host-receipt.v1', created_by='run-manager',
        source_event_id=start.id)
    completed = ZfEvent(type='runtime.action.attempt.completed', origin='kernel', actor='run-manager',
        task_id=source['task_id'], correlation_id=source['workflow_run_id'], causation_id=start.id,
        payload={**fields, 'status': 'failed', 'receipt_ref': ref})
    return start, completed, receipt


def test_post_settlement_host_failure_reaches_existing_evidence_index(tmp_path):
    source, dispatch, hook, settled = observations(tmp_path)
    start, completed, receipt = host_probe(tmp_path, source)
    events = [dispatch, hook, settled, start, completed, replace(hook, id='late-native')]
    ref = capture_execution_evidence(tmp_path, source, events)
    index = hydrate_sidecar_ref(tmp_path, ref).payload
    assert [r['event_id'] for r in index['entries']] == [hook.id, completed.id]
    body = hydrate_sidecar_ref(tmp_path, index['entries'][1]['ref']).payload
    assert body['output'].startswith('assertion RED: TODO') and 'private-value' not in body['output']
    assert body['exit_code'] == 1 and body['target_commit'] == receipt['target_commit']
    assert body['claims_status'] == 'host_execution_fact_not_product_verdict'
    assert body['authorizes_execution'] is False
    assert body['receipt_ref'] == completed.payload['receipt_ref']


@pytest.mark.parametrize('change', ['origin', 'start_origin', 'task', 'attempt', 'request',
    'missing_start', 'duplicate_completion', 'before_settlement', 'receipt_digest', 'manifest_digest'])
def test_unbound_host_effect_is_excluded(tmp_path, change):
    source, dispatch, hook, settled = observations(tmp_path)
    start, completed, receipt = host_probe(tmp_path, source)
    if change == 'origin': completed = replace(completed, origin='worker')
    if change == 'start_origin': start = replace(start, origin='worker')
    if change == 'task': completed = replace(completed, task_id='other')
    if change == 'attempt': source['active_attempt_id'] = 'new-attempt'
    if change == 'request': source['request_hash'] = 'new-request'
    if change == 'receipt_digest': (tmp_path / completed.payload['receipt_ref']['ref']).write_text('{}')
    if change == 'manifest_digest':
        receipt['manifest']['head'] = 'different'
        ref = write_sidecar_json(tmp_path, 'artifacts/changed.json', receipt,
            kind='task_host_receipt', schema_version='task-host-receipt.v1', created_by='run-manager',
            source_event_id=start.id)
        completed = replace(completed, payload={**completed.payload, 'receipt_ref': ref})
    events = [dispatch, hook, settled, start, completed]
    if change == 'missing_start': events.remove(start)
    if change == 'duplicate_completion': events.append(replace(completed, id='duplicate'))
    if change == 'before_settlement': events = [dispatch, hook, start, completed, settled]
    index = hydrate_sidecar_ref(tmp_path, capture_execution_evidence(tmp_path, source, events)).payload
    assert completed.id not in [r['event_id'] for r in index['entries']]


def test_corrupt_bound_host_output_is_explicitly_unavailable(tmp_path):
    source, dispatch, hook, settled = observations(tmp_path)
    start, completed, receipt = host_probe(tmp_path, source)
    (tmp_path / receipt['output_ref']['ref']).write_text('changed')
    index = hydrate_sidecar_ref(tmp_path, capture_execution_evidence(
        tmp_path, source, [dispatch, hook, settled, start, completed])).payload
    entry = next(r for r in index['entries'] if r['event_id'] == completed.id)
    body = hydrate_sidecar_ref(tmp_path, entry['ref']).payload
    assert body['status'] == 'unavailable' and body['output'] is None
    assert body['authorizes_execution'] is False
