"""A timed-out rebuild observes verified closure of its exact Goal lineage on the next tick."""
from dataclasses import replace

import pytest

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.run_manager import _settle_pending_action_effects, _unresolved_action_effects


def evidence():
    run = 'run-1'
    scope = {'workflow_run_id': run, 'task_map_generation': 'generation'}
    goal = {**scope, 'goal_id': 'goal', 'plan_artifact_package_digest': 'package'}
    receipt = {'kind': 'task_integration_receipt', 'ref': 'artifacts/integration.json', 'sha256': 'e' * 64}
    action = {**scope, 'effect_id': 'E', 'action': 'fanout-aggregate-rebuild',
              'failure_class': 'goal_closure_identity_invalid', 'source_event_id': 'invalid',
              'expected_event_types': ['flow.goal.closed']}
    def event(kind, key, body, cause=None):
        return ZfEvent(type=kind, id=key, origin='kernel', correlation_id=run, causation_id=cause, payload=body)
    return [
        event('goal.closure.identity.invalid', 'invalid', {**goal, 'candidate_head_commit': 'source'}),
        event('run.manager.action.effect.pending', 'pending', action),
        event('integration.queue.integrated', 'integration', {**scope, 'task_id': 'T', 'task_commit': 'source',
              'status': 'integrated', 'receipt_ref': receipt, 'receipt_digest': receipt['sha256']}),
        event('candidate.ready', 'freeze', {**goal, 'status': 'frozen', 'candidate_head_commit': 'target',
              'task_integration_receipt_refs': [receipt], 'completed_task_ids': ['T']}),
        event('run.manager.action.effect.failed', 'timed-out', {**action, 'reason': 'recovery effect timed out',
              'effect_pending_event_id': 'pending', 'observed_event_id': ''}, 'pending'),
        event('flow.goal.closed', 'closed', {**goal, 'candidate_head_commit': 'target'}),
    ]


@pytest.mark.parametrize('change', ['integrated', 'same_target', 'wrong_goal', 'wrong_run', 'wrong_generation',
    'wrong_plan', 'unbound_target', 'untrusted_integration', 'untrusted_closure', 'other_failure', 'explicit_failure',
    'unbound_timeout', 'other_receipt', 'other_task', 'wrong_freeze_plan', 'repaired_copy'])
def test_late_goal_rebuild_requires_exact_failure_and_target_lineage(tmp_path, change):
    rows = evidence()
    if change == 'same_target':
        rows[-1].payload['candidate_head_commit'] = 'source'
    elif change in {'wrong_goal', 'wrong_run', 'wrong_generation', 'wrong_plan', 'unbound_target'}:
        key = {'wrong_goal': 'goal_id', 'wrong_run': 'workflow_run_id', 'wrong_generation': 'task_map_generation',
               'wrong_plan': 'plan_artifact_package_digest', 'unbound_target': 'candidate_head_commit'}[change]
        rows[-1].payload[key] = 'other'
    elif change == 'other_receipt':
        rows[3].payload['task_integration_receipt_refs'] = [{**rows[2].payload['receipt_ref'], 'ref': 'other'}]
    elif change == 'other_task':
        rows[3].payload['completed_task_ids'] = ['other-task']
    elif change == 'wrong_freeze_plan':
        rows[3].payload['plan_artifact_package_digest'] = 'other'
    elif change == 'repaired_copy':
        rows[3].payload['repaired_failure_event_id'] = 'failure'
    elif change == 'untrusted_integration':
        rows[2] = replace(rows[2], origin='worker')
    elif change == 'untrusted_closure':
        rows[-1] = replace(rows[-1], origin='worker')
    elif change == 'other_failure':
        rows[1].payload['source_event_id'] = 'missing'
    elif change == 'explicit_failure':
        rows[4].payload['observed_event_id'] = 'failure-evidence'
    elif change == 'unbound_timeout':
        rows[4].payload['effect_pending_event_id'] = 'other'
    writer = EventWriter(EventLog(tmp_path/'events.jsonl'), default_origin='kernel')
    for row in rows:
        writer.append(row)
    initial = writer.event_log.read_all()
    assert _unresolved_action_effects(initial) == ([], ['E'])
    recovered = change in {'integrated', 'same_target'}
    assert _settle_pending_action_effects(initial, writer) == int(recovered)
    final = writer.event_log.read_all()
    assert _unresolved_action_effects(final) == ([], [] if recovered else ['E'])
    if recovered:
        assert final[-1].payload['effect_pending_event_id'] == 'pending'
        assert final[-1].payload['observed_event_id'] == final[-1].causation_id == 'closed'
        assert final[-1].type == 'run.manager.action.effect.passed'
        assert _settle_pending_action_effects(final, writer) == 0


def test_run_manager_tick_reconciles_once_without_emitting_product_verdict(tmp_path):
    from tests.test_run_manager import _state, _config
    from zf.runtime.run_manager import run_manager_tick

    state, log, writer = _state(tmp_path)
    for row in evidence():
        writer.append(row)
    for _ in range(2):
        run_manager_tick(state_dir=state, writer=writer, config=_config(), project_root=tmp_path,
                         auto_execute=False, spawn_repairs=False)
    events = log.read_all()
    passed = [e for e in events if e.type == 'run.manager.action.effect.passed']
    assert len(passed) == 1
    assert passed[0].payload['observed_event_id'] == 'closed'
    assert not any(e.type in {'judge.passed', 'run.goal.completed', 'run.delivery.settled'} for e in events)
