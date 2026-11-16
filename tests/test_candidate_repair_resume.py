"""An authorized replacement reader owns recovery instead of the old checkpoint."""
import pytest

from zf.core.events import ZfEvent, EventLog, EventWriter
from zf.runtime.workflow_resume import WorkflowBatchResumeCheckpoint, _batch_checkpoint_superseded_reason
from zf.runtime.workflow_resume_apply import _apply_batch_checkpoint


def case():
    scope = {'workflow_run_id': 'run-1', 'pdd_id': 'P1', 'stage_id': 'verify',
             'candidate_ref': 'candidate/P1', 'candidate_head_commit': 'a' * 40, 'freeze_id': 'freeze-1'}
    source = ZfEvent(id='aggregate-failed', type='fanout.aggregate.completed', payload={
        **scope, 'fanout_id': 'old', 'status': 'failed'})
    failure = ZfEvent(id='failure', type='test.failed', payload={**scope, 'fanout_id': 'old'})
    trigger = ZfEvent(id='repair', type='candidate.ready', payload={**scope,
        'repaired_failure_event_id': failure.id, 'operator_authorized': True,
        'operator_authorization_ref': 'review.md'})
    started = ZfEvent(id='new-reader', type='fanout.started', payload={
        **scope, 'fanout_id': 'new', 'trigger_event_id': trigger.id})
    checkpoint = WorkflowBatchResumeCheckpoint(checkpoint_id='cp-old', source_event_id=source.id,
        source_event_type=source.type, blocking_event_id=source.id, safe_resume_action='reemit_candidate_ready',
        fanout_id='old', stage_id='verify', workflow_run_id='run-1', pdd_id='P1',
        candidate_ref='candidate/P1', candidate_head_commit='a' * 40, freeze_id='freeze-1',
        candidate_base_commit='b' * 40, completed_task_ids=['T1'])
    return [source, failure, trigger, started], checkpoint


def test_old_checkpoint_cannot_reemit_after_authorized_repair_starts(tmp_path):
    events, checkpoint = case()
    log = EventLog(tmp_path / 'events.jsonl')
    for event in events: log.append(event)
    result = _apply_batch_checkpoint(writer=EventWriter(log), checkpoint=checkpoint,
        state_dir=tmp_path, events=events)
    assert not result.applied
    assert 'authorized reader repair' in result.reason
    assert len([e for e in log.read_all() if e.type == 'candidate.ready']) == 1


@pytest.mark.parametrize('fault', ['no_dispatch', 'wrong_run', 'wrong_stage', 'wrong_failure',
    'wrong_target', 'wrong_freeze', 'unapproved', 'wrong_trigger'])
def test_unrelated_repair_does_not_hide_pending_checkpoint(fault):
    events, checkpoint = case()
    if fault == 'no_dispatch': events.pop()
    elif fault == 'wrong_run': events[2].payload['workflow_run_id'] = 'other'
    elif fault == 'wrong_stage': events[3].payload['stage_id'] = 'other'
    elif fault == 'wrong_failure': events[2].payload['repaired_failure_event_id'] = 'other'
    elif fault == 'wrong_target': events[2].payload['candidate_head_commit'] = 'b' * 40
    elif fault == 'wrong_freeze': events[2].payload['freeze_id'] = 'other'
    elif fault == 'unapproved': events[2].payload['operator_authorized'] = False
    elif fault == 'wrong_trigger': events[3].payload['trigger_event_id'] = 'other'
    reason = _batch_checkpoint_superseded_reason(events, checkpoint)
    assert 'authorized reader repair' not in reason
