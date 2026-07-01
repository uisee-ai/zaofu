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


class _CompletionReactor(EventReactorMixin):
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
        self.event_writer.append(ZfEvent(
            type="task.status_changed",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "from": task.status,
                "to": to_status,
                "source": "test_completion_liveness",
                "trigger_event": trigger_event,
            },
        ))
        return True


def _lane_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="completion-liveness"),
        session=SessionConfig(tmux_session="completion-liveness"),
        roles=[
            RoleConfig(
                name="dev-lane-3",
                backend="mock",
                publishes=["dev.build.done"],
            ),
            RoleConfig(
                name="review-lane-3",
                backend="mock",
                triggers=["static_gate.passed"],
                publishes=["review.approved", "review.rejected"],
            ),
            RoleConfig(
                name="verify-lane-3",
                backend="mock",
                triggers=["review.approved"],
                publishes=["verify.passed", "verify.failed"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_review_test_judge_reconcile=True,
            ),
        ),
    )


def test_static_gate_passed_dispatches_same_lane_review(tmp_path: Path) -> None:
    reactor = _CompletionReactor(cfg=_lane_config(), state_dir=tmp_path)
    reactor.task_store.add(Task(
        id="CJMIN-GATEWAY-001",
        title="gateway",
        status="in_progress",
        assigned_to="dev-lane-3",
    ))
    gate = reactor.event_writer.append(ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="CJMIN-GATEWAY-001",
        payload={"trigger_event_id": "evt-dev"},
    ))
    handler = reactor.event_registry.primary("static_gate.passed")

    assert handler is not None
    decision = handler(gate)

    task = reactor.task_store.get("CJMIN-GATEWAY-001")
    assert decision is not None
    assert decision.action == "assign"
    assert decision.role == "review-lane-3"
    assert task is not None
    assert task.status == "review"
    assert task.assigned_to == "review-lane-3"
    assert any(
        event.type == "task.assigned"
        and event.payload.get("trigger_event_id") == gate.id
        for event in reactor.event_log.read_all()
    )


def test_design_critique_done_uses_workflow_next_role(tmp_path: Path) -> None:
    cfg = ZfConfig(
        project=ProjectConfig(name="completion-design"),
        session=SessionConfig(tmux_session="completion-design"),
        roles=[
            RoleConfig(
                name="critic",
                backend="mock",
                publishes=["design.critique.done"],
            ),
            RoleConfig(
                name="refactor-plan-synth",
                backend="mock",
                triggers=["design.critique.done"],
                publishes=["zaofu.refactor.plan.ready"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_review_test_judge_reconcile=True,
            ),
        ),
    )
    reactor = _CompletionReactor(cfg=cfg, state_dir=tmp_path)
    reactor.task_store.add(Task(
        id="CJMIN-STATE-001",
        title="state",
        status="in_progress",
        assigned_to="critic",
    ))
    critique = reactor.event_writer.append(ZfEvent(
        type="design.critique.done",
        actor="critic",
        task_id="CJMIN-STATE-001",
        payload={"verdict": "approved"},
    ))

    decision = reactor._on_build_done(critique)

    task = reactor.task_store.get("CJMIN-STATE-001")
    assert decision is not None
    assert decision.action == "assign"
    assert decision.role == "refactor-plan-synth"
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "refactor-plan-synth"
