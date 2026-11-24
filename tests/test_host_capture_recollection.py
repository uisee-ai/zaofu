"""A raised capture allowance grants at most one separately linked Candidate observation."""
import base64
from dataclasses import replace
from pathlib import Path
import subprocess

import pytest

from tests.test_candidate_host_binding import candidate
from tests.test_host_verification import policy
from tests.test_task_host_actions import case
from zf.runtime.host_verification import reconcile_host_verification
from zf.runtime.host_verification_handoff import completed_reader_probes
from zf.runtime.host_verification_retained import reader_probe_inputs_improved
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_manifest import binding


@pytest.mark.parametrize('gate', [
    'allowed', 'total', 'no_authority', 'allowance_short', 'not_zero',
    'unknown_error', 'paused', 'budget', 'changed_target', 'observe_only', 'second_overflow',
])
def test_capture_failure_reconciles_only_after_exact_new_authority(candidate, monkeypatch, gate):
    service, _, _, freeze = candidate
    service.writer.default_origin = 'kernel'
    service.config.runtime.host_verification = p = policy(max_artifact_bytes=8, max_total_evidence_bytes=10)
    monkeypatch.setattr('zf.runtime.host_probe_recollection._container_absent', lambda _: True)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        mount = next(v for v in argv if 'dst=/evidence' in v)
        output = Path(mount.split('src=')[1].split(',')[0])
        if gate == 'unknown_error':
            (output / 'trace.zip').symlink_to('absent')
        elif gate == 'total':
            (output / 'first.zip').write_bytes(b'12345')
            (output / 'trace.zip').write_bytes(b'abcdefg')
        else:
            (output / 'trace.zip').write_bytes(b'z' * (10 if gate == 'second_overflow' and len(calls) == 2 else 9))
        return subprocess.CompletedProcess(argv, 1 if gate == 'not_zero' else 0, 'assertions ran', '')
    monkeypatch.setattr('zf.runtime.host_verification._run_container', run)
    assert reconcile_host_verification(service) == 1
    events = service.writer.event_log.read_all()
    original = next(e for e in events if e.type == 'runtime.action.attempt.completed'
                    and e.payload.get('operation_id') == 'candidate-1')
    failed = hydrate_sidecar_ref(service.state_dir, original.payload['receipt_ref']).payload
    assert failed['evidence_status'] == 'collection_failed' and failed['evidence_ref'] is None
    if gate != 'no_authority':
        p.recollect_commands = list(p.commands)
    if gate == 'total':
        p.max_total_evidence_bytes = 12
    elif gate != 'allowance_short':
        p.max_artifact_bytes = 9
    if gate == 'paused':
        service.writer.emit('dispatch.paused', actor='operator')
    if gate == 'budget':
        p.max_executions_per_run = 1
    if gate == 'changed_target':
        service.writer.emit('candidate.ready', payload={**freeze, 'candidate_head_commit': 'b' * 40})
    for _ in range(3):
        reconcile_host_verification(service, allow_execution=gate != 'observe_only')
    if gate not in {'allowed', 'total', 'second_overflow'}:
        assert len(calls) == 1
        return
    assert len(calls) == 2
    events = service.writer.event_log.read_all()
    successor = next(e for e in events if e.type == 'runtime.action.attempt.completed'
                     and e.payload.get('recollection_of_receipt_id') == original.id)
    assert successor.payload['recollection_of_start_id'] == original.causation_id
    assert hydrate_sidecar_ref(service.state_dir, original.payload['receipt_ref']).payload == failed
    op, _, contract, _ = binding(service, 'candidate-1')
    required, receipts, has_failed = completed_reader_probes(service, op, contract, p, events)
    assert required == receipts.keys() == {'contract-verification'}
    assert has_failed is (gate == 'second_overflow')
    assert receipts['contract-verification'] == successor.payload['receipt_ref']
    assert not any(e.type in {'test.passed', 'task.done', 'run.goal.completed'} for e in events)
    if gate != 'second_overflow':
        receipt = hydrate_sidecar_ref(service.state_dir, successor.payload['receipt_ref']).payload
        files = hydrate_sidecar_ref(service.state_dir, receipt['evidence_ref']).payload['files']
        trace = next(item for item in files if item['path'] == 'trace.zip')
        assert base64.b64decode(trace['base64']) == (b'abcdefg' if gate == 'total' else b'z' * 9)
        assert reader_probe_inputs_improved(service, op, contract, p, receipts)
        unbound = replace(successor, payload={**successor.payload, 'recollection_of_receipt_id': 'other-receipt'})
        history = [unbound if event.id == successor.id else event for event in events]
        assert completed_reader_probes(service, op, contract, p, history)[2] is True
