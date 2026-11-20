"""Diagnosis must expose deployed source receipts, not infer them from its checkout."""
import pytest

from tests.test_run_manager import _state
from zf.runtime.run_manager import _emit_autoresearch_request
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json


def loaded(state, writer, name, *, owner=None):
    ref = write_sidecar_json(state, f'diagnostics/runtime-load/{name}.json', {
        'schema_version': 'runtime-load.v1', 'state_dir': str(owner or state),
        'source_root': f'/frozen/{name}', 'source_revision': name,
        'pid': 123, 'loaded_consumers': {},
    }, kind='runtime-load', schema_version='runtime-load.v1', created_by='zf-cli')
    return writer.emit('loop.started', actor='zf-cli', payload={'runtime_load_ref': ref})


@pytest.mark.parametrize('case', ['valid', 'tampered', 'other_state', 'missing'])
def test_diagnostic_intake_pins_failure_time_and_current_source(tmp_path, case):
    state, log, writer = _state(tmp_path)
    old = loaded(state, writer, 'old')
    failed = writer.emit('task.attempt.failed', task_id='T1', payload={'workflow_run_id': 'R1'})
    if case == 'missing':
        new = writer.emit('loop.started', payload={})
    else:
        new = loaded(state, writer, 'new', owner=tmp_path / 'other' if case == 'other_state' else None)
        if case == 'tampered':
            (state / new.payload['runtime_load_ref']['ref']).write_text('{}')
    assert _emit_autoresearch_request(state_dir=state, writer=writer,
        action={'action': 'diagnose-attention', 'checkpoint_id': 'failure',
                'task_id': 'T1', 'source_event_ids': [failed.id]},
        projection={}, project_root=tmp_path, causation_id=failed.id)
    request = next(e for e in log.read_all() if e.type == 'run.manager.autoresearch.requested')
    body = hydrate_sidecar_ref(state, request.payload['context_ref']).payload
    evidence = body['runtime_sources']
    at_failure = evidence['at_source_events'][failed.id]
    assert at_failure['event_id'] == old.id
    assert at_failure['source_revision'] == 'old'
    current = evidence['latest_startup']
    assert current['event_id'] == new.id
    if case == 'valid':
        assert current['source_revision'] == 'new'
        assert current['source_root'] == '/frozen/new'
        assert current['receipt_ref'] == new.payload['runtime_load_ref']
    else:
        assert current['status'] == 'unavailable'
        assert not current.get('source_root')
