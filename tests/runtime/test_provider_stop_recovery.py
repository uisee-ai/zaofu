from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _StubTransport:
    def __init__(self) -> None:
        self.sends: list[tuple[str, Path, str]] = []

    def send_task(self, role_name: str, briefing_path: Path, prompt: str) -> None:
        self.sends.append((role_name, briefing_path, prompt))
        pass

    def is_alive(self, role_name: str) -> bool:
        return True

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""


def _config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
        ],
    )


def _arch_config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="arch",
                backend="mock",
                instance_id="arch",
                publishes=[
                    "artifact.manifest.published",
                    "arch.proposal.done",
                ],
            ),
        ],
    )


def test_completed_without_terminal_event_requeues_task(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="recover",
        status="in_progress",
        assigned_to="dev",
    ))
    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())

    decision = orch._on_codex_hook_stop(ZfEvent(  # type: ignore[attr-defined]
        type="codex.hook.stop",
        actor="dev",
        task_id="T1",
        payload={"provider_stop_reason": "completed_without_terminal_event"},
    ))

    assert decision is not None
    assert decision.action == "dispatch"
    task = store.get("T1")
    assert task is not None
    assert task.status == "backlog"
    assert task.assigned_to == "dev"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(e.type == "provider.stop.recovery" for e in events)


def test_completed_without_terminal_event_after_green_check_requests_terminal(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="recover",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-dev",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={"role": "dev", "assignee": "dev", "dispatch_id": "disp-dev"},
    ))
    log.append(ZfEvent(
        type="codex.hook.post_tool_use",
        actor="dev",
        task_id="T1",
        payload={
            "tool_input": {
                "command": "python3 -m pytest tests/e2e/test_calc_e2e.py -q",
            },
            "tool_response": "Process exited with code 0\n3 passed in 0.21s\n",
        },
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, _config(state_dir), transport)

    decision = orch._on_codex_hook_stop(ZfEvent(  # type: ignore[attr-defined]
        type="codex.hook.stop",
        actor="dev",
        task_id="T1",
        payload={"provider_stop_reason": "completed_without_terminal_event"},
    ))

    assert decision is not None
    assert decision.action == "recover"
    task = store.get("T1")
    assert task is not None
    assert task.status == "in_progress"
    events = EventLog(state_dir / "events.jsonl").read_all()
    recovered = [e for e in events if e.type == "worker.stuck.recovered"]
    assert recovered[-1].payload["recovery_action"] == (
        "terminal_completion_requested_after_green_verification"
    )
    assert recovered[-1].payload["expected_event"] == "dev.build.done"
    assert transport.sends


def test_completed_without_terminal_event_after_manifest_requests_terminal(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="plan",
        status="in_progress",
        assigned_to="arch",
        active_dispatch_id="disp-plan",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={"role": "arch", "assignee": "arch", "dispatch_id": "disp-plan"},
    ))
    log.append(ZfEvent(
        type="artifact.manifest.published",
        actor="arch",
        task_id="T1",
        payload={
            "manifest": {
                "task_id": "T1",
                "role": "arch",
                "artifact_refs": [
                    {
                        "kind": "spec",
                        "path": "docs/specs/demo.md",
                        "sha256": "a" * 64,
                        "summary": "demo spec",
                    }
                ],
            }
        },
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, _arch_config(state_dir), transport)

    decision = orch._on_codex_hook_stop(ZfEvent(  # type: ignore[attr-defined]
        type="codex.hook.stop",
        actor="arch",
        task_id="T1",
        payload={"provider_stop_reason": "completed_without_terminal_event"},
    ))

    assert decision is not None
    assert decision.action == "recover"
    task = store.get("T1")
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "arch"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not any(e.type == "provider.stop.recovery" for e in events)
    recovered = [e for e in events if e.type == "worker.stuck.recovered"]
    assert recovered[-1].payload["recovery_action"] == "terminal_completion_requested"
    assert recovered[-1].payload["expected_event"] == "arch.proposal.done"
    assert transport.sends


def test_auth_error_suspends_task_for_operator(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="recover",
        status="in_progress",
        assigned_to="dev",
    ))
    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())

    decision = orch._on_agent_api_blocked(ZfEvent(  # type: ignore[attr-defined]
        type="agent.api_blocked",
        actor="dev",
        task_id="T1",
        payload={"provider_stop_reason": "auth_error"},
    ))

    assert decision is not None
    assert decision.action == "block"
    task = store.get("T1")
    assert task is not None
    assert task.status == "blocked"
    assert task.blocked_reason == "provider_stop:auth_error"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(e.type == "provider.stop.recovery" for e in events)
    assert any(e.type == "human.escalate" for e in events)
