"""Review regressions across watcher cursors, task archives and Web metrics."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.cli.watch_batch import make_watch_callbacks
from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_contract_authority import TaskContractAuthorityService
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.watcher import EventWatcher
from zf.web.projections import metric_tasks, metric_views


def _runtime(root: Path):
    state = root / "custom-state"
    state.mkdir(exist_ok=True)
    sessions = SessionStore(state / "session.yaml")
    if not sessions.path.exists():
        sessions.create(str(root))
    return Orchestrator(
        state, ZfConfig(project=ProjectConfig(name="review-regressions")),
        TmuxTransport(TmuxSession("review-regressions", dry_run=True)),
        project_root=root,
    )


def _watch(runtime):
    on_event, on_batch = make_watch_callbacks(
        orchestrator=runtime, wake_patterns={"worker.heartbeat"},
        wake_worthy_fn=lambda _: True,
        rate_limiter=SimpleNamespace(allow=lambda _: True),
        simulation=False, event_log=runtime.event_log, stop_watcher=lambda: None,
    )
    return EventWatcher(runtime.event_log.path, on_event, event_log=runtime.event_log,
                        on_batch_consumed=on_batch)


def _note(text):
    return ZfEvent(type="memory.note", payload={"mem_type": "context", "content": text})


@pytest.mark.parametrize("idle_between", [False, True])
def test_observation_before_wake_is_applied_exactly_once(tmp_path, idle_between):
    runtime = _runtime(tmp_path)
    watcher = _watch(runtime)
    runtime.event_log.append(_note("observation before wake"))
    watcher.poll_once()
    if idle_between:
        runtime.run_once()
    runtime.event_log.append(ZfEvent(type="worker.heartbeat"))
    watcher.poll_once()
    runtime.run_once()
    assert [entry.content for entry in runtime.memory_store.get(None)] == [
        "observation before wake",
    ]


@pytest.mark.parametrize("new_count", [1, 2])
def test_watcher_reads_equal_or_larger_rotated_segment(tmp_path, new_count):
    log = EventLog(tmp_path / "events.jsonl")
    old = ZfEvent(type="worker.heartbeat", id="evt-old")
    log.append(old)
    batches = []
    watcher = EventWatcher(log.path, lambda _: None, event_log=log,
                           on_batch_consumed=lambda events, _: batches.extend(events))
    archive = tmp_path / "events"
    archive.mkdir()
    log.path.rename(archive / "2026-10-30.jsonl")
    expected = [replace(old, id=f"evt-{i:03}") for i in range(new_count)]
    for event in expected:
        log.append(event)
    watcher.poll_once()
    watcher.poll_once()
    assert [e.id for e in batches] == [e.id for e in expected]


def test_restart_consumes_unread_archive_tail_then_new_active(tmp_path):
    runtime = _runtime(tmp_path)
    log = runtime.event_log
    log.append(_note("before checkpoint"))
    runtime.run_once()
    log.append(_note("unread before rotation"))
    archive = log.path.parent / "events"
    archive.mkdir(exist_ok=True)
    log.path.rename(archive / "2026-10-30.jsonl")
    log.append(_note("after rotation " + "x" * 3000))
    restarted = _runtime(tmp_path)
    restarted.run_once()
    restarted.run_once()
    assert [entry.content for entry in restarted.memory_store.get(None)] == [
        "before checkpoint", "unread before rotation", "after rotation " + "x" * 3000,
    ]


@pytest.mark.parametrize("lost_index", [False, True])
def test_reclosed_task_returns_latest_contract_and_can_reopen_again(tmp_path, lost_index):
    store = TaskStore(tmp_path / "kanban.json")
    service = TaskContractAuthorityService(
        task_store=store, state_dir=tmp_path,
        event_writer=EventWriter(EventLog(tmp_path / "events.jsonl")),
    )
    store.add(Task(id="T1", title="Replan", contract=TaskContract(behavior="v1")))
    store.update("T1", status="done")
    revised = service.replace(
        store.get("T1"), contract=TaskContract(behavior="v2"), source="test_replan",
        task_updates={"status": "backlog"}, reopen_terminal=True,
    ).task
    closed = store.update("T1", status="done")
    if lost_index:
        (tmp_path / "kanban-terminal-index.json").unlink()
    assert store.get("T1") == closed
    assert store.get("T1").contract_authority_revision == revised.contract_authority_revision
    third = service.replace(
        store.get("T1"), contract=TaskContract(behavior="v3"), source="test_replan",
        task_updates={"status": "backlog"}, reopen_terminal=True,
    ).task
    assert third.contract.behavior == "v3"


def test_web_metrics_keep_reopened_task_current(tmp_path):
    store = TaskStore(tmp_path / "kanban.json")
    task = store.add(Task(id="T1", title="Reopened"))
    store.update(task.id, status="done")
    metric_views.refresh(tmp_path)
    store.reopen(replace(task, status="backlog"))
    metric_views.refresh(tmp_path)
    assert [(t.id, t.status) for t in metric_tasks.rows(tmp_path)] == [(task.id, "backlog")]
    assert metric_tasks.counts(tmp_path) == {"ready": 1}
    assert metric_tasks.core_inputs(tmp_path)[0]["done"] == 0
