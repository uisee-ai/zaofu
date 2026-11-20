"""No-progress history survives retries and cannot borrow other work's progress."""
from zf.core.events import EventLog, ZfEvent
from zf.runtime.run_manager import _outcome_no_progress_break


def test_other_run_same_task_does_not_clear_persisted_failure_window(tmp_path):
    path = tmp_path / 'events.jsonl'
    log = EventLog(path)
    action = {'task_id': 'T1', 'workflow_run_id': 'R1', 'safe_resume_action': 'resume_verify'}
    for index in range(3):
        log.append(ZfEvent(type='workflow.resume.applied', task_id='T1', payload={
            **action, 'operation_id': f'op-{index}', 'attempt_id': f'attempt-{index}',
            'source_commit': f'deployment-{index}',
        }))
    log.append(ZfEvent(type='verify.passed', task_id='T1', payload={'workflow_run_id': 'R2'}))
    assert '3 times' in _outcome_no_progress_break(EventLog(path).read_all(), action)
    log.append(ZfEvent(type='verify.passed', task_id='T1', payload={'workflow_run_id': 'R1'}))
    assert _outcome_no_progress_break(EventLog(path).read_all(), action) == ''


def test_sibling_child_progress_does_not_clear_failure_window():
    action = {'workflow_run_id': 'R1', 'fanout_id': 'F1', 'child_id': 'reader-a',
              'safe_resume_action': 'repair_failed_children'}
    events = [ZfEvent(type='workflow.resume.applied', payload=action) for _ in range(3)]
    events.append(ZfEvent(type='fanout.child.completed', payload={**action, 'child_id': 'reader-b'}))
    assert _outcome_no_progress_break(events, action)


def test_foreign_run_attempts_do_not_exhaust_current_recovery():
    action = {'task_id': 'T1', 'workflow_run_id': 'R1', 'safe_resume_action': 'resume_verify'}
    events = [ZfEvent(type='workflow.resume.applied', payload={**action, 'workflow_run_id': 'R2'}) for _ in range(3)]
    assert _outcome_no_progress_break(events, action) == ''
