from pathlib import Path

import pytest

from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.long_horizon import TaskEventIndex, build_resume_packet
from zf.runtime.recovery_context import build_task_recovery_context
from zf.runtime.recovery import build_recovery_briefing


@pytest.mark.parametrize("case", ["current", "old_run", "old_dispatch", "ambiguous", "missing"])
def test_recovery_consumers_bind_only_current_attempt(tmp_path: Path, case: str) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "feature_list.json").write_text("[]\n")
    task = Task(id="TASK-RECOVER", status="in_progress", active_dispatch_id="dispatch-new",
                execution_binding=TaskExecutionBinding(workflow_run_id="RUN-NEW"))
    TaskStore(state / "kanban.json").add(task)
    store = TaskAttemptStore(state / "task_attempts.json")
    attempt = None
    if case != "missing":
        attempt = store.ensure_for_dispatch(
            run_id="RUN-OLD" if case == "old_run" else "RUN-NEW", task_id=task.id,
            dispatch_id="dispatch-old" if case == "old_dispatch" else "dispatch-new",
            role="dev", instance_id="dev-0", operation_id="op-current", briefing_ref="",
            created_at="2026-10-24T00:00:00+00:00", lease_expires_at="", max_attempts=3,
        ).attempt
    if case == "ambiguous":
        store.ensure_for_dispatch(
            run_id="RUN-NEW", task_id=task.id, dispatch_id="dispatch-other", role="verify",
            instance_id="verify-0", operation_id="op-other", briefing_ref="",
            created_at="2026-10-24T00:00:01+00:00", lease_expires_at="", max_attempts=3,
        )
    context = build_task_recovery_context(state, [], task_id=task.id)
    packet = build_resume_packet(state, task.id, project_root=tmp_path, index=TaskEventIndex([]))
    for facts in (context["attempt_identity"], packet["attempt_identity"]):
        assert facts["authorizes_continuation"] is False
        if case == "current":
            assert facts["status"] == "bound"
            assert facts["identity"]["attempt_id"] == attempt["attempt_id"]
            assert facts["identity"]["operation_id"] == "op-current"
            assert facts["identity"]["lease_id"] == attempt["lease_id"]
        else:
            assert facts["status"] == "unknown"
            assert facts["identity"] == {}
    briefing = build_recovery_briefing(state, "dev", task, project_root=tmp_path)
    assert "not permission to continue" in briefing
    if case == "current":
        assert attempt["attempt_id"] in briefing
        assert "op-current" in briefing
    else:
        assert "**Attempt identity**: unknown" in briefing
        assert "op-current" not in briefing


def test_resume_requested_dispatch_cannot_relabel_current_attempt(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "feature_list.json").write_text("[]\n")
    task = Task(id="TASK-RECOVER", active_dispatch_id="dispatch-new",
                execution_binding=TaskExecutionBinding(workflow_run_id="RUN-NEW"))
    TaskStore(state / "kanban.json").add(task)
    TaskAttemptStore(state / "task_attempts.json").ensure_for_dispatch(
        run_id="RUN-NEW", task_id=task.id, dispatch_id="dispatch-new", role="dev",
        instance_id="dev-0", operation_id="op-current", briefing_ref="",
        created_at="2026-10-24T00:00:00+00:00", lease_expires_at="", max_attempts=3,
    )
    packet = build_resume_packet(state, task.id, dispatch_id="dispatch-old",
                                 project_root=tmp_path, index=TaskEventIndex([]))
    assert packet["attempt_identity"]["status"] == "unknown"
    assert packet["attempt_identity"]["identity"] == {}
