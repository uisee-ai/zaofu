"""Fresh Reader attempts regenerate kernel-derived reads, not old digests."""
from copy import deepcopy

import pytest

from tests.test_candidate_host_consumption import evidence, prepare
from zf.core.config.schema import RoleConfig
from zf.core.events import ZfEvent
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.reader_fanout_retry import prepare_fresh_reader_retry
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


@pytest.mark.parametrize('retain_stale_ancestor', [False, True])
def test_retry_required_reads_resolve_to_current_manifest(evidence, retain_stale_ancestor):
    runtime, seeded, *_ = evidence
    original, payload = prepare(runtime, seeded, 'consumer-retry')
    original_path = runtime.state_dir / load_workflow_operation(runtime.event_log, original.operation_id)['request_ref']['ref']
    original_request = original_path.read_bytes()
    custom = {**payload['required_reads'][0], 'json_path': '$.schema_version'}
    payload['required_reads'].append(custom)
    initial_reads = deepcopy(payload['required_reads'])
    child = {'child_id': 'consumer-retry', 'payload': payload}

    def prepare_child(**kwargs):
        attempt = kwargs['retry_attempt']
        prepared = prepare_call_operation(runtime, payload=kwargs['child'].payload,
            operation_type='fanout_reader_child', operation_key=f'consumer-retry-{attempt}',
            stage_id='issue-lanes-verify', task_id='FLOW-ANCHOR',
            dispatch_id=f'attempt-consumer-retry-{attempt}', causation_id='evt-retry')
        return {'prepared_call': prepared}

    runtime._prepare_reader_fanout_child_operation = prepare_child
    for attempt in (1, 2):
        if attempt == 2 and retain_stale_ancestor:
            # Existing pre-fix retries already carry obsolete ancestor digests.
            child['payload']['required_reads'].extend(initial_reads)
        prepare_fresh_reader_retry(runtime,
            manifest={'fanout_id': 'fanout-candidate', 'stage_id': 'issue-lanes-verify',
                      'trace_id': seeded['run_id']}, child=child,
            role=RoleConfig(name='consumer-retry', instance_id='consumer-retry'),
            attempt=attempt, previous_dispatch=ZfEvent(type='fanout.child.dispatched',
                payload={'run_id': child['payload']['attempt_id']}))
        current = child['payload']
        manifest = hydrate_sidecar_ref(runtime.state_dir, current['attempt_source_manifest']).payload
        assert custom in current['required_reads']
        for row in current['required_reads']:
            assert any(s['source_id'] == row['source_id'] and s['artifact_id'] == row['artifact_id']
                       and s['sha256'] == row['artifact_sha256'] for s in manifest['sources']), row
            read_attempt_artifact(runtime.state_dir, manifest=manifest,
                source_id=row['source_id'], artifact_id=row['artifact_id'],
                artifact_sha256=row['artifact_sha256'], json_path=row['json_path'])
    assert original_path.read_bytes() == original_request
    assert sum(e.type == 'runtime.action.attempt.started' for e in runtime.event_log.read_all()) == 1


@pytest.mark.parametrize('parent_run', ['same', 'foreign'])
def test_reader_retry_stops_at_own_enclosing_workflow(evidence, parent_run):
    from zf.runtime.reader_retry_reads import explicit_retry_reads

    runtime, seeded, *_ = evidence
    run = seeded['run_id']
    parent = 'wop-parent-workflow'
    runtime.event_writer.append(ZfEvent(type='workflow.operation.requested', origin='kernel',
        payload={'operation_id': parent, 'operation_type': 'workflow',
                 'workflow_run_id': run if parent_run == 'same' else 'foreign-run',
                 'task_id': 'FLOW-ANCHOR', 'request_hash': 'workflow-request'}))
    prepared, payload = prepare(runtime, seeded, 'nested-reader', parent_operation_id=parent)
    if parent_run == 'foreign':
        with pytest.raises(ValueError, match='scope changed'):
            explicit_retry_reads(runtime, payload, prepared.operation_id)
        return
    seen = explicit_retry_reads(runtime, payload, prepared.operation_id)
    assert seen == [row for row in payload['required_reads'] if row['source_id'] != 'candidate-host-execution']
