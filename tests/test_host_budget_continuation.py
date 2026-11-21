"""An owner budget increase preserves facts and resumes only the budget wait."""
from dataclasses import asdict, replace

import pytest

from tests.test_host_verification import policy, prepare
from tests.test_task_host_actions import case
from zf.core.config.host_verification import parse_host_verification
from zf.core.events import ZfEvent
from zf.runtime.host_verification import reconcile_host_verification
from zf.runtime.host_verification_policy import policy_receipt_matches
from zf.runtime.task_host_manifest import digest, preview


def test_config_accepts_explicit_120_execution_budget():
    assert parse_host_verification({**asdict(policy()), 'max_executions_per_run': 120}).max_executions_per_run == 120


@pytest.mark.parametrize('limit', [True, -1, 1.5, '120'])
def test_budget_remains_an_explicit_nonnegative_integer(limit):
    with pytest.raises(ValueError):
        parse_host_verification({**asdict(policy()), 'max_executions_per_run': limit})


@pytest.mark.parametrize('snapshot', [False, True])
def test_budget_increase_preserves_matching_execution_receipt(tmp_path, snapshot):
    old = replace(policy(), max_executions_per_run=100)
    receipt = {'policy_digest': digest(asdict(old))}
    if snapshot:
        receipt['policy_snapshot'] = asdict(old)
    assert policy_receipt_matches(tmp_path, replace(old, max_executions_per_run=120), receipt)


@pytest.mark.parametrize('snapshot', [False, True])
@pytest.mark.parametrize('field,value', [
    ('image', 'sha256:' + 'b' * 64), ('network', 'host'),
    ('commands', ['different command']), ('approval_ref', 'other-owner'),
    ('timeout_seconds', 60), ('protected_files', {'probe.py': 'a' * 64}),
])
def test_budget_increase_cannot_disguise_execution_policy_change(tmp_path, field, value, snapshot):
    old = replace(policy(), max_executions_per_run=100)
    receipt = {'policy_digest': digest(asdict(old))}
    if snapshot:
        receipt['policy_snapshot'] = asdict(old)
    current = replace(old, max_executions_per_run=120, **{field: value})
    assert not policy_receipt_matches(tmp_path, current, receipt)


def test_budget_snapshot_must_match_its_original_digest(tmp_path):
    old = replace(policy(), max_executions_per_run=100)
    receipt = {'policy_digest': digest(asdict(old)), 'policy_snapshot': {**asdict(old), 'timeout_seconds': 60}}
    assert not policy_receipt_matches(tmp_path, replace(old, max_executions_per_run=120), receipt)


@pytest.mark.parametrize(('before', 'after', 'snapshot', 'expected'), [
    (120, 140, True, True), (120, 140, False, False),
    (100, 99, True, False), (100, 99, False, False),
])
def test_budget_compatibility_requires_expansion_and_bounded_legacy_history(tmp_path, before, after, snapshot, expected):
    old = replace(policy(), max_executions_per_run=before)
    receipt = {'policy_digest': digest(asdict(old))}
    if snapshot:
        receipt['policy_snapshot'] = asdict(old)
    assert policy_receipt_matches(tmp_path, replace(old, max_executions_per_run=after), receipt) is expected


def test_budget_wait_resumes_once_without_resetting_usage(case, monkeypatch):
    service, _ = prepare(case)
    p = service.config.runtime.host_verification
    p.max_executions_per_run = 1
    service.writer.emit('runtime.action.attempt.started', payload={
        'workflow_run_id': 'run-1', 'approval_ref': p.approval_ref, 'manifest_digest': 'prior-probe'})
    calls = []
    monkeypatch.setattr('zf.runtime.host_verification.verify_in_container', lambda *args: (
        calls.append(args) or {'status':'completed', 'source_stable':True, 'exit_code':0}, 'passed'))
    assert reconcile_host_verification(service) == 0
    assert reconcile_host_verification(service) == 0
    assert not calls
    p.max_executions_per_run = 2  # Owner config update; the approval identity stays unchanged.
    assert reconcile_host_verification(service) == 1
    assert reconcile_host_verification(service) == 0
    assert len(calls) == 1
    events = service.writer.event_log.read_all()
    assert sum(e.type == 'runtime.action.attempt.started' and e.payload.get('approval_ref') == p.approval_ref for e in events) == 2
    assert sum(e.type == 'human.escalate' for e in events) == 1


@pytest.mark.parametrize('reason', ['unknown container outcome', 'source changed'])
def test_more_budget_does_not_override_nonbudget_escalation(case, monkeypatch, reason):
    service, _ = prepare(case)
    manifest = preview(service, operation_id='op-1', kind='verify', command_id='contract-verification')
    service.writer.append(ZfEvent(type='human.escalate', payload={
        'host_manifest_digest':manifest['manifest_digest'], 'reason':reason}))
    service.config.runtime.host_verification.max_executions_per_run = 120
    monkeypatch.setattr('zf.runtime.host_verification.verify_in_container', lambda *args: pytest.fail('unresolved escalation'))
    assert reconcile_host_verification(service) == 0


def test_new_effect_failure_is_not_hidden_by_the_old_budget_alert(case, monkeypatch):
    service, _ = prepare(case)
    manifest = preview(service, operation_id='op-1', kind='verify', command_id='contract-verification')
    service.writer.emit('human.escalate', payload={
        'host_manifest_digest': manifest['manifest_digest'], 'reason': 'preauthorized_probe_budget_exhausted'})
    service.config.runtime.host_verification.max_executions_per_run = 120
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise ValueError('protected source changed')

    monkeypatch.setattr('zf.runtime.host_verification.apply', fail)
    assert reconcile_host_verification(service) == 1
    assert reconcile_host_verification(service) == 0
    assert len(calls) == 1
    assert [e.payload['reason'] for e in service.writer.event_log.read_all()
            if e.type == 'human.escalate'] == [
        'preauthorized_probe_budget_exhausted', 'protected source changed']
