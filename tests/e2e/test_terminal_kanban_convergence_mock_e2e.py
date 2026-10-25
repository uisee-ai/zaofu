from __future__ import annotations

from pathlib import Path

from zf.cli.watched_batch import process_watched_batch
from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.runtime.watcher import EventWatcher
from zf.web.projections.tasks import _kanban


class _AllowAll:
    def allow(self, _event_type: str) -> bool:
        return True


def test_goal_terminal_settles_kanban_before_simulation_stop_and_restart(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    log = EventLog(state_dir / "events.jsonl")
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-TERMINAL-E2E",
        title="terminal convergence",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(feature_id="RUN-TERMINAL-E2E"),
    ))
    config = ZfConfig(
        project=ProjectConfig(name="terminal-e2e"),
        session=SessionConfig(tmux_session="terminal-e2e"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )
    transport = TmuxTransport(TmuxSession(
        session_name="terminal-e2e",
        dry_run=True,
    ))
    orchestrator = Orchestrator(
        state_dir,
        config,
        transport,
        project_root=tmp_path,
    )
    watcher = EventWatcher(
        state_dir / "events.jsonl",
        on_event=lambda _line: None,
        on_batch_consumed=lambda events, offset: process_watched_batch(
            events,
            consumed_offset=offset,
            wake_patterns={"worker.state.changed", "run.goal.completed"},
            wake_worthy_fn=lambda _event: True,
            rate_limiter=_AllowAll(),
            orchestrator=orchestrator,
            simulation=True,
            event_log=log,
            stop_watcher=watcher.stop,
        ),
        event_log=log,
    )
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="zf-cli",
        payload={"state": "idle"},
    ))
    terminal = ZfEvent(
        type="run.goal.completed",
        actor="zf-cli",
        correlation_id="RUN-TERMINAL-E2E",
        payload={
            "run_id": "RUN-TERMINAL-E2E",
            "workflow_run_id": "RUN-TERMINAL-E2E",
            "pdd_id": "RUN-TERMINAL-E2E",
            "feature_id": "RUN-TERMINAL-E2E",
            "completed_task_ids": ["TASK-TERMINAL-E2E"],
        },
    )
    log.append(terminal)
    watched_batch_end = log.current_offset()

    watcher.poll_once()

    assert watcher.stopped is True
    assert store.list_all() == []
    assert store.get("TASK-TERMINAL-E2E").status == "done"
    assert _kanban(state_dir) == []
    assert (
        SessionStore(state_dir / "session.yaml").load().latest_event_offset
        == watched_batch_end
    )
    events = log.read_all()
    types = [event.type for event in events]
    assert types.count("run.goal.completed") == 1
    assert types.count("task.status_changed") == 1
    assert types.count("simulation.done") == 1
    assert types.index("run.goal.completed") < types.index("task.status_changed")
    assert types.index("task.status_changed") < types.index("simulation.done")

    restarted = Orchestrator(
        state_dir,
        config,
        transport,
        project_root=tmp_path,
    )
    restarted.run_once()

    replayed_types = [event.type for event in log.read_all()]
    assert replayed_types.count("run.goal.completed") == 1
    assert replayed_types.count("task.status_changed") == 1
    assert replayed_types.count("simulation.done") == 1
    assert store.get("TASK-TERMINAL-E2E").status == "done"
