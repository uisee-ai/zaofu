"""S4 conformance cases for restart, stale work and bounded recovery."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os
import subprocess
import sys
import time

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_attempt_recovery import pending_task_attempt_recovery_actions
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport
from zf.core.state import atomic_io

from tests.testkit.cleanup import assert_no_child_process_leaks, child_processes
from tests.testkit.oracles import assert_no_duplicate_event_ids
from tests.testkit.failpoints import InjectedFailure
from tests.testkit.external_sinks import ExternalEffectSpy
from tests.testkit.scenarios import isolated_project
from tests.testkit.snapshots import truth_snapshot
from tests.testkit.evidence import write_json_atomic
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


def _orchestrator_context(tmp_path: Path):
    project = isolated_project(
        tmp_path,
        run_id="RUN-S4-CONFORMANCE",
        task_id="TASK-S4",
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
        project=ProjectConfig(name="s4-conformance"),
        session=SessionConfig(tmux_session="s4-conformance"),
        roles=[RoleConfig(name="dev", backend="mock", stages=["implement"])],
    )

    def new_orchestrator() -> Orchestrator:
        return Orchestrator(
            state_dir,
            config,
            TmuxTransport(
                TmuxSession(session_name="s4-conformance", dry_run=True),
            ),
            project_root=tmp_path,
        )

    return project, state_dir, store, log, new_orchestrator


def _runtime_dispatch_child(root: Path, mode: str) -> None:
    """Run the real Kernel dispatch path for crash/restart comparison."""
    state_dir = root / "state"
    config = ZfConfig(
        project=ProjectConfig(name="s4-runtime-child"),
        session=SessionConfig(tmux_session="s4-runtime-child"),
        roles=[RoleConfig(name="dev", backend="mock", stages=["implement"])],
    )
    orchestrator = Orchestrator(
        state_dir,
        config,
        TmuxTransport(TmuxSession(session_name="s4-runtime-child", dry_run=True)),
        project_root=root,
    )
    if mode in {"crash", "before"}:
        original_append = orchestrator.event_writer.append

        def append_then_crash(event):  # noqa: ANN001
            if mode == "before" and event.type == "task.dispatched":
                os._exit(72)
            result = original_append(event)
            if mode == "crash" and event.type == "task.dispatched":
                os._exit(71)
            return result

        orchestrator.event_writer.append = append_then_crash
    orchestrator.run_once()
    raise SystemExit(0)


def test_restart_continues_dispatch_without_repeating_side_effect(
    tmp_path: Path, request,
) -> None:
    project, state_dir, store, log, new_orchestrator = _orchestrator_context(tmp_path)
    attach_runtime_case_evidence(request, state_dir, task_id=project.task_id,
        given="private active session and backlog task", when="dispatch then rebuild Orchestrator on the same state",
        then="same active_dispatch_id and event count", forbidden=["redispatch", "state regression"])
    store.add(Task(id=project.task_id, title="restartable", status="backlog"))

    first = new_orchestrator()
    first_decisions = first.run_once()
    first_dispatches = [item for item in first_decisions if item.action == "dispatch"]
    assert len(first_dispatches) == 1
    before = truth_snapshot(
        log,
        state={
            "status": store.get(project.task_id).status,
            "active_dispatch_id": store.get(project.task_id).active_dispatch_id,
        },
    )
    dispatch_count_before = len(
        [event for event in log.read_all() if event.type == "task.dispatched"]
    )

    second = new_orchestrator()
    second_decisions = second.run_once()
    assert [item for item in second_decisions if item.action == "dispatch"] == []
    assert store.get(project.task_id).status == "in_progress"
    assert store.get(project.task_id).active_dispatch_id == before.state[
        "active_dispatch_id"
    ]
    dispatch_count_after = len(
        [event for event in log.read_all() if event.type == "task.dispatched"]
    )
    assert dispatch_count_after == dispatch_count_before
    assert_no_duplicate_event_ids(log)
    assert truth_snapshot(
        log,
        state={
            "status": store.get(project.task_id).status,
            "active_dispatch_id": store.get(project.task_id).active_dispatch_id,
        },
    ).state == before.state
    assert state_dir == project.state_dir


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-002: real Orchestrator dispatch crash after ledger append "
        "does not replay to the no-fault event sequence"
    ),
)
def test_real_orchestrator_dispatch_crash_restarts_to_baseline(
    tmp_path: Path, request,
) -> None:
    """A production Kernel child crash must be equivalent to clean dispatch."""
    module = "tests.conformance.test_zaofu_testkit_s4"
    case_root = tmp_path / "dispatch-crash-case"
    case_root.mkdir()
    evidence = attach_runtime_case_evidence(request, case_root, task_id="TASK-S4",
        given="independent baseline/fault roots with the same backlog task",
        when="real child exits after task.dispatched append, then another child resumes",
        then="canonical task and event-type sequence match clean dispatch", forbidden=["duplicate or missing dispatch", "replay sequence drift"],
        inputs={"crash_exit": 71, "child_mode": "crash"})
    baseline_root = case_root / "baseline-runtime"
    baseline_project, _baseline_state, baseline_store, baseline_log, make_baseline = (
        _orchestrator_context(baseline_root)
    )
    baseline_store.add(
        Task(id=baseline_project.task_id, title="runtime crash", status="backlog")
    )
    make_baseline().run_once()
    baseline_types = [event.type for event in baseline_log.read_all()]
    baseline_task = baseline_store.get(baseline_project.task_id)
    assert baseline_task is not None

    fault_root = case_root / "fault-runtime"
    fault_project, _fault_state, fault_store, fault_log, _make_fault = (
        _orchestrator_context(fault_root)
    )
    fault_store.add(
        Task(id=fault_project.task_id, title="runtime crash", status="backlog")
    )
    if evidence is not None:
        evidence.checkpoint()
    crashed = subprocess.run(
        [sys.executable, "-m", module, "--runtime-child", str(fault_root), "crash"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    write_json_atomic(case_root / "crash-receipt.json", {
        "args": crashed.args, "returncode": crashed.returncode, "stdout": crashed.stdout, "stderr": crashed.stderr,
    })
    assert crashed.returncode == 71, crashed.stderr

    recovered = subprocess.run(
        [sys.executable, "-m", module, "--runtime-child", str(fault_root), "resume"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    write_json_atomic(case_root / "resume-receipt.json", {
        "args": recovered.args, "returncode": recovered.returncode, "stdout": recovered.stdout, "stderr": recovered.stderr,
    })
    assert recovered.returncode == 0, recovered.stderr
    recovered_task = fault_store.get(fault_project.task_id)
    assert recovered_task is not None
    assert recovered_task.status == baseline_task.status
    assert recovered_task.active_dispatch_id
    assert len([
        event for event in fault_log.read_all()
        if event.type == "task.dispatched"
    ]) == 1
    assert [event.type for event in fault_log.read_all()] == baseline_types
    assert_no_duplicate_event_ids(fault_log)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZF-TEST-007: real Orchestrator crash before dispatch ledger append "
        "persists a state transition that is not equivalent to baseline"
    ),
)
def test_real_orchestrator_pre_dispatch_crash_restarts_to_baseline(
    tmp_path: Path, request,
) -> None:
    """A child crash before ledger append must leave a retryable backlog task."""
    module = "tests.conformance.test_zaofu_testkit_s4"
    case_root = tmp_path / "pre-dispatch-crash-case"
    case_root.mkdir()
    evidence = attach_runtime_case_evidence(request, case_root, task_id="TASK-S4",
        given="independent baseline/fault roots with the same backlog task",
        when="real child exits before task.dispatched append, then another child resumes",
        then="canonical task and event-type sequence match clean dispatch", forbidden=["duplicate or missing dispatch", "unrecoverable pre-event transition"],
        inputs={"crash_exit": 72, "child_mode": "before"})
    baseline_root = case_root / "baseline-pre-runtime"
    baseline_project, _baseline_state, baseline_store, baseline_log, make_baseline = (
        _orchestrator_context(baseline_root)
    )
    baseline_store.add(
        Task(id=baseline_project.task_id, title="pre-dispatch crash", status="backlog")
    )
    make_baseline().run_once()
    baseline_task = baseline_store.get(baseline_project.task_id)
    assert baseline_task is not None
    baseline_types = [event.type for event in baseline_log.read_all()]

    fault_root = case_root / "fault-pre-runtime"
    fault_project, _fault_state, fault_store, fault_log, _make_fault = (
        _orchestrator_context(fault_root)
    )
    fault_store.add(
        Task(id=fault_project.task_id, title="pre-dispatch crash", status="backlog")
    )
    if evidence is not None:
        evidence.checkpoint()
    crashed = subprocess.run(
        [sys.executable, "-m", module, "--runtime-child", str(fault_root), "before"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    write_json_atomic(case_root / "crash-receipt.json", {
        "args": crashed.args, "returncode": crashed.returncode, "stdout": crashed.stdout, "stderr": crashed.stderr,
    })
    assert crashed.returncode == 72, crashed.stderr

    recovered = subprocess.run(
        [sys.executable, "-m", module, "--runtime-child", str(fault_root), "resume"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    write_json_atomic(case_root / "resume-receipt.json", {
        "args": recovered.args, "returncode": recovered.returncode, "stdout": recovered.stdout, "stderr": recovered.stderr,
    })
    assert recovered.returncode == 0, recovered.stderr
    recovered_task = fault_store.get(fault_project.task_id)
    assert recovered_task is not None
    assert recovered_task.status == baseline_task.status
    assert recovered_task.active_dispatch_id
    assert [event.type for event in fault_log.read_all()] == baseline_types
    assert len([
        event for event in fault_log.read_all()
        if event.type == "task.dispatched"
    ]) == 1
    assert_no_duplicate_event_ids(fault_log)


def test_repeated_restart_recovery_keeps_one_dispatch_and_stable_snapshot(
    tmp_path: Path,
) -> None:
    """A long restart sequence must converge without redispatch or drift."""

    project, _state_dir, store, log, new_orchestrator = _orchestrator_context(tmp_path)
    store.add(Task(id=project.task_id, title="restart soak", status="backlog"))

    first = new_orchestrator()
    decisions = first.run_once()
    assert [item for item in decisions if item.action == "dispatch"]
    initial = store.get(project.task_id)
    assert initial is not None
    dispatch_id = initial.active_dispatch_id
    baseline_events = [event.id for event in log.read_all()]

    for _ in range(100):
        restarted = new_orchestrator()
        assert [item for item in restarted.run_once() if item.action == "dispatch"] == []
        current = store.get(project.task_id)
        assert current is not None
        assert current.status == "in_progress"
        assert current.active_dispatch_id == dispatch_id
        assert [event.id for event in log.read_all()] == baseline_events

    dispatches = [
        event for event in log.read_all()
        if event.type == "task.dispatched" and event.task_id == project.task_id
    ]
    assert len(dispatches) == 1
    assert_no_duplicate_event_ids(log)


def test_thousand_cycle_restart_soak_keeps_one_dispatch_and_event_snapshot(
    tmp_path: Path,
) -> None:
    """A longer deterministic restart soak must not drift canonical truth."""
    project, _state_dir, store, log, new_orchestrator = _orchestrator_context(tmp_path)
    store.add(Task(id=project.task_id, title="long restart soak", status="backlog"))
    child_baseline = child_processes()

    first = new_orchestrator()
    decisions = first.run_once()
    assert [item for item in decisions if item.action == "dispatch"]
    initial = store.get(project.task_id)
    assert initial is not None
    dispatch_id = initial.active_dispatch_id
    baseline_events = [event.id for event in log.read_all()]

    for _ in range(1000):
        restarted = new_orchestrator()
        assert [item for item in restarted.run_once() if item.action == "dispatch"] == []
        current = store.get(project.task_id)
        assert current is not None
        assert current.status == "in_progress"
        assert current.active_dispatch_id == dispatch_id
        assert [event.id for event in log.read_all()] == baseline_events

    dispatches = [
        event for event in log.read_all()
        if event.type == "task.dispatched" and event.task_id == project.task_id
    ]
    assert len(dispatches) == 1
    assert_no_duplicate_event_ids(log)
    assert_no_child_process_leaks(child_baseline)


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-018: repeated restart appends unbounded worker.drift.detected events",
)
def test_multi_task_restart_soak_preserves_dispatches_and_event_snapshot(
    tmp_path: Path,
) -> None:
    """Several independent tasks must survive repeated recovery together."""
    project = isolated_project(
        tmp_path,
        run_id="RUN-S4-MULTI-SOAK",
        task_id="TASK-S4-MULTI-0",
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
    task_ids = [f"TASK-S4-MULTI-{index}" for index in range(4)]
    for task_id in task_ids:
        store.add(Task(id=task_id, title=f"multi-task {task_id}", status="backlog"))
    config = ZfConfig(
        project=ProjectConfig(name="s4-multi-soak"),
        session=SessionConfig(tmux_session="s4-multi-soak"),
        roles=[
            RoleConfig(
                name=f"dev-{index}",
                instance_id=f"dev-{index}",
                backend="mock",
                stages=["implement"],
            )
            for index in range(len(task_ids))
        ],
    )

    def new_orchestrator() -> Orchestrator:
        return Orchestrator(
            state_dir,
            config,
            TmuxTransport(TmuxSession(session_name="s4-multi-soak", dry_run=True)),
            project_root=tmp_path,
        )

    # Give each role a deterministic reconciliation opportunity, without
    # relying on timing or sleeps to make all tasks ready.
    for _ in range(len(task_ids) + 2):
        new_orchestrator().run_once()
    initial_rows = {task_id: store.get(task_id) for task_id in task_ids}
    assert all(row is not None and row.status == "in_progress" for row in initial_rows.values())
    baseline_events = [event.id for event in log.read_all()]
    baseline_dispatches = [
        event for event in log.read_all() if event.type == "task.dispatched"
    ]
    assert {event.task_id for event in baseline_dispatches} == set(task_ids)
    assert len(baseline_dispatches) == len(task_ids)

    for _ in range(250):
        new_orchestrator().run_once()
        for task_id in task_ids:
            current = store.get(task_id)
            assert current is not None
            assert current.status == "in_progress"
            assert current.active_dispatch_id == initial_rows[task_id].active_dispatch_id

    # Evaluate drift after the complete soak instead of stopping at the first
    # diagnostic event; this proves the long-run growth is bounded (or exposes
    # the registered ZF-TEST-018 regression).
    assert [event.id for event in log.read_all()] == baseline_events

    dispatches = [
        event for event in log.read_all() if event.type == "task.dispatched"
    ]
    assert len(dispatches) == len(task_ids)
    assert_no_duplicate_event_ids(log)


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-007: dispatch mutates TaskStore before task.dispatched append",
)
def test_crash_before_dispatch_event_leaves_task_for_one_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure before the ledger append must not advance canonical state."""
    project, _state_dir, store, log, new_orchestrator = _orchestrator_context(tmp_path)
    store.add(Task(id=project.task_id, title="pre-append crash", status="backlog"))
    orchestrator = new_orchestrator()
    original_append = orchestrator.event_writer.append

    def crash_before_append(event):  # noqa: ANN001
        if event.type == "task.dispatched":
            raise InjectedFailure("before_event_append")
        return original_append(event)

    monkeypatch.setattr(orchestrator.event_writer, "append", crash_before_append)
    with pytest.raises(InjectedFailure, match="before_event_append"):
        orchestrator.run_once()

    assert store.get(project.task_id).status == "backlog"
    assert not [event for event in log.read_all() if event.type == "task.dispatched"]

    restarted = new_orchestrator()
    decisions = restarted.run_once()
    assert [item for item in decisions if item.action == "dispatch"]
    assert store.get(project.task_id).status == "in_progress"
    assert len([event for event in log.read_all() if event.type == "task.dispatched"]) == 1
    assert_no_duplicate_event_ids(log)


def test_stale_retry_result_is_ignored_and_current_attempt_is_preserved(
    tmp_path: Path, request,
) -> None:
    project, _state_dir, store, log, new_orchestrator = _orchestrator_context(tmp_path)
    attach_runtime_case_evidence(request, project.state_dir, task_id=project.task_id,
        given="task bound to disp-current with retry_count=2", when="consume retry scheduling for disp-old",
        then="skip stale scheduling and preserve active_dispatch_id", forbidden=["stale overwrite", "duplicate event ids"],
        inputs={"current_dispatch_id": "disp-current", "stale_dispatch_id": "disp-old"})
    store.add(
        Task(
            id=project.task_id,
            title="current attempt",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-current",
            retry_count=2,
        ),
    )
    stale = ZfEvent(
        id="EV-S4-STALE-1",
        type="task.retry_scheduled",
        actor="dev",
        task_id=project.task_id,
        payload={"dispatch_id": "disp-old", "route": "retry"},
        correlation_id=project.run_id,
    )
    log.append(stale)

    decision = new_orchestrator()._on_completion_scheduled(stale)

    task = store.get(project.task_id)
    assert decision is not None and decision.action == "skip"
    assert task.status == "in_progress"
    assert task.active_dispatch_id == "disp-current"
    ignored = [event for event in log.read_all() if event.type == "task.retry.stale_ignored"]
    assert len(ignored) == 1
    assert ignored[0].payload["schedule_event_id"] == stale.id
    assert_no_duplicate_event_ids(log)


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-002: dispatch context is not restored after post-event crash",
)
def test_crash_after_dispatch_event_is_replay_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, _state_dir, store, log, new_orchestrator = _orchestrator_context(tmp_path)
    store.add(Task(id=project.task_id, title="commit point", status="backlog"))
    orchestrator = new_orchestrator()
    original_append = orchestrator.event_writer.append

    def append_then_crash(event):  # noqa: ANN001
        result = original_append(event)
        if event.type == "task.dispatched":
            raise InjectedFailure("after_event_append_before_cycle_return")
        return result

    monkeypatch.setattr(orchestrator.event_writer, "append", append_then_crash)
    with pytest.raises(InjectedFailure, match="after_event_append"):
        orchestrator.run_once()

    assert store.get(project.task_id).status == "in_progress"
    assert len([event for event in log.read_all() if event.type == "task.dispatched"]) == 1

    restarted = new_orchestrator()
    assert [item for item in restarted.run_once() if item.action == "dispatch"] == []
    assert len([event for event in log.read_all() if event.type == "task.dispatched"]) == 1
    assert_no_duplicate_event_ids(log)

    baseline_root = tmp_path / "baseline"
    baseline_root.mkdir()
    baseline_project, _baseline_state, baseline_store, baseline_log, make_baseline = (
        _orchestrator_context(baseline_root)
    )
    baseline_store.add(
        Task(id=baseline_project.task_id, title="commit point", status="backlog"),
    )
    make_baseline().run_once()
    assert store.get(project.task_id).status == baseline_store.get(
        baseline_project.task_id,
    ).status
    assert store.get(project.task_id).active_dispatch_id
    assert baseline_store.get(baseline_project.task_id).active_dispatch_id
    assert [event.type for event in log.read_all()] == [
        event.type for event in baseline_log.read_all()
    ]


@pytest.mark.parametrize(
    ("status", "forbidden_event"),
    [
        ("in_progress", "run.goal.completed"),
        ("review", "run.goal.completed"),
        ("blocked", "task.dispatched"),
    ],
)
def test_recovery_state_never_fabricates_terminal_or_dispatch_event(
    tmp_path: Path,
    status: str,
    forbidden_event: str,
) -> None:
    project, _state_dir, store, log, new_orchestrator = _orchestrator_context(tmp_path)
    store.add(Task(id=project.task_id, title="recovery case", status=status))

    new_orchestrator().run_once()

    assert not [event for event in log.read_all() if event.type == forbidden_event]
    if status == "blocked":
        assert store.get(project.task_id).status == "blocked"


def test_external_effect_spy_deduplicates_retried_operation() -> None:
    spy: ExternalEffectSpy[str] = ExternalEffectSpy()
    executions: list[str] = []

    def publish() -> str:
        executions.append("published")
        return "remote-result"

    assert spy.execute("op-1", publish) == "remote-result"
    assert spy.execute("op-1", publish) == "remote-result"
    assert executions == ["published"]
    assert spy.execution_count("op-1") == 1
    assert [call.executed for call in spy.calls] == [True, False]
    spy.assert_at_most_once()


def test_external_effect_spy_is_at_most_once_under_concurrent_retries() -> None:
    spy: ExternalEffectSpy[str] = ExternalEffectSpy()
    start = Barrier(8)
    executions: list[str] = []

    def publish() -> str:
        executions.append("published")
        time.sleep(0.01)
        return "remote-result"

    def retry() -> str:
        start.wait(timeout=2)
        return spy.execute("op-concurrent", publish)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: retry(), range(8)))

    assert results == ["remote-result"] * 8
    assert executions == ["published"]
    assert spy.execution_count("op-concurrent") == 1
    assert sum(not call.executed for call in spy.calls) == 7
    spy.assert_at_most_once()


def test_external_effect_spy_allows_retry_after_failed_callback() -> None:
    """A failed effect is not cached as a successful external result."""
    spy: ExternalEffectSpy[str] = ExternalEffectSpy()
    attempts = 0

    def publish() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary sink failure")
        return "remote-result"

    with pytest.raises(RuntimeError, match="temporary sink failure"):
        spy.execute("op-retry-after-failure", publish)
    assert spy.execute("op-retry-after-failure", publish) == "remote-result"
    assert attempts == 2
    assert spy.execution_count("op-retry-after-failure") == 1
    assert [call.executed for call in spy.calls] == [True]


def test_external_effect_spy_rejects_missing_operation_identity() -> None:
    spy: ExternalEffectSpy[str] = ExternalEffectSpy()

    with pytest.raises(ValueError, match="operation_key"):
        spy.execute("", lambda: "must-not-run")
    assert spy.calls == ()


def test_expired_canonical_attempt_routes_to_lifecycle_recovery(
    tmp_path: Path,
) -> None:
    """Recovery decisions must read the canonical attempt, not a shadow file."""
    store = TaskAttemptStore(tmp_path / "task_attempts.json")
    created = store.ensure_for_dispatch(
        run_id="RUN-S4-RECOVER",
        task_id="TASK-S4-RECOVER",
        dispatch_id="dispatch-recover",
        role="dev",
        instance_id="dev-recover",
        operation_id="operation-recover",
        briefing_ref="briefing.md",
        created_at="2026-07-06T20:00:00+00:00",
        lease_expires_at="2026-07-06T20:10:00+00:00",
        max_attempts=3,
    )
    updated = store.update(
        created.attempt["attempt_id"],
        status="sent",
        updated_at="2026-07-06T20:10:00+00:00",
        retryable=True,
    )
    assert updated is not None

    actions = pending_task_attempt_recovery_actions(
        {},
        canonical_store_path=store.path,
        now=datetime(2026, 7, 6, 20, 40, tzinfo=timezone.utc),
        lease_grace_s=900,
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "worker-lifecycle-recover"
    assert action["workflow_run_id"] == "RUN-S4-RECOVER"
    assert action["task_id"] == "TASK-S4-RECOVER"
    assert action["preflight"]["status"] == "passed"


def test_store_replace_failure_preserves_last_complete_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    path = state_dir / "kanban.json"
    store = TaskStore(path)
    store.add(Task(id="TASK-S4-ATOMIC", title="atomic", status="backlog"))
    original_replace = atomic_io.os.replace

    def fail_replace(source, target):  # noqa: ANN001
        if Path(target) == path:
            raise InjectedFailure("before_store_atomic_replace")
        return original_replace(source, target)

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)
    with pytest.raises(InjectedFailure, match="before_store_atomic_replace"):
        store.update("TASK-S4-ATOMIC", status="in_progress")

    reloaded = TaskStore(path)
    assert reloaded.get("TASK-S4-ATOMIC").status == "backlog"
    assert not list(state_dir.glob("kanban.json.*.tmp"))


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--runtime-child":
    _runtime_dispatch_child(Path(sys.argv[2]), sys.argv[3])
