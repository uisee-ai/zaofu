from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _orchestrator(tmp_path: Path) -> tuple[Orchestrator, TaskStore, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    config = ZfConfig(
        project=ProjectConfig(name="skills-dispatch"),
        session=SessionConfig(tmux_session="skills-dispatch"),
        roles=[
            RoleConfig(name="dev-basic", backend="mock", skills=[]),
            RoleConfig(name="dev-python", backend="mock", skills=["python-tdd"]),
        ],
    )
    transport = TmuxTransport(TmuxSession(session_name="skills", dry_run=True))
    return Orchestrator(state_dir, config, transport), TaskStore(state_dir / "kanban.json"), log


def _task(*, assigned_to: str | None, skills: list[str]) -> Task:
    return Task(
        id="TASK-1",
        title="implement endpoint",
        status="backlog",
        assigned_to=assigned_to,
        skills_required=skills,
        contract=TaskContract(
            behavior="endpoint returns a stable response",
            verification="pytest -q",
        ),
    )


def test_assigned_role_without_required_skills_is_not_dispatched(tmp_path: Path) -> None:
    orchestrator, store, log = _orchestrator(tmp_path)
    store.add(_task(assigned_to="dev-basic", skills=["python-tdd"]))

    decisions = orchestrator._dispatch_ready()

    assert decisions == []
    events = log.read_all()
    assert not any(event.type == "task.dispatched" for event in events)
    mismatch = next(event for event in events if event.type == "dispatch.skills_unmatched")
    assert mismatch.payload["required_skills"] == ["python-tdd"]
    assert mismatch.payload["candidate_roles"] == ["dev-basic"]
    assert mismatch.payload["recovery_owner"] == "run_manager"


def test_unassigned_task_selects_skill_compatible_role(tmp_path: Path) -> None:
    orchestrator, store, log = _orchestrator(tmp_path)
    store.add(_task(assigned_to=None, skills=["python-tdd"]))

    decisions = orchestrator._dispatch_ready()

    assert len(decisions) == 1
    assert decisions[0].role == "dev-python"
    dispatched = next(event for event in log.read_all() if event.type == "task.dispatched")
    assert dispatched.payload["assignee"] == "dev-python"
    assert not any(event.type == "dispatch.skills_unmatched" for event in log.read_all())


def test_task_without_required_skills_keeps_legacy_dispatch(tmp_path: Path) -> None:
    orchestrator, store, log = _orchestrator(tmp_path)
    store.add(_task(assigned_to="dev-basic", skills=[]))

    decisions = orchestrator._dispatch_ready()

    assert len(decisions) == 1
    assert decisions[0].role == "dev-basic"
    assert any(event.type == "task.dispatched" for event in log.read_all())
