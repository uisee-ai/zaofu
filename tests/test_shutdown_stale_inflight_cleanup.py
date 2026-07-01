"""#R fix: GracefulShutdown emit task.requeued for in_progress tasks
before tmux kill — prevents post-restart stale WIP zombies.

Cangjie 2026-05-21 evidence: 4 P*V* tasks stuck `assigned_to=review
status=in_progress` for 1h+ post zf-stop+start cycle. Root cause:
`zf stop` killed review LLM session mid-task, but kanban remained
`in_progress`. Post-restart kernel WIP check thinks review busy
forever → dispatch_skipped wip_busy_reassign_branch infinite loop.

Fix: shutdown.py adds `_emit_stale_inflight_cleanup` step between
step 4 (wait_active_turns) and step 5 (save_transcripts). Scans
kanban for status=in_progress, emits task.requeued + resets
status=backlog assigned_to=None.

Refs: cangjie incidents/2026-05-21-observation-R-zf-stop-no-graceful-
cleanup-stale-wip.md
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.shutdown import GracefulShutdown


def _make_state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    # session.yaml needed by GracefulShutdown init
    (state_dir / "session.yaml").write_text("runtime_state: running\n")
    (state_dir / "events.jsonl").touch()
    return state_dir


def _add_task(
    store: TaskStore,
    task_id: str,
    status: str,
    assigned_to: str | None = None,
    active_dispatch_id: str = "",
) -> None:
    contract = TaskContract(
        behavior=f"test {task_id}",
        verification="echo ok",
        scope=[f"{task_id}.ts"],
        owner_role="dev",
    )
    task = Task(
        id=task_id,
        title=f"test {task_id}",
        key=f"F-test:{task_id.lower()}",
        status=status,
        priority=3,
        contract=contract,
        assigned_to=assigned_to or "",
        active_dispatch_id=active_dispatch_id,
    )
    store.add(task)


# ─── core unit: cleanup scans + emits ────────────────────────────────────


def test_requeue_recovery_events_are_known_types() -> None:
    assert "task.requeue.skipped" in KNOWN_EVENT_TYPES
    assert "task.requeue.recovered" in KNOWN_EVENT_TYPES


def test_inflight_task_requeued_on_stop(tmp_path: Path):
    """in_progress task → status=backlog, assigned_to=None, task.requeued emit."""
    state_dir = _make_state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    _add_task(
        store,
        "TASK-X",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id="disp-x",
    )
    _add_task(store, "TASK-Y", status="backlog", assigned_to=None)

    transport = MagicMock()
    transport.shutdown = MagicMock()

    shutdown = GracefulShutdown(state_dir=state_dir, transport=transport)
    shutdown.execute()

    # TASK-X requeued
    task_x = store.get("TASK-X")
    assert task_x.status == "backlog"
    assert task_x.assigned_to in ("", None)
    assert task_x.active_dispatch_id == ""

    # TASK-Y unchanged
    task_y = store.get("TASK-Y")
    assert task_y.status == "backlog"

    # task.requeued event emitted for TASK-X
    events_text = (state_dir / "events.jsonl").read_text()
    requeued = [json.loads(line) for line in events_text.splitlines()
                if json.loads(line).get("type") == "task.requeued"]
    assert len(requeued) >= 1
    assert any(
        (e.get("task_id") == "TASK-X" or
         (e.get("payload") or {}).get("task_id") == "TASK-X")
        for e in requeued
    )
    matching = next(
        e for e in requeued
        if e.get("task_id") == "TASK-X" or
        (e.get("payload") or {}).get("task_id") == "TASK-X"
    )
    payload = matching.get("payload") or {}
    assert payload.get("source") == "graceful_stop_inflight_cleanup"
    assert payload.get("from_status") == "in_progress"
    assert payload.get("from_assignee") == "dev-1"
    assert payload.get("from_dispatch_id") == "disp-x"

    progress = (state_dir / "progress.md").read_text(encoding="utf-8")
    assert "`TASK-X` [backlog] (unassigned)" in progress
    assert "`TASK-X` [in_progress] @dev-1" not in progress


def test_fast_stop_requeues_and_emits_run_teardown_without_snapshot(tmp_path: Path):
    state_dir = _make_state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    _add_task(
        store,
        "TASK-X",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id="disp-x",
    )
    transport = MagicMock()
    transport.shutdown = MagicMock()

    shutdown = GracefulShutdown(state_dir=state_dir, transport=transport)
    steps = shutdown.execute_fast()

    task_x = store.get("TASK-X")
    assert task_x.status == "backlog"
    assert task_x.assigned_to in ("", None)
    assert "emit_teardown_event" in steps
    assert "save_shutdown_snapshot" not in steps
    assert not (state_dir / "last-shutdown").exists()
    transport.shutdown.assert_called_once()

    events = [
        json.loads(line)
        for line in (state_dir / "events.jsonl").read_text().splitlines()
    ]
    assert any(event["type"] == "run.teardown" for event in events)
    assert any(
        event["type"] == "task.requeued" and event.get("task_id") == "TASK-X"
        for event in events
    )


def test_inflight_task_with_current_handoff_progress_not_requeued(tmp_path: Path):
    """Current dispatch progress must survive stop so restart can hand off."""
    state_dir = _make_state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    _add_task(
        store,
        "TASK-X",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id="disp-x",
    )

    transport = MagicMock()
    transport.shutdown = MagicMock()
    shutdown = GracefulShutdown(state_dir=state_dir, transport=transport)
    shutdown.event_log.append(ZfEvent(
        type="task.dispatched",
        actor="zf-cli",
        task_id="TASK-X",
        payload={"role": "dev", "assignee": "dev-1", "dispatch_id": "disp-x"},
    ))
    build_done = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-X",
        payload={"dispatch_id": "disp-x", "summary": "done"},
    )
    shutdown.event_log.append(build_done)

    shutdown.execute()

    task_x = store.get("TASK-X")
    assert task_x.status == "in_progress"
    assert task_x.assigned_to == "dev-1"
    assert task_x.active_dispatch_id == "disp-x"

    events = [
        json.loads(line)
        for line in (state_dir / "events.jsonl").read_text().splitlines()
    ]
    assert not any(
        event.get("type") == "task.requeued" and event.get("task_id") == "TASK-X"
        for event in events
    )
    skipped = [
        event for event in events
        if event.get("type") == "task.requeue.skipped"
        and event.get("task_id") == "TASK-X"
    ]
    assert len(skipped) == 1
    assert skipped[0]["payload"]["progress_event_id"] == build_done.id
    assert skipped[0]["payload"]["progress_event_type"] == "dev.build.done"


def test_inflight_task_with_old_dispatch_progress_still_requeued(tmp_path: Path):
    """Progress from an older dispatch cannot protect the current WIP turn."""
    state_dir = _make_state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    _add_task(
        store,
        "TASK-X",
        status="in_progress",
        assigned_to="dev-1",
        active_dispatch_id="disp-current",
    )

    transport = MagicMock()
    transport.shutdown = MagicMock()
    shutdown = GracefulShutdown(state_dir=state_dir, transport=transport)
    shutdown.event_log.append(ZfEvent(
        type="task.dispatched",
        actor="zf-cli",
        task_id="TASK-X",
        payload={
            "role": "dev",
            "assignee": "dev-1",
            "dispatch_id": "disp-old",
        },
    ))
    shutdown.event_log.append(ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-X",
        payload={"dispatch_id": "disp-old", "summary": "old done"},
    ))
    shutdown.event_log.append(ZfEvent(
        type="task.dispatched",
        actor="zf-cli",
        task_id="TASK-X",
        payload={
            "role": "dev",
            "assignee": "dev-1",
            "dispatch_id": "disp-current",
        },
    ))

    shutdown.execute()

    task_x = store.get("TASK-X")
    assert task_x.status == "backlog"
    assert task_x.active_dispatch_id == ""


def test_empty_kanban_no_op(tmp_path: Path):
    """No in_progress task → no task.requeued emit."""
    state_dir = _make_state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    _add_task(store, "TASK-A", status="backlog", assigned_to=None)

    transport = MagicMock()
    transport.shutdown = MagicMock()
    shutdown = GracefulShutdown(state_dir=state_dir, transport=transport)
    shutdown.execute()

    events_text = (state_dir / "events.jsonl").read_text()
    requeued = [line for line in events_text.splitlines()
                if json.loads(line).get("type") == "task.requeued"]
    assert len(requeued) == 0


def test_multiple_inflight_all_requeued(tmp_path: Path):
    """Multiple in_progress tasks all requeued, each emits task.requeued."""
    state_dir = _make_state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    _add_task(store, "TASK-A", status="in_progress", assigned_to="dev-1")
    _add_task(store, "TASK-B", status="in_progress", assigned_to="review")
    _add_task(store, "TASK-C", status="in_progress", assigned_to="test-2")

    transport = MagicMock()
    transport.shutdown = MagicMock()
    shutdown = GracefulShutdown(state_dir=state_dir, transport=transport)
    shutdown.execute()

    for tid in ("TASK-A", "TASK-B", "TASK-C"):
        t = store.get(tid)
        assert t.status == "backlog", f"{tid} not requeued"
        assert t.assigned_to in ("", None), f"{tid} still has assigned_to"

    events_text = (state_dir / "events.jsonl").read_text()
    requeued = [json.loads(line) for line in events_text.splitlines()
                if json.loads(line).get("type") == "task.requeued"]
    requeued_tids = {
        (e.get("task_id") or (e.get("payload") or {}).get("task_id"))
        for e in requeued
    }
    assert requeued_tids == {"TASK-A", "TASK-B", "TASK-C"}


def test_step_stale_inflight_cleanup_in_completed_steps(tmp_path: Path):
    """The new step 'stale_inflight_cleanup' appears in steps_completed."""
    state_dir = _make_state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    _add_task(store, "TASK-X", status="in_progress", assigned_to="dev-1")

    transport = MagicMock()
    transport.shutdown = MagicMock()
    shutdown = GracefulShutdown(state_dir=state_dir, transport=transport)
    steps = shutdown.execute()

    assert "stale_inflight_cleanup" in steps
    # Order: should be after stop_dispatch and wait_active_turns, before save_transcripts
    idx_cleanup = steps.index("stale_inflight_cleanup")
    idx_wait = steps.index("wait_active_turns")
    idx_save = steps.index("save_transcripts")
    assert idx_wait < idx_cleanup < idx_save
