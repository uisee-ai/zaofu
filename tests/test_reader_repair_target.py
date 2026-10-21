import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot, write_task_contract_snapshot, snapshot_payload_fields,
    build_target_snapshot, write_target_snapshot, target_payload_fields,
    hydrate_target_snapshot, target_descriptor_from_payload,
)
from tests.test_control_actions_recovery import _service, _req


def setup_reader(tmp_path, *, compact=False):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()
    git('init', '-q')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'base')
    old = git('rev-parse', 'HEAD')
    git('checkout', '-qb', 'repair')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'repair')
    new = git('rev-parse', 'HEAD')
    svc = _service(tmp_path)
    svc.project_root = tmp_path
    svc.config = SimpleNamespace(workflow=SimpleNamespace(stages=[SimpleNamespace(
        id='prd-post-verify-discovery', attempt_domain='task', topology='fanout_reader',
        trigger='flow.discovery.requested', failure_event='',
        aggregate=SimpleNamespace(failure_event='flow.discovery.failed'),
    )]))
    snapshot = build_task_contract_snapshot(Task(id='T1', title='verify repair'),
        workflow_run_id='RUN', task_map_generation_id='GEN', base_commit=old, task_ref='task/T1')
    descriptor = write_task_contract_snapshot(svc.state_dir, snapshot)
    target = write_target_snapshot(svc.state_dir, build_target_snapshot(descriptor,
        target_commit=old, contract_snapshot=snapshot))
    origin_payload = {
        **snapshot_payload_fields(descriptor), **target_payload_fields(target),
        'workflow_run_id': 'RUN', 'task_id': 'T1', 'task_map_generation': 'GEN',
        'candidate_ref': 'refs/heads/old', 'candidate_head_commit': old, 'target_commit': old,
    }
    if compact:
        for key in ('target_commit', 'target_snapshot_ref', 'target_snapshot_digest'):
            origin_payload.pop(key)
    origin = svc.writer.append(ZfEvent(type='flow.discovery.requested', task_id='T1', correlation_id='RUN', payload=origin_payload))
    svc.writer.append(ZfEvent(type='fanout.started', correlation_id='RUN', payload={
        'fanout_id': 'failed-reader', 'stage_id': 'prd-post-verify-discovery', 'trigger_event_id': origin.id,
    }))
    failure = svc.writer.append(ZfEvent(type='flow.discovery.failed', task_id='T1', correlation_id='RUN', payload={
        'workflow_run_id': 'RUN', 'fanout_id': 'failed-reader', 'failure_class': 'verifier_execution_failure',
    }))
    escalation = svc.writer.append(ZfEvent(type='human.escalate', task_id='T1', correlation_id='RUN', causation_id=failure.id,
        payload={'failure_class': 'stage_replan_cap_exhausted', 'source_event_id': failure.id,
                 'allowed_actions': ['operator_review', 'start_new_generation']}))
    payload = {'escalation_event_id': escalation.id, 'approval_ref': 'operator:approved-repair',
               'repair_target': {'source_commit': old, 'commit': new, 'ref': 'refs/heads/repair'}}
    def apply(p=payload):
        return svc._execute_action(requested=_req(p), action='stage-replan-new-generation',
                                   requested_action='stage-replan-new-generation', payload=p)
    return svc, origin, payload, apply


@pytest.mark.parametrize('compact', [False, True])
def test_repaired_reader_gets_new_target_with_original_contract(tmp_path: Path, compact):
    svc, origin, payload, apply = setup_reader(tmp_path, compact=compact)
    result = apply()
    assert result['ok']
    trigger = next(e for e in svc.writer.event_log.read_all() if e.id == result['stage_trigger_event_id'])
    assert trigger.payload['candidate_head_commit'] == payload['repair_target']['commit']
    assert trigger.payload['target_commit'] == payload['repair_target']['commit']
    assert trigger.payload['candidate_ref'] == 'refs/heads/repair'
    assert trigger.payload['contract_snapshot_ref'] == origin.payload['contract_snapshot_ref']
    new = hydrate_target_snapshot(svc.state_dir, target_descriptor_from_payload(trigger.payload))
    if not compact:
        old = hydrate_target_snapshot(svc.state_dir, target_descriptor_from_payload(origin.payload))
        assert old['target_commit'] == payload['repair_target']['source_commit']
    assert new['target_commit'] == payload['repair_target']['commit']
    assert apply()['replayed']
    conflicting = {**payload, 'repair_target': {**payload['repair_target'], 'commit': 'a' * 40}}
    assert not apply(conflicting)['ok']


@pytest.mark.parametrize('change', [
    {'source_commit': 'a' * 40}, {'commit': 'a' * 40}, {'ref': '--help'}, {'ref': 'refs/heads/missing'},
    {'commit': ''},
])
def test_bad_repair_cannot_reopen_run(tmp_path: Path, change):
    svc, origin, payload, apply = setup_reader(tmp_path)
    payload['repair_target'].update(change)
    result = apply()
    assert not result['ok']
    assert not any(e.type == 'run.goal.updated' for e in svc.writer.event_log.read_all())
    assert len([e for e in svc.writer.event_log.read_all() if e.type == 'flow.discovery.requested']) == 1


def test_unchanged_target_cannot_buy_another_retry(tmp_path: Path):
    svc, origin, payload, apply = setup_reader(tmp_path)
    payload['repair_target']['commit'] = payload['repair_target']['source_commit']
    assert not apply()['ok']


def test_non_descendant_repair_and_corrupted_snapshot_are_rejected(tmp_path: Path):
    svc, origin, payload, apply = setup_reader(tmp_path)
    subprocess.check_call(['git', '-C', str(tmp_path), 'checkout', '--orphan', 'unrelated'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(['git', '-C', str(tmp_path), '-c', 'user.name=Test', '-c',
                          'user.email=test@example.invalid', 'commit', '--allow-empty', '-qm', 'unrelated'])
    unrelated = subprocess.check_output(['git', '-C', str(tmp_path), 'rev-parse', 'HEAD'], text=True).strip()
    invalid = {**payload, 'repair_target': {**payload['repair_target'],
               'ref': 'refs/heads/unrelated', 'commit': unrelated}}
    assert not apply(invalid)['ok']
    (svc.state_dir / origin.payload['target_snapshot_ref']).write_text('{}')
    assert not apply()['ok']
    assert not any(e.type == 'run.goal.updated' for e in svc.writer.event_log.read_all())
