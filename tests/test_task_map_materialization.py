from __future__ import annotations

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.task_map_materialization import (
    commit_task_map_materialization,
    prepare_task_map_materialization,
)


def test_materialization_rolls_forward_after_store_fault(tmp_path):
    state_dir = tmp_path / ".zf"
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    tasks = [
        Task(
            id="A",
            title="A",
            status="backlog",
            contract=TaskContract(behavior="a", verification="true"),
        ),
        Task(
            id="B",
            title="B",
            status="backlog",
            blocked_by=["A"],
            contract=TaskContract(behavior="b", verification="true"),
        ),
    ]
    plan, descriptor = prepare_task_map_materialization(
        state_dir=state_dir,
        tasks=tasks,
        task_map_ref="artifacts/task-map.json",
        package_id="planpkg-package-sha",
        package_ref="artifacts/plan-packages/p.json",
        package_digest="package-sha",
        writer=writer,
    )

    with pytest.raises(RuntimeError, match="injected materialization fault"):
        commit_task_map_materialization(
            state_dir=state_dir,
            plan=plan,
            descriptor=descriptor,
            writer=writer,
            fail_after_store_write=True,
        )

    result = commit_task_map_materialization(
        state_dir=state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=writer,
    )
    replay = commit_task_map_materialization(
        state_dir=state_dir,
        plan=plan,
        descriptor=descriptor,
        writer=writer,
    )

    assert [task.id for task in TaskStore(state_dir / "kanban.json").list_all()] == ["A", "B"]
    assert result["status"] == "committed"
    assert result["plan_artifact_package_id"] == "planpkg-package-sha"
    assert replay == result
    events = writer.event_log.read_all()
    assert sum(event.type == "task_map.materialization.prepared" for event in events) == 1
    assert sum(event.type == "task_map.materialization.committed" for event in events) == 1
    assert sum(event.type == "task.created" for event in events) == 2
