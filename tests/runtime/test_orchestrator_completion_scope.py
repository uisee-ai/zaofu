from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator_reactor import EventReactorMixin


class _ScopeReactor(EventReactorMixin):
    def __init__(self, *, cfg: ZfConfig, state_dir: Path) -> None:
        self.config = cfg
        self.event_log = EventLog(state_dir / "events.jsonl")
        self.task_store = TaskStore(state_dir / "kanban.json")
        self.project_root = state_dir
        self._event_writer = EventWriter(self.event_log)
        self.event_registry = self._build_event_registry()

    def _move_task(
        self,
        task_id: str,
        to_status: str,
        *,
        trigger_event: str = "",
    ) -> bool:
        task = self.task_store.get(task_id)
        if task is None:
            return False
        self.task_store.update(task_id, status=to_status)
        return True


def test_static_gate_passed_has_executable_graph_handler(tmp_path: Path) -> None:
    cfg = ZfConfig(
        project=ProjectConfig(name="completion-scope"),
        session=SessionConfig(tmux_session="completion-scope"),
        roles=[
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                publishes=["dev.build.done"],
            ),
            RoleConfig(
                name="review-lane-0",
                backend="mock",
                triggers=["static_gate.passed"],
                publishes=["review.approved"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_review_test_judge_reconcile=True,
            ),
        ),
    )
    reactor = _ScopeReactor(cfg=cfg, state_dir=tmp_path)
    entries = reactor.event_registry.resolve("static_gate.passed")

    assert entries
    assert entries[0].source == "workflow_graph_event_handler"


def test_completion_event_replay_records_action_not_out_of_scope(
    tmp_path: Path,
) -> None:
    cfg = ZfConfig(
        project=ProjectConfig(name="completion-scope"),
        session=SessionConfig(tmux_session="completion-scope"),
        roles=[
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                publishes=["dev.build.done"],
            ),
            RoleConfig(
                name="review-lane-0",
                backend="mock",
                triggers=["static_gate.passed"],
                publishes=["review.approved"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_review_test_judge_reconcile=True,
            ),
        ),
    )
    reactor = _ScopeReactor(cfg=cfg, state_dir=tmp_path)
    reactor.task_store.add(Task(
        id="CJMIN-PI-CORE-001",
        title="pi core",
        status="in_progress",
        assigned_to="dev-lane-0",
    ))
    gate = reactor.event_writer.append(ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="CJMIN-PI-CORE-001",
    ))

    decision = reactor.event_registry.primary("static_gate.passed")(gate)  # type: ignore[operator]

    assert decision is not None
    assert decision.action == "assign"
    assert "out_of_scope" not in decision.reason
