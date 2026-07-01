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
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _StubTransport:
    def send_task(self, role_name: str, briefing_path: Path, prompt: str) -> None:
        pass

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
                name="judge",
                backend="mock",
                publishes=["judge.passed", "judge.failed"],
            ),
        ],
        workflow=WorkflowConfig(),
        verification=VerificationConfig(
            contract=ContractDConfig(dispatch_token_required=True),
        ),
    )


def _layer1_config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(),
        roles=[
            RoleConfig(
                name="judge",
                backend="mock",
                publishes=["judge.passed", "judge.failed"],
            ),
        ],
        workflow=WorkflowConfig(),
        verification=VerificationConfig(
            contract=ContractDConfig(dispatch_token_required=True),
        ),
    )


def test_late_judge_success_after_orphan_closes_same_dispatch(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T1",
        title="late success",
        status="backlog",
        active_dispatch_id="disp-good",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.orphaned",
        actor="zf-cli",
        task_id="T1",
        payload={"assigned_to": "judge"},
    ))
    log.append(ZfEvent(
        type="judge.passed",
        actor="judge",
        task_id="T1",
        payload={"dispatch_id": "disp-good"},
    ))

    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())
    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert any(d.action == "move" and d.task_id == "T1" for d in decisions)
    task = TaskStore(state_dir / "kanban.json").get("T1")
    assert task is not None
    assert task.status == "done"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(e.type == "task.late_success.reconciled" for e in events)


def test_late_judge_success_with_stale_dispatch_is_not_closed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T1",
        title="late success",
        status="backlog",
        active_dispatch_id="disp-new",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.orphaned",
        actor="zf-cli",
        task_id="T1",
        payload={"assigned_to": "judge"},
    ))
    log.append(ZfEvent(
        type="judge.passed",
        actor="judge",
        task_id="T1",
        payload={"dispatch_id": "disp-old"},
    ))

    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())
    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert not decisions
    task = TaskStore(state_dir / "kanban.json").get("T1")
    assert task is not None
    assert task.status == "backlog"


def test_duplicate_terminal_event_replays_from_dispatch_ledger(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="terminal replay",
        status="testing",
        assigned_to="judge",
        active_dispatch_id="disp-good",
    ))
    orch = Orchestrator(state_dir, _layer1_config(state_dir), _StubTransport())
    first = ZfEvent(
        type="judge.passed",
        actor="judge",
        task_id="T1",
        payload={"dispatch_id": "disp-good"},
    )
    second = ZfEvent(
        type="judge.passed",
        actor="judge",
        task_id="T1",
        payload={"dispatch_id": "disp-good"},
    )

    first_decisions = orch.run_once([first])
    second_decisions = orch.run_once([second])

    assert any(d.action == "move" for d in first_decisions)
    assert any(d.action == "skip" for d in second_decisions)
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert sum(e.type == "dispatch.terminal.recorded" for e in events) == 1
    assert sum(e.type == "dispatch.terminal.replayed" for e in events) == 1
    assert sum(e.type == "task.status_changed" for e in events) == 1


def test_stale_terminal_event_records_dispatch_rejection(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T1",
        title="stale terminal",
        status="testing",
        assigned_to="judge",
        active_dispatch_id="disp-new",
    ))
    orch = Orchestrator(state_dir, _layer1_config(state_dir), _StubTransport())

    decisions = orch.run_once([
        ZfEvent(
            type="judge.passed",
            actor="judge",
            task_id="T1",
            payload={"dispatch_id": "disp-old"},
        ),
    ])

    assert decisions and decisions[0].action == "block"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(e.type == "dispatch.terminal.rejected" for e in events)
