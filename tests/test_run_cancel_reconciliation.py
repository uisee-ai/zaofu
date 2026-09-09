from zf.core.events.model import ZfEvent
from zf.runtime.run_cancel_reconciliation import _task_bound_to_run


def test_old_cancel_does_not_own_retried_task() -> None:
    events = [
        ZfEvent(
            type="workflow.invoke.requested",
            actor="external-issue-intake",
            task_id="ISSUE-23",
            payload={"workflow_run_id": "TRIAGE-OLD"},
        ),
        ZfEvent(
            type="workflow.invoke.requested",
            actor="external-issue-intake",
            task_id="ISSUE-23",
            payload={"workflow_run_id": "TRIAGE-NEW"},
        ),
        ZfEvent(
            type="run.cancelled",
            actor="web",
            task_id="ISSUE-23",
            payload={"workflow_run_id": "TRIAGE-OLD"},
        ),
    ]

    assert not _task_bound_to_run(
        events,
        task_id="ISSUE-23",
        run_id="TRIAGE-OLD",
        aliases={},
    )
