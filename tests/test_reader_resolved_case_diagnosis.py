"""A child's successful repair cannot consume a newer aggregate failure."""
import pytest

from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.recovery_case import recovery_action_admission
from zf.runtime.run_manager import build_run_manager_projection


def _case(tmp_path, *, new_first=True, terminal='resolved', **failure_changes):
    writer = EventWriter(EventLog(tmp_path / 'events.jsonl'), default_origin='kernel')
    config = ZfConfig()
    config.runtime.run_manager.resident_agent.enabled = True
    writer.emit('run.started', payload={'workflow_run_id': 'run-1'})
    body = {'workflow_run_id': 'run-1', 'trace_id': 'run-1', 'fanout_id': 'scan-1',
            'stage_id': 'prd-scan', 'failure_kind': 'infra', 'failed_children': ['product', 'tech']}
    first = writer.emit('prd.scan.failed', payload=body)
    first_action = build_run_manager_projection(tmp_path, events=writer.event_log.read_all(), config=config)['pending_actions'][0]
    writer.emit('run.manager.action.planned', payload={**first_action, 'action': 'resident-agent-reprompt',
        'request_kind': 'recovery_diagnosis'})
    writer.emit('run.manager.resident.prompted', payload={
        'source_action_checkpoint_id': first_action['checkpoint_id'], 'request_id': 'first-diag'})
    repair = {**first_action, 'action': 'child-rebuild', 'checkpoint_id': 'repair-product',
              'source_event_ids': ['recommendation-product'], 'fanout_id': 'scan-1',
              'workflow_run_id': 'run-1', 'fanout_child_id': 'product'}
    writer.emit('run.manager.action.effect.pending', payload=repair)
    def newer():
        return writer.emit('prd.scan.failed', payload={**body, 'failed_children': ['tech'], **failure_changes})
    second = newer() if new_first else None
    if terminal == 'resolved':
        writer.emit('run.manager.action.effect.passed', payload=repair)
    elif terminal == 'blocked':
        writer.emit('run.manager.action.blocked', payload={**repair, 'blocker_kind': 'human_decision'})
    if second is None:
        second = newer()
    return writer, config, first, second, first_action


@pytest.mark.parametrize('new_first', [True, False])
def test_new_reader_input_survives_prior_child_repair(tmp_path, new_first):
    writer, config, first, second, prior = _case(tmp_path, new_first=new_first)
    projection = build_run_manager_projection(tmp_path, events=writer.event_log.read_all(), config=config)
    actions = [a for a in projection['pending_actions'] if a.get('failure_class') == 'prd_scan_failed']
    assert len(actions) == 1
    action = actions[0]
    assert action['source_event_ids'] == [second.id]
    assert action['recovery_case_id'] == prior['recovery_case_id']
    assert projection['continuation']['next_operation']['checkpoint_id'] == action['checkpoint_id']
    assert recovery_action_admission(writer.event_log.read_all(), action)['status'] == 'admitted'
    writer.emit('run.manager.action.planned', payload={**action, 'action': 'resident-agent-reprompt',
        'request_kind': 'recovery_diagnosis'})
    assert recovery_action_admission(writer.event_log.read_all(), action)['status'] == 'blocked'
    assert sum(e.id == first.id for e in writer.event_log.read_all()) == 1


@pytest.mark.parametrize('terminal', ['active', 'blocked'])
def test_new_reader_input_does_not_override_inflight_or_human_gate(tmp_path, terminal):
    writer, config, *_ = _case(tmp_path, terminal=terminal)
    p = build_run_manager_projection(tmp_path, events=writer.event_log.read_all(), config=config)
    assert not [a for a in p['pending_actions'] if a.get('failure_class') == 'prd_scan_failed']


@pytest.mark.parametrize('change', ['replay', 'other_run', 'other_fanout', 'worker_origin', 'semantic', 'mutating'])
def test_resolved_case_rejects_unbound_or_replayed_input(tmp_path, change):
    from dataclasses import replace

    writer, _, first, second, prior = _case(tmp_path)
    events = writer.event_log.read_all()
    action = {**prior, 'source_event_ids': [second.id], 'checkpoint_id': 'new-diagnosis'}
    if change == 'replay':
        action['source_event_ids'] = [first.id]
    elif change == 'mutating':
        action['action'] = 'child-rebuild'
    else:
        body = dict(second.payload)
        origin = second.origin
        if change == 'other_run':
            body['workflow_run_id'] = 'run-2'
        elif change == 'other_fanout':
            body['fanout_id'] = 'scan-2'
        elif change == 'worker_origin':
            origin = 'worker'
        else:
            body['failure_kind'] = 'semantic'
        events = [replace(e, payload=body, origin=origin) if e.id == second.id else e for e in events]
    admission = recovery_action_admission(events, action)
    assert admission['status'] == 'blocked'
    assert admission['reason'] == 'case_resolved'
