"""Active anchor protection must not block replay or a terminal owner's reuse."""

import pytest

from zf.core.events.model import ZfEvent


@pytest.mark.parametrize(("task_id", "request_id", "last_event", "blocked"), [
    ("TASK-A", "REQ-B", "", True),
    ("TASK-A", "REQ-B", "run.paused", True),
    ("TASK-A", "REQ-A", "", False),
    ("TASK-B", "REQ-B", "", False),
    ("TASK-A", "REQ-B", "run.completed", False),
])
def test_active_anchor_request_guard(task_id, request_id, last_event, blocked):
    # Keep the absent v7 guard visible without breaking unrelated collection.
    from zf.runtime.workflow_task_request_rotation import active_task_request_blocker

    rows = [ZfEvent(
        type="run.admission.admitted", task_id="TASK-A",
        payload={"workflow_run_id": "RUN-A", "request_id": "REQ-A",
                 "source_event_id": "invoke-A"},
    )]
    if last_event:
        rows.append(ZfEvent(type=last_event, payload={"workflow_run_id": "RUN-A"}))
    reason = active_task_request_blocker(iter(rows), task_id=task_id, request_id=request_id)
    assert reason == ("workflow Task TASK-A is owned by active Run RUN-A" if blocked else "")
