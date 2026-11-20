"""Repair completion must continue the original recovery case and versions."""
from zf.core.events.model import ZfEvent
from zf.runtime.recovery_case import bind_recovery_case, build_recovery_case_projection
from zf.runtime.run_manager import (
    _pending_post_repair_continuation_actions,
    _repair_continuation_from_action,
)


def test_post_repair_progress_closes_original_case_not_an_unrelated_case():
    original = bind_recovery_case({
        'workflow_run_id': 'run-1', 'task_id': 'task-2',
        'task_map_generation': 'generation-3', 'contract_revision': '4',
        'target_commit': 'a' * 40, 'active_attempt_id': 'attempt-5',
        'checkpoint_id': 'checkpoint-6', 'safe_resume_action': 'repair_failed_children',
        'failure_class': 'source_bug', 'fingerprint': 'reader-probe-failure',
    })
    continuation = _repair_continuation_from_action(original)
    queue = {'items': [{'status': 'merged', 'queue_id': 'repair-7',
                        'fingerprint': original['fingerprint'], 'continuation': continuation}]}
    pending = _pending_post_repair_continuation_actions(queue, [])
    assert len(pending) == 1
    resumed = bind_recovery_case(pending[0])
    for key in ('recovery_case_id', 'workflow_run_id', 'task_id', 'task_map_generation',
                'contract_revision', 'target_commit', 'active_attempt_id'):
        assert resumed[key] == original[key]
    events = [
        ZfEvent(type='run.manager.action.effect.passed', payload={**original, 'action': 'repair-closeout-apply'}),
        ZfEvent(type='run.manager.action.effect.passed', payload=resumed),
    ]
    cases = build_recovery_case_projection(events)['cases']
    assert len(cases) == 1
    assert cases[0]['status'] == 'resolved'
