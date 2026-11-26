"""A repaired probe must not reopen diagnosis after bound revalidation succeeds."""
from dataclasses import replace

import pytest

from tests.test_host_probe_diagnosis import _failed_probe, _later_receipt
from tests.test_task_host_actions import case
from zf.core.events import ZfEvent
from zf.runtime.run_manager import (
    _pending_post_repair_continuation_actions,
    build_repair_merge_queue,
    build_run_manager_projection,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def repaired(case, monkeypatch):
    service, _, events, failure, config = _failed_probe(case, monkeypatch)
    receipt = hydrate_sidecar_ref(service.state_dir, failure.payload['receipt_ref']).payload
    continuation = {
        'resume_original_workflow': True, 'recovery_case_id': 'case-probe',
        'workflow_run_id': 'run-1', 'task_id': 'T1', 'task_map_generation': 'map-1',
        'target_commit': receipt['target_commit'], 'checkpoint_id': 'recommendation:repair',
        'safe_resume_action': 'agent_recommendation',
    }
    planned = ZfEvent(type='run.manager.action.planned', origin='kernel', actor='run-manager',
        payload={**continuation, 'action': 'resident-agent-reprompt',
                 'source_event_id': failure.id, 'failure_event_ids': [failure.id]})
    merged = ZfEvent(type='run.manager.repair.merge.merged', id='merged', origin='kernel',
        payload={'queue_id': 'repair-1', 'fingerprint': 'repair-1', 'status': 'merged',
                 'continuation': continuation})
    history = _later_receipt(service, [*events, planned, merged], failure, code=0)
    return service, config, history, failure, merged


def pending(service, config, events):
    return _pending_post_repair_continuation_actions(
        build_repair_merge_queue(events), events, state_dir=service.state_dir, config=config)


def test_successful_revalidation_retires_original_repair_continuation(case, monkeypatch):
    service, config, events, _, _ = repaired(case, monkeypatch)
    assert pending(service, config, events) == []
    projection = build_run_manager_projection(service.state_dir, events=events,
        config=config, project_root=service.project_root)
    assert not any(a.get('checkpoint_id') == 'recommendation:repair'
                   for a in projection['pending_actions'])
    # The read projection must neither change the ledger nor invent product success.
    assert not any(e.type == 'test.passed' for e in service.writer.event_log.read_all())


@pytest.mark.parametrize('invalid', [
    'worker_success', 'missing_start', 'tampered_receipt', 'before_merge',
    'wrong_case', 'wrong_generation', 'wrong_target', 'new_failure', 'no_success',
])
def test_unbound_or_failed_revalidation_keeps_continuation(case, monkeypatch, invalid):
    service, config, events, failure, merged = repaired(case, monkeypatch)
    success = events[-1]
    if invalid == 'worker_success':
        events[-1] = replace(success, origin='worker')
    elif invalid == 'missing_start':
        events = [e for e in events if e.id != success.causation_id]
    elif invalid == 'tampered_receipt':
        (service.state_dir / success.payload['receipt_ref']['ref']).write_text('{}')
    elif invalid == 'before_merge':
        events = [e for e in events if e.id != merged.id] + [merged]
    elif invalid in {'wrong_case', 'wrong_generation', 'wrong_target'}:
        key = {'wrong_case': 'recovery_case_id', 'wrong_generation': 'task_map_generation',
               'wrong_target': 'target_commit'}[invalid]
        changed = replace(merged, payload={**merged.payload, 'continuation': {
            **merged.payload['continuation'], key: 'different'}})
        events = [changed if e.id == merged.id else e for e in events]
    elif invalid == 'new_failure':
        events.append(replace(failure, id='failed-again'))
    else:
        events = events[:-2]
    assert len(pending(service, config, events)) == 1


@pytest.mark.parametrize("change", [{"command_id": "second-obligation"}, {"tier": "different-tier"}])
def test_another_obligation_does_not_retire_the_original_repair(case, monkeypatch, change):
    service, config, events, failure, _ = repaired(case, monkeypatch)
    history = _later_receipt(service, events[:-2], failure, code=0, command=change)
    assert len(pending(service, config, history)) == 1
