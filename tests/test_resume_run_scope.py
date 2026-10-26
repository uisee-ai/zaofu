from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.handoff_summary import project_handoff_summary
from zf.runtime.long_horizon import TaskEventIndex, build_resume_packet


@pytest.mark.parametrize("pending_request", [False, True])
def test_recovery_consumers_do_not_inherit_old_run_completion(
    tmp_path: Path, pending_request: bool,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "feature_list.json").write_text("[]\n")
    task = Task(
        id="TASK-REUSED", status="in_progress", active_dispatch_id="dispatch-new",
        contract=TaskContract(behavior="current delivery", owner_role="dev"),
        execution_binding=(
            TaskExecutionBinding(request_id="REQ", request_revision=2)
            if pending_request else TaskExecutionBinding(workflow_run_id="RUN-NEW")
        ),
    )
    TaskStore(state / "kanban.json").add(task)
    rows = [
        ZfEvent(id="current-dispatch", type="task.dispatched", task_id=task.id,
                payload={"workflow_run_id": "RUN-NEW", "dispatch_id": "dispatch-new", "role": "dev"}),
        ZfEvent(id="stale-judge", type="judge.passed", task_id=task.id,
                payload={"workflow_run_id": "RUN-OLD", "dispatch_id": "dispatch-old",
                         "files_touched": ["obsolete.py"]}),
    ]
    log = EventLog(state / "events.jsonl")
    for row in rows:
        log.append(row)
    log.close()
    packet = build_resume_packet(state, task.id, project_root=tmp_path,
                                 index=TaskEventIndex(rows))
    assert not packet["completed_evidence"]
    assert not packet["do_not_repeat"]
    assert "obsolete.py" not in packet["changed_files"]
    summary = project_handoff_summary(state, task.id, task=task,
                                      task_events=list(enumerate(rows)), index=TaskEventIndex(rows))
    assert summary["current_stage"] != "ship"
    assert "stale-judge" not in summary["source_event_ids"]
    assert "obsolete.py" not in summary["changed_files"]
