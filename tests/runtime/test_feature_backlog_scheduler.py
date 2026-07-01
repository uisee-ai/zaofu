from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    ContractDConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    VerificationConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _StubTransport:
    def __init__(self) -> None:
        self.sends: list[str] = []

    def send_task(self, role_name: str, briefing_path: Path, prompt: str) -> None:
        self.sends.append(role_name)

    def is_alive(self, role_name: str) -> bool:
        return True

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""


def _config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(
                name="dev",
                backend="mock",
                replicas=4,
                triggers=["task.assigned"],
                publishes=["dev.build.done"],
            ),
            RoleConfig(
                name="test",
                backend="mock",
                replicas=4,
                triggers=["review.approved"],
                publishes=["test.passed"],
            ),
        ],
        workflow=WorkflowConfig(),
    )


def test_contract_ready_backlog_fills_dev_replica_pool(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    for i in range(4):
        store.add(Task(
            id=f"T{i}",
            title=f"task {i}",
            status="backlog",
            contract=TaskContract(
                behavior=f"behavior {i}",
                verification="pytest",
                owner_role="dev",
            ),
        ))
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="session.started", actor="zf-cli")
    )

    transport = _StubTransport()
    orch = Orchestrator(state_dir, _config(state_dir), transport)
    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]

    assert len(decisions) == 4
    assert set(transport.sends) == {"dev-1", "dev-2", "dev-3", "dev-4"}
    tasks = store.list_all()
    assert {task.assigned_to for task in tasks} == {
        "dev-1",
        "dev-2",
        "dev-3",
        "dev-4",
    }
    assert all(task.status == "in_progress" for task in tasks)
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert sum(e.type == "task.assigned" for e in events) == 4
    assert sum(e.type == "task.dispatched" for e in events) == 4


def test_backlog_scheduler_yields_requeued_task_with_progress(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="requeued after build",
        status="backlog",
        contract=TaskContract(
            behavior="behavior",
            verification="pytest",
            owner_role="dev",
        ),
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="T1",
        payload={"dispatch_id": "disp-dev"},
    ))
    log.append(ZfEvent(
        type="task.requeued",
        actor="zf-cli",
        task_id="T1",
        payload={"source": "graceful_stop_inflight_cleanup"},
    ))

    transport = _StubTransport()
    orch = Orchestrator(state_dir, _config(state_dir), transport)
    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]

    assert decisions == []
    assert transport.sends == []
    task = store.get("T1")
    assert task is not None
    assert task.status == "backlog"
    assert not task.assigned_to
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not any(
        event.type == "task.assigned"
        and event.payload.get("source") == "feature_backlog_scheduler"
        for event in events
    )


def test_backlog_scheduler_does_not_bind_assignment_without_worker(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="ready but no dev worker",
        status="backlog",
        contract=TaskContract(
            behavior="behavior",
            verification="pytest",
            owner_role="dev",
        ),
    ))
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="session.started", actor="zf-cli")
    )
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(),
        roles=[RoleConfig(name="orchestrator", backend="mock")],
        workflow=WorkflowConfig(),
    )

    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)
    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]

    assert decisions == []
    assert transport.sends == []
    task = store.get("T1")
    assert task is not None
    assert task.status == "backlog"
    assert not task.assigned_to
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not any(event.type == "task.assigned" for event in events)
    assert any(
        event.type == "orchestrator.dispatch_skipped"
        and event.payload.get("reason") == "no_available_role"
        for event in events
    )


def test_assigned_test_backlog_fills_test_replica_pool(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    for i in range(4):
        store.add(Task(
            id=f"T{i}",
            title=f"task {i}",
            status="backlog",
            assigned_to="test",
        ))
    log = EventLog(state_dir / "events.jsonl")
    for i in range(4):
        log.append(ZfEvent(
            type="task.assigned",
            actor="orchestrator",
            task_id=f"T{i}",
            payload={"assignee": "test", "role": "test"},
        ))

    transport = _StubTransport()
    orch = Orchestrator(state_dir, _config(state_dir), transport)
    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]

    assert len(decisions) == 4
    assert set(transport.sends) == {"test-1", "test-2", "test-3", "test-4"}


def test_exclusive_files_do_not_dispatch_concurrently(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    for task_id in ("T1", "T2"):
        store.add(Task(
            id=task_id,
            title=task_id,
            status="backlog",
            contract=TaskContract(
                behavior=f"behavior {task_id}",
                verification="pytest",
                owner_role="dev",
                exclusive_files=["src/shared.py"],
            ),
        ))
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="session.started", actor="zf-cli")
    )

    transport = _StubTransport()
    orch = Orchestrator(state_dir, _config(state_dir), transport)
    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert len(transport.sends) == 1
    tasks = {task.id: task for task in store.list_all()}
    in_progress = [task for task in tasks.values() if task.status == "in_progress"]
    assert [task.id for task in in_progress] == ["T1"]
    assert tasks["T2"].status == "backlog"
    skipped = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "orchestrator.dispatch_skipped"
    ]
    assert skipped
    assert skipped[-1].payload["reason"].startswith("exclusive_files_conflict:")


def test_wave_scheduler_dispatches_lower_wave_first(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T2",
        title="wave 2",
        status="backlog",
        contract=TaskContract(
            behavior="behavior 2",
            verification="pytest",
            owner_role="dev",
            wave=2,
        ),
    ))
    store.add(Task(
        id="T1",
        title="wave 1",
        status="backlog",
        contract=TaskContract(
            behavior="behavior 1",
            verification="pytest",
            owner_role="dev",
            wave=1,
        ),
    ))
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="session.started", actor="zf-cli")
    )

    transport = _StubTransport()
    orch = Orchestrator(state_dir, _config(state_dir), transport)
    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]

    assert [decision.task_id for decision in decisions] == ["T1"]
    tasks = {task.id: task for task in store.list_all()}
    assert tasks["T1"].status == "in_progress"
    assert tasks["T2"].status == "backlog"
    skipped = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "orchestrator.dispatch_skipped"
    ]
    assert skipped
    assert skipped[-1].payload["reason"].startswith("wave_blocked:")


def test_strict_contract_preflight_blocks_bad_backlog_task(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="bad contract",
        status="backlog",
        contract=TaskContract(
            behavior="do it",
            verification="",
            owner_role="dev",
        ),
    ))
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="session.started", actor="zf-cli")
    )
    config = _config(state_dir)
    config.verification = VerificationConfig(
        contract=ContractDConfig(required=True),
    )

    transport = _StubTransport()
    orch = Orchestrator(state_dir, config, transport)
    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]
    second_decisions = orch._dispatch_ready()  # type: ignore[attr-defined]

    assert decisions == []
    assert second_decisions == []
    assert transport.sends == []
    task = store.get("T1")
    assert task is not None
    assert task.status == "backlog"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(event.type == "task.contract.invalid" for event in events)
    assert sum(event.type == "task.contract.invalid" for event in events) == 1
    assert sum(event.type == "orchestrator.dispatch_skipped" for event in events) == 1
