"""Real watcher callbacks recover unacknowledged batches via CLI startup catch-up."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.testkit.scenarios import isolated_project
from zf.cli.start import (
    _process_watched_batch,
    _process_watched_event,
    _run_startup_orchestrator_catchup,
)
from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.watcher import EventWatcher


class _AllowAll:
    def allow(self, _event_type: str) -> bool:
        return True


@pytest.mark.parametrize("failure_at", ["second_event", "batch_ack"])
def test_startup_recovers_mid_batch_failure_without_duplicate_task_settlement(
    tmp_path: Path, failure_at: str, monkeypatch: pytest.MonkeyPatch, record_property,
) -> None:
    project = isolated_project(tmp_path, run_id="RUN-BATCH-RECOVERY")
    state = project.state_dir
    (state / "memory").mkdir()
    writer = project.event_writer()
    log = writer.event_log
    session = SessionStore(state / "session.yaml")
    session.create(project_root=str(tmp_path))
    tasks = TaskStore(state / "kanban.json")
    task_ids = ("TASK-BATCH-FIRST", "TASK-BATCH-PENDING", "TASK-BATCH-LATE")
    for task_id in task_ids:
        tasks.add(Task(
            id=task_id, title=task_id, status="in_progress", assigned_to="dev",
            contract=TaskContract(feature_id=f"RUN-{task_id}"),
        ))
    config = ZfConfig(
        project=ProjectConfig(name="watcher-batch-recovery", state_dir="state"),
        session=SessionConfig(tmux_session="watcher-batch-recovery"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )
    transport = TmuxTransport(TmuxSession("watcher-batch-recovery", dry_run=True))

    def reopen() -> Orchestrator:
        return Orchestrator(state, config, transport, project_root=tmp_path)

    runtime = reopen()
    _run_startup_orchestrator_catchup(runtime, log)
    baseline_offset = session.load().latest_event_offset
    pushed: set[str] = set()
    visited: list[str] = []
    batch_calls: list[int] = []
    ack_attempts: list[int] = []
    injected = RuntimeError(f"test callback interrupted at {failure_at}")

    if failure_at == "batch_ack":
        def interrupt_ack(offset: int) -> None:
            ack_attempts.append(offset)
            raise injected

        monkeypatch.setattr(runtime, "acknowledge_consumed_offset", interrupt_ack)

    def terminal(task_id: str) -> ZfEvent:
        # Given terminal input tests mechanical settlement, not semantic acceptance.
        return ZfEvent(type="run.goal.completed", actor="testkit", payload={
            "run_id": f"RUN-{task_id}", "workflow_run_id": f"RUN-{task_id}",
            "pdd_id": f"RUN-{task_id}", "feature_id": f"RUN-{task_id}",
            "completed_task_ids": [task_id],
        })

    first, pending, late = (terminal(task_id) for task_id in task_ids)

    def on_event(line: str) -> None:
        event = ZfEvent.from_json(line)
        visited.append(event.id)
        if event.id == pending.id and failure_at == "second_event":
            raise injected
        if _process_watched_event(
            event, wake_patterns={"run.goal.completed"},
            wake_worthy_fn=lambda _event: True, rate_limiter=_AllowAll(),
            orchestrator=runtime, simulation=False, event_log=log,
            stop_watcher=watcher.stop,
        ):
            pushed.add(event.id)
        if event.id == first.id:
            assert tasks.get(task_ids[0]).status == "done"
            writer.append(late)

    def on_batch(events: list[ZfEvent], offset: int) -> None:
        batch_calls.append(offset)
        assert [event.id for event in events] == [first.id, pending.id]
        _process_watched_batch(
            events, consumed_offset=offset, pushed_event_ids=pushed, orchestrator=runtime,
        )

    watcher = EventWatcher(log.path, on_event, event_log=log, on_batch_consumed=on_batch)
    writer.append(first)
    writer.append(pending)
    original_boundary = log.current_offset()
    original_bytes = log.path.read_bytes()
    with pytest.raises(RuntimeError) as caught:
        watcher.poll_once()
    assert caught.value is injected
    assert visited == [first.id, pending.id]
    assert late.id not in visited
    assert batch_calls == ([original_boundary] if failure_at == "batch_ack" else [])
    assert ack_attempts == batch_calls
    assert session.load().latest_event_offset == baseline_offset < original_boundary
    assert tasks.get(task_ids[0]).status == "done"
    assert tasks.get(task_ids[1]).status == (
        "done" if failure_at == "batch_ack" else "in_progress"
    )
    assert tasks.get(task_ids[2]).status == "in_progress"

    # New watcher starts at EOF, so recovery must use the durable CLI startup path.
    restarted = reopen()
    fresh_watcher = EventWatcher(log.path, lambda _line: pytest.fail("unexpected old line"),
                                 event_log=log)
    assert fresh_watcher.poll_once() == []
    before_catchup = log.current_offset()
    _run_startup_orchestrator_catchup(restarted, log)
    assert session.load().latest_event_offset == before_catchup
    assert all(tasks.get(task_id).status == "done" for task_id in task_ids)

    def settlements() -> list[str]:
        return [event.to_json() for event in log.read_all()
                if event.type == "task.status_changed"
                and event.task_id in task_ids and event.payload.get("to") == "done"]

    completed = settlements()
    assert len(completed) == 3
    assert {json.loads(line)["task_id"] for line in completed} == set(task_ids)
    _run_startup_orchestrator_catchup(restarted, log)
    _run_startup_orchestrator_catchup(reopen(), log)
    assert settlements() == completed
    assert log.path.read_bytes().startswith(original_bytes)
    assert [event.id for event in log.read_all() if event.type == "run.goal.completed"] == [
        first.id, pending.id, late.id,
    ]
    assert not [event for event in log.read_all()
                if event.type in {"orchestrator.tick.failed", "kernel.housekeeping.failed"}]
    record_property("watcher_batch_recovery", json.dumps({
        "failure_at": failure_at, "baseline_offset": baseline_offset,
        "unacknowledged_boundary": original_boundary, "catchup_boundary": before_catchup,
        "visited_event_ids": visited, "deferred_event_id": late.id,
        "completed_task_ids": list(task_ids), "settlements": 3,
        "real_provider": False, "process_crash": False,
    }))
