"""S3 conformance cases for the deterministic orchestration spine.

These cases intentionally inspect both the event ledger and canonical task
store.  A dispatch decision alone is not enough evidence that the kernel
performed a valid transition.
"""

from __future__ import annotations

from pathlib import Path
import time

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport

from tests.testkit.oracles import (
    assert_forbidden_events,
    assert_no_duplicate_event_ids,
    assert_required_events,
)
from tests.testkit.scenarios import IsolatedProject, isolated_project
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence
from tests.test_fanout_hardening import _start, _state


@pytest.fixture
def orchestration_context(tmp_path: Path) -> tuple[IsolatedProject, TaskStore, EventLog, Orchestrator]:
    """Build one deterministic project without touching the checkout.

    The fixture deliberately uses ``state/`` rather than the repository's
    default ``.zf/`` so that this suite proves custom state-dir propagation.
    """
    project = isolated_project(
        tmp_path,
        run_id="RUN-S3-CONFORMANCE",
        task_id="TASK-S3",
    )
    state_dir = project.state_dir
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="session.started", actor="zf-cli"))
    log.append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    SessionStore(state_dir / "session.yaml").update(runtime_state="active")
    store = TaskStore(state_dir / "kanban.json")
    config = ZfConfig(
        project=ProjectConfig(name="s3-conformance"),
        session=SessionConfig(tmux_session="s3-conformance"),
        roles=[
            RoleConfig(
                name="dev",
                backend="mock",
                stages=["implement"],
                publishes=["dev.build.done", "dev.blocked"],
            ),
            RoleConfig(
                name="review",
                backend="mock",
                stages=["code_review"],
                publishes=["review.approved", "review.rejected"],
            ),
        ],
    )
    transport = TmuxTransport(
        TmuxSession(session_name="s3-conformance", dry_run=True),
    )
    orchestrator = Orchestrator(
        state_dir,
        config,
        transport,
        project_root=tmp_path,
    )
    return project, store, log, orchestrator


def test_ready_task_dispatch_updates_ledger_and_store(orchestration_context, request) -> None:
    project, store, log, orchestrator = orchestration_context
    attach_runtime_case_evidence(request, project.state_dir, task_id=project.task_id,
        given="active private session, mock roles and dry-run tmux", when="add backlog task and run_once",
        then="one dispatch; canonical in_progress with dispatch identity", forbidden=["duplicate dispatch", "missing task.dispatched"])
    store.add(Task(id=project.task_id, title="ready task", status="backlog"))

    decisions = orchestrator.run_once()

    dispatches = [item for item in decisions if item.action == "dispatch"]
    assert len(dispatches) == 1
    assert dispatches[0].task_id == project.task_id
    assert store.get(project.task_id).status == "in_progress"
    dispatched = [event for event in log.read_all() if event.type == "task.dispatched"]
    assert len(dispatched) == 1
    assert dispatched[0].task_id == project.task_id
    assert dispatched[0].payload.get("dispatch_id")
    assert_required_events(log, ["task.dispatched"])
    assert_no_duplicate_event_ids(log)


def test_dependency_barrier_keeps_child_backlog_until_parent_done(
    orchestration_context, request,
) -> None:
    project, store, log, orchestrator = orchestration_context
    attach_runtime_case_evidence(request, project.state_dir, task_id=project.task_id,
        given="private parent/child task dependency", when="run_once before and after kernel parent-done transition",
        then="child dispatch only after dependency closes", forbidden=["premature child dispatch"], inputs={"parent_task_id": "TASK-PARENT"})
    store.add(Task(id="TASK-PARENT", title="parent", status="in_progress"))
    store.add(
        Task(
            id=project.task_id,
            title="dependent child",
            status="backlog",
            blocked_by=["TASK-PARENT"],
        ),
    )

    decisions = orchestrator.run_once()

    assert [item for item in decisions if item.action == "dispatch"] == []
    assert store.get(project.task_id).status == "backlog"
    assert_forbidden_events(log, ["task.dispatched"])

    assert orchestrator._move_task(
        "TASK-PARENT",
        "done",
        trigger_event="judge.passed",
    )
    assert store.get(project.task_id).status == "backlog"
    second = orchestrator.run_once()
    assert [item for item in second if item.action == "dispatch"]
    assert store.get(project.task_id).status == "in_progress"


def test_duplicate_wakeup_does_not_redispatch_same_task(orchestration_context, request) -> None:
    project, store, log, orchestrator = orchestration_context
    attach_runtime_case_evidence(request, project.state_dir, task_id=project.task_id,
        given="review task assigned to mock dev", when="deliver the same review.rejected twice",
        then="one rework request and one dispatch", forbidden=["second dispatch", "duplicate event ids"],
        inputs={"event_id": "EV-S3-REJECT-1", "correlation_id": project.run_id})
    store.add(Task(id=project.task_id, title="reworkable", status="review", assigned_to="dev"))
    rejection = ZfEvent(
        id="EV-S3-REJECT-1",
        type="review.rejected",
        actor="review",
        task_id=project.task_id,
        payload={"reason": "missing regression evidence"},
        correlation_id=project.run_id,
    )
    log.append(rejection)

    first = orchestrator.run_once(events=[rejection])
    second = orchestrator.run_once(events=[rejection])

    assert [item for item in first if item.action == "dispatch"]
    assert [item for item in second if item.action == "dispatch"] == []
    events = log.read_all()
    assert len([event for event in events if event.type == "task.dispatched"]) == 1
    assert len([event for event in events if event.type == "task.rework.requested"]) == 1
    assert store.get(project.task_id).status == "in_progress"
    assert_no_duplicate_event_ids(log)


def test_provider_completion_moves_task_to_review_without_terminal_completion(
    orchestration_context, request,
) -> None:
    """A provider fact advances the task, but cannot self-complete the run."""

    project, store, log, orchestrator = orchestration_context
    attach_runtime_case_evidence(request, project.state_dir, task_id=project.task_id,
        given="in_progress task with mock provider fact", when="append and consume dev.build.done",
        then="task enters review, not run completion", forbidden=["run.goal.completed"],
        inputs={"event_id": "EV-S3-PROVIDER-DONE", "correlation_id": project.run_id})
    store.add(
        Task(
            id=project.task_id,
            title="provider result",
            status="in_progress",
            assigned_to="dev",
        )
    )
    completed = ZfEvent(
        id="EV-S3-PROVIDER-DONE",
        type="dev.build.done",
        actor="dev",
        task_id=project.task_id,
        correlation_id=project.run_id,
        payload={"result": "provider reported completion"},
    )

    log.append(completed)
    orchestrator.run_once()
    orchestrator.run_once(events=[completed])

    assert store.get(project.task_id).status == "review"
    assert len([event for event in log.read_all() if event.type == "dev.build.done"]) == 1
    assert_forbidden_events(log, ["run.goal.completed"])


def test_review_failure_routes_bounded_rework_and_preserves_reason(
    orchestration_context, request,
) -> None:
    project, store, log, orchestrator = orchestration_context
    attach_runtime_case_evidence(request, project.state_dir, task_id=project.task_id,
        given="review task assigned to mock dev", when="consume review.rejected with missing-test reason",
        then="rework dispatch and briefing retain the reason", forbidden=["run.goal.completed"],
        inputs={"reason": "missing empty-input test", "correlation_id": project.run_id})
    store.add(Task(id=project.task_id, title="reviewed task", status="review", assigned_to="dev"))
    rejection = ZfEvent(
        id="EV-S3-REJECT-2",
        type="review.rejected",
        actor="review",
        task_id=project.task_id,
        payload={"reason": "missing empty-input test"},
        correlation_id=project.run_id,
    )
    log.append(rejection)

    decisions = orchestrator.run_once(events=[rejection])

    assert any(item.action == "dispatch" for item in decisions)
    assert store.get(project.task_id).status == "in_progress"
    assert_required_events(log, ["task.rework.requested", "task.dispatched"])
    briefing = state_dir = project.state_dir / "briefings" / f"dev-{project.task_id}-rework.md"
    assert briefing.exists()
    assert "missing empty-input test" in briefing.read_text()
    assert_forbidden_events(log, ["run.goal.completed"])


def test_rework_cap_blocks_dispatch_and_emits_cap_fact(
    orchestration_context, request,
) -> None:
    """Repeated product failure must stop at the configured retry cap."""
    project, store, log, orchestrator = orchestration_context
    attach_runtime_case_evidence(request, project.state_dir, task_id=project.task_id,
        given="review task already at max_rework_attempts=1", when="consume another review.rejected",
        then="one cap fact, no dispatch, current task remains in_progress", forbidden=["task.dispatched"],
        inputs={"max_rework_attempts": 1, "retry_count": 1})
    role = orchestrator.config.roles[0]
    role.max_rework_attempts = 1
    store.add(
        Task(
            id=project.task_id,
            title="bounded rework",
            status="review",
            assigned_to=role.instance_id or role.name,
            retry_count=1,
        ),
    )
    rejection = ZfEvent(
        id="EV-S3-CAP-1",
        type="review.rejected",
        actor="review",
        task_id=project.task_id,
        payload={"reason": "same deterministic failure"},
        correlation_id=project.run_id,
    )

    decisions = orchestrator.run_once(events=[rejection])

    assert [item for item in decisions if item.action == "dispatch"] == []
    events = log.read_all()
    capped = [event for event in events if event.type == "task.rework.capped"]
    assert len(capped) == 1
    assert capped[0].payload["max_attempts"] == 1
    assert capped[0].payload["retry_count"] >= 1
    assert not [event for event in events if event.type == "task.dispatched"]
    assert orchestrator.task_store.get(project.task_id).status == "in_progress"


def test_full_dispatch_timeout_fails_fanout_without_silent_completion(
    tmp_path: Path, request,
) -> None:
    """A stage budget expiry must emit failure facts and never complete."""
    state_dir, log, _transport, orchestrator = _state(
        tmp_path,
        timeout_seconds=1,
    )
    attach_runtime_case_evidence(request, state_dir,
        given="private reader fanout with a one-second timeout", when="start candidate review then advance clock past budget",
        then="timeout and failed aggregate/rejection; worker idle", forbidden=["run.goal.completed"],
        inputs={"pdd_id": "F-11111111", "role": "review-a", "correlation_id": "trace-1"})
    _start(orchestrator)
    orchestrator._now = lambda: time.time() + 10  # type: ignore[method-assign]

    orchestrator.run_once(events=[])

    events = log.read_all()
    timed_out = [event for event in events if event.type == "fanout.timed_out"]
    assert len(timed_out) == 1
    assert timed_out[0].payload["pending_children"] == ["review-a"]
    aggregates = [
        event for event in events if event.type == "fanout.aggregate.completed"
    ]
    assert aggregates and aggregates[-1].payload["status"] == "failed"
    assert aggregates[-1].payload["reason"] == "timeout"
    rejected = [event for event in events if event.type == "review.rejected"]
    assert rejected and rejected[-1].payload["reason"] == "timeout"
    assert not [event for event in events if event.type == "run.goal.completed"]
    assert orchestrator.worker_health()["review-a"] == "idle"
