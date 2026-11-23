"""Explicit repeat-safety authorizes one new observation, never rewrites unknown."""
from dataclasses import asdict
import subprocess

import pytest

from tests.test_host_verification import prepare, policy
from tests.test_task_host_actions import case
from zf.core.config.host_verification import parse_host_verification
from zf.runtime.host_probe_diagnosis import pending_host_probe_diagnoses
from zf.runtime.host_verification import reconcile_host_verification
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def unknown_probe(case, monkeypatch, *, authorized=False):
    service, _ = prepare(case)
    service.writer.default_origin = 'kernel'
    service.config.runtime.host_verification.max_executions_per_run = 4
    if authorized:
        service.config.runtime.host_verification.recollect_commands = ['python3 probe.py']
    def unknown(*args, **kwargs):
        raise ValueError('collector output unavailable')
    monkeypatch.setattr('zf.runtime.host_verification._run_container', unknown)
    assert reconcile_host_verification(service) == 1
    events = service.writer.event_log.read_all()
    failed = next(e for e in events if e.type == 'runtime.action.attempt.failed')
    return service, failed


@pytest.mark.parametrize('second_unknown', [False, True])
def test_recollection_runs_once_through_runtime_and_preserves_original(case, monkeypatch, second_unknown):
    service, failed = unknown_probe(case, monkeypatch)
    p = service.config.runtime.host_verification
    p.recollect_commands = list(p.commands)
    monkeypatch.setattr('zf.runtime.host_probe_recollection._container_absent', lambda name: True)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if second_unknown:
            raise ValueError('independent collector still unavailable')
        return subprocess.CompletedProcess(argv, 0, 'new observation passed', '')
    monkeypatch.setattr('zf.runtime.host_verification._run_container', run)
    reconcile_host_verification(service)
    for _ in range(3):
        reconcile_host_verification(service)
    assert len(calls) == 1
    events = service.writer.event_log.read_all()
    assert next(e for e in events if e.id == failed.id) == failed
    starts = [e for e in events if e.type == 'runtime.action.attempt.started' and e.payload.get('kind') == 'verify']
    assert len(starts) == 2
    assert starts[1].payload['recollection_of_start_id'] == starts[0].id
    assert starts[1].payload['manifest_digest'] != starts[0].payload['manifest_digest']
    completed = [e for e in events if e.type == 'runtime.action.attempt.completed' and e.payload.get('kind') == 'verify']
    assert len(completed) == (0 if second_unknown else 1)
    if completed:
        receipt = hydrate_sidecar_ref(service.state_dir, completed[0].payload['receipt_ref']).payload
        assert receipt['recollection_of_start_id'] == failed.causation_id
        assert receipt['product_verdict'] == 'not_evaluated'
        assert not any(a['source_event_id'] == failed.id for a in
            pending_host_probe_diagnoses(service.state_dir, events, service.config))
    assert not any(e.type in {'test.passed', 'task.done', 'run.goal.completed'} for e in events)


@pytest.mark.parametrize('gate', ['unapproved', 'container_present', 'budget', 'paused'])
def test_recollection_does_not_bypass_authority_or_live_effect(case, monkeypatch, gate):
    service, _ = unknown_probe(case, monkeypatch)
    p = service.config.runtime.host_verification
    if gate != 'unapproved': p.recollect_commands = list(p.commands)
    if gate == 'budget': p.max_executions_per_run = 1
    if gate == 'paused': service.writer.emit('dispatch.paused', actor='operator')
    monkeypatch.setattr('zf.runtime.host_probe_recollection._container_absent', lambda name: gate != 'container_present')
    monkeypatch.setattr('zf.runtime.host_verification._run_container', lambda *a, **kw: pytest.fail('must not run'))
    assert reconcile_host_verification(service) == 0


@pytest.mark.parametrize('commands', ['python3 probe.py', ['unapproved'], ['python3 probe.py'] * 2, [None]])
def test_recollection_policy_requires_exact_unique_subset(commands):
    with pytest.raises(ValueError, match='recollect_commands'):
        parse_host_verification({**asdict(policy()), 'recollect_commands': commands})


def test_preapproved_unknown_keeps_rm_owner_without_premature_human_gate(case, monkeypatch):
    service, failed = unknown_probe(case, monkeypatch, authorized=True)
    assert not any(e.type == 'human.escalate' for e in service.writer.event_log.read_all())
    monkeypatch.setattr('zf.runtime.host_probe_recollection._container_absent', lambda name: True)
    monkeypatch.setattr('zf.runtime.host_verification._run_container', lambda argv, **kw:
        subprocess.CompletedProcess(argv, 0, 'new observation', ''))
    assert reconcile_host_verification(service) == 1
    events = service.writer.event_log.read_all()
    assert not any(e.type == 'human.escalate' for e in events)
    assert any(e.type == 'runtime.action.attempt.completed'
               and e.payload.get('recollection_of_start_id') == failed.causation_id for e in events)


def test_prior_completed_evidence_survives_capture_allowance_increase(tmp_path):
    from zf.runtime.host_verification_policy import policy_receipt_matches
    from zf.runtime.task_host_manifest import digest
    previous = asdict(policy())
    previous.pop('recollect_commands')  # Receipt from the deployed prior parser.
    receipt = {'policy_snapshot': previous, 'policy_digest': digest(previous)}
    current = policy()
    current.recollect_commands = list(current.commands)
    current.max_artifact_bytes = 64 * 1024 * 1024
    current.max_total_evidence_bytes = 128 * 1024 * 1024
    assert policy_receipt_matches(tmp_path, current, receipt)
    current.network = 'host'
    assert not policy_receipt_matches(tmp_path, current, receipt)


@pytest.mark.parametrize('error', [FileNotFoundError('docker'), subprocess.TimeoutExpired('docker', 15)])
def test_container_inventory_unavailable_cannot_authorize_recollection(monkeypatch, error):
    from zf.runtime.host_probe_recollection import _container_absent
    def unavailable(*args, **kwargs):
        raise error
    monkeypatch.setattr('zf.runtime.host_probe_recollection.subprocess.run', unavailable)
    assert not _container_absent('zf-probe-unavailable')
