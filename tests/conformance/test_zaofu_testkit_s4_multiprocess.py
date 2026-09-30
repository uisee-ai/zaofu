"""S4 process-level concurrency for the canonical dispatch boundary."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport

from tests.testkit.cleanup import assert_no_child_process_leaks, child_processes

class DuplicateDispatch(AssertionError):
    """Only the known duplicate-dispatch verdict may be expected to fail."""


def _run_orchestrator_once(
    state_dir_text: str,
    project_root_text: str,
    result_queue,
    dispatch_barrier,
) -> None:
    """Run one independent kernel instance against the shared state root."""
    state_dir = Path(state_dir_text)
    project_root = Path(project_root_text)
    config = ZfConfig(
        project=ProjectConfig(name="s4-multiprocess"),
        session=SessionConfig(tmux_session="s4-multiprocess"),
        roles=[RoleConfig(name="dev", backend="mock", stages=["implement"])],
    )
    try:
        orchestrator = Orchestrator(
            state_dir,
            config,
            TmuxTransport(TmuxSession(
                session_name="s4-multiprocess",
                dry_run=True,
            )),
            project_root=project_root,
        )
        original_dispatch = orchestrator._dispatch_task

        def synchronized_dispatch(*args, **kwargs):
            # Both real run_once paths must reach dispatch eligibility before
            # either can publish a claim. No state/result is replaced here.
            dispatch_barrier.wait(timeout=10)
            return original_dispatch(*args, **kwargs)

        orchestrator._dispatch_task = synchronized_dispatch
        decisions = orchestrator.run_once()
        result_queue.put({
            "dispatches": [
                item.task_id
                for item in decisions
                if item.action == "dispatch"
            ],
            "error": "",
        })
    except BaseException as exc:  # pragma: no cover - parent reports details
        result_queue.put({"dispatches": [], "error": repr(exc)})
        raise


@pytest.mark.xfail(
    strict=True,
    raises=DuplicateDispatch,
    reason="ZF-TEST-003: independent Kernel processes can duplicate dispatch",
)
@pytest.mark.parametrize(
    "process_count",
    [2, 4, 8, 16, 32],
    ids=[
        "two-process",
        "four-process",
        "eight-process",
        "sixteen-process",
        "thirty-two-process",
    ],
)
def test_kernel_processes_accept_one_dispatch_for_shared_task(
    tmp_path: Path,
    process_count: int,
) -> None:
    """Concurrent wakeups must not create duplicate dispatch side effects."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    SessionStore(state_dir / "session.yaml").update(runtime_state="active")
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-S4-MP",
        title="process race",
        status="backlog",
        assigned_to="dev",
    ))

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    dispatch_barrier = context.Barrier(process_count)
    # Queue/Barrier may start Python's resource tracker; capture it as part
    # of the harness baseline so only processes owned by this case count.
    child_baseline = child_processes()
    processes = [
        context.Process(
            target=_run_orchestrator_once,
            args=(str(state_dir), str(tmp_path), result_queue, dispatch_barrier),
        )
        for _ in range(process_count)
    ]
    try:
        for process in processes:
            process.start()
        # The 32-process variant can take longer to schedule when the full
        # TestKit runs concurrently with other subprocess cases.  This is a
        # harness collection window, not the product's dispatch timeout.
        join_timeout = 45 if process_count >= 32 else 20
        for process in processes:
            process.join(timeout=join_timeout)
        assert [process.exitcode for process in processes] == [0] * process_count
        results = [result_queue.get(timeout=5) for _ in processes]
        assert all(not result["error"] for result in results), results
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()
        assert_no_child_process_leaks(child_baseline)

    log = EventLog(state_dir / "events.jsonl")
    try:
        events = log.read_all()
    finally:
        log.close()
    dispatches = [
        event for event in events
        if event.type == "task.dispatched" and event.task_id == "TASK-S4-MP"
    ]
    decisions_count = sum(
        result["dispatches"].count("TASK-S4-MP")
        for result in results
    )
    assert TaskStore(state_dir / "kanban.json").get("TASK-S4-MP").status == (
        "in_progress"
    )
    assert len({event.id for event in events}) == len(events)
    if len(dispatches) > 1 or decisions_count > 1:
        raise DuplicateDispatch(
            f"dispatch events={len(dispatches)}, dispatch decisions={decisions_count}"
        )
    assert len(dispatches) == 1
    assert decisions_count == 1
