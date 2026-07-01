from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _StubTransport:
    def __init__(self) -> None:
        self.sends: list[str] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sends.append(role_name)

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):  # noqa: ANN001
        return []


def _make_orchestrator(
    tmp_path: Path,
) -> tuple[Orchestrator, TaskStore, FeatureStore, EventLog, _StubTransport]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")

    transport = _StubTransport()
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(
                name="dev",
                backend="mock",
                publishes=["dev.build.done"],
                triggers=["task.assigned"],
            ),
        ],
    )
    return (
        Orchestrator(state_dir, config, transport),  # type: ignore[arg-type]
        TaskStore(state_dir / "kanban.json"),
        FeatureStore(state_dir / "feature_list.json"),
        EventLog(state_dir / "events.jsonl"),
        transport,
    )


def test_feature_liveness_closes_active_feature_when_all_tasks_terminal(
    tmp_path: Path,
) -> None:
    orch, task_store, feature_store, log, _transport = _make_orchestrator(tmp_path)
    feature_store.add(Feature(id="F-ABC12345", title="Feature", status="active"))
    task_store.add(Task(
        id="T1",
        title="T1",
        key="F-ABC12345:part-a",
        status="in_progress",
        assigned_to="dev",
    ))
    task_store.update("T1", status="done")

    decisions = orch.run_once()

    assert feature_store.get("F-ABC12345").status == "done"  # archived lookup
    assert any(
        event.type == "feature.status_changed"
        and event.task_id == "F-ABC12345"
        and event.payload.get("source") == "feature_liveness_sweep"
        for event in log.read_all()
    )
    assert any(decision.action == "move" for decision in decisions)


def test_feature_liveness_blocks_active_feature_without_tasks(
    tmp_path: Path,
) -> None:
    orch, _task_store, feature_store, log, _transport = _make_orchestrator(tmp_path)
    feature_store.add(Feature(id="F-ABC12345", title="Feature", status="active"))

    decisions = orch.run_once()
    orch.run_once()

    block_events = [
        event for event in log.read_all()
        if event.type == "feature.liveness.blocked"
    ]
    assert len(block_events) == 1
    assert block_events[0].payload.get("reason") == "active feature has no linked tasks"
    assert any(event.type == "human.escalate" for event in log.read_all())
    assert any(decision.action == "block" for decision in decisions)


def test_feature_liveness_does_not_block_ready_task(tmp_path: Path) -> None:
    orch, task_store, feature_store, log, transport = _make_orchestrator(tmp_path)
    feature_store.add(Feature(id="F-ABC12345", title="Feature", status="active"))
    task_store.add(Task(
        id="T1",
        title="T1",
        key="F-ABC12345:part-a",
        status="backlog",
    ))

    orch.run_once()

    assert transport.sends == ["dev"]
    assert not any(
        event.type == "feature.liveness.blocked"
        for event in log.read_all()
    )
