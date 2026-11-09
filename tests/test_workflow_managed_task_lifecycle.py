from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.sidecar_refs import write_sidecar_text
from zf.runtime.workflow_anchor import (
    WORKFLOW_TASK_REQUEST_ROTATION_SOURCE,
    bind_workflow_request_to_task,
    mark_workflow_managed_task,
)
from zf.runtime.workflow_task_lifecycle import (
    RESEARCH_TASK_COMPLETION_SOURCE,
    WORKFLOW_TASK_ACTIVATION_SOURCE,
    WORKFLOW_TASK_TERMINAL_SOURCE,
    activate_workflow_managed_task,
    complete_standalone_research_task,
    reopen_workflow_managed_task_from_run_recovery,
    settle_workflow_managed_task_from_run_terminal,
)


class _RecordingTransport:
    def send_task(
        self,
        role_name,
        briefing_path,
        prompt,
        *,
        context=None,
    ):  # noqa: ANN001
        return None

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="workflow-task-lifecycle"),
        roles=[
            RoleConfig(
                name="research-reader",
                backend="mock",
                role_kind="reader",
            ),
        ],
        workflow=WorkflowConfig(
            stages=[
                WorkflowStageConfig(
                    id="research-fanout",
                    trigger="workflow.invoke.requested",
                    topology="fanout_reader",
                    roles=["research-reader"],
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        success_event="research.approved",
                        failure_event="research.rejected",
                    ),
                ),
            ],
        ),
    )


def _runtime(
    tmp_path: Path,
    *,
    workflow_managed: bool,
) -> tuple[Orchestrator, EventLog, TaskStore]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    task = Task(id="TASK-RESEARCH", title="Research the delivery decision")
    if workflow_managed:
        mark_workflow_managed_task(task)
    store.add(task)
    log = EventLog(state_dir / "events.jsonl")
    runtime = Orchestrator(
        state_dir,
        _config(),
        _RecordingTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    return runtime, log, store


def _invoke() -> ZfEvent:
    return ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-RESEARCH",
        correlation_id="run-research-1",
        payload={
            "task_id": "TASK-RESEARCH",
            "pattern_id": "research-fanout",
            "workflow_run_id": "run-research-1",
            "requested_by": "kanban-agent",
            "reason": "approved research start",
        },
    )


def test_accepted_workflow_activates_managed_task_once(
    tmp_path: Path,
) -> None:
    runtime, log, store = _runtime(tmp_path, workflow_managed=True)
    store.update("TASK-RESEARCH", assigned_to="research-reader")
    invoke = _invoke()

    runtime.run_once(events=[invoke])

    task = store.get("TASK-RESEARCH")
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to is None
    events = log.read_all()
    accepted = next(
        event
        for event in events
        if event.type == "workflow.invoke.accepted"
    )
    status_events = [
        event
        for event in events
        if event.type == "task.status_changed"
        and event.payload.get("source")
        == WORKFLOW_TASK_ACTIVATION_SOURCE
    ]
    assert len(status_events) == 1
    assert status_events[0].causation_id == accepted.id
    assert status_events[0].payload["previous_assigned_to"] == "research-reader"
    assert status_events[0].payload["assignment_released"] is True
    assert task.started_at == accepted.ts

    store.update("TASK-RESEARCH", status="backlog")
    restarted = Orchestrator(
        runtime.state_dir,
        _config(),
        _RecordingTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    restarted.run_once(events=[invoke])

    assert store.get("TASK-RESEARCH").status == "in_progress"  # type: ignore[union-attr]
    assert sum(
        event.type == "task.status_changed"
        and event.payload.get("source")
        == WORKFLOW_TASK_ACTIVATION_SOURCE
        for event in log.read_all()
    ) == 1


def test_accepted_workflow_does_not_take_over_ordinary_task(
    tmp_path: Path,
) -> None:
    runtime, log, store = _runtime(tmp_path, workflow_managed=False)

    runtime.run_once(events=[_invoke()])

    assert store.get("TASK-RESEARCH").status == "in_progress"  # type: ignore[union-attr]
    assert not any(
        event.type == "task.status_changed"
        and event.payload.get("source")
        == WORKFLOW_TASK_ACTIVATION_SOURCE
        for event in log.read_all()
    )


def test_blocked_task_requires_terminal_request_rotation_to_reactivate(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    origin_digest = "sha256:" + "a" * 64
    task = bind_workflow_request_to_task(
        mark_workflow_managed_task(Task(
            id="TASK-ROTATE",
            title="Retry blocked workflow",
            status="blocked",
            blocked_reason="prior run blocked",
        )),
        request_id="REQ-NEW",
        request_revision=1,
        origin_binding_digest=origin_digest,
    )
    store.add(task)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    terminal = writer.emit(
        "run.goal.blocked",
        actor="orchestrator",
        task_id=task.id,
        correlation_id="REQ-OLD",
        payload={
            "request_id": "REQ-OLD",
            "run_id": "RUN-OLD",
            "workflow_run_id": "RUN-OLD",
            "reason": "prior run blocked",
        },
    )
    accepted = ZfEvent(
        type="workflow.invoke.accepted",
        actor="zf-cli",
        task_id=task.id,
        correlation_id="REQ-NEW",
        payload={
            "task_id": task.id,
            "request_id": "REQ-NEW",
            "request_revision": 1,
            "workflow_run_id": "RUN-NEW",
            "pattern_id": "issue-flow",
        },
    )

    assert activate_workflow_managed_task(
        task_store=store,
        event_writer=writer,
        accepted_event=accepted,
    ) is None
    assert store.get(task.id).status == "blocked"  # type: ignore[union-attr]

    rotation = writer.emit(
        "task.contract.update",
        actor="operator",
        task_id=task.id,
        payload={
            "source": WORKFLOW_TASK_REQUEST_ROTATION_SOURCE,
            "request_id": "REQ-NEW",
            "request_revision": 1,
            "origin_binding_digest": origin_digest,
            "prior_request_id": "REQ-OLD",
            "prior_request_revision": 2,
            "prior_run_id": "RUN-OLD",
            "prior_terminal_event_id": terminal.id,
            "prior_terminal_type": terminal.type,
        },
    )
    updated = activate_workflow_managed_task(
        task_store=store,
        event_writer=writer,
        accepted_event=accepted,
    )

    assert updated is not None
    assert updated.status == "in_progress"
    assert updated.blocked_reason == ""
    status_event = next(
        event
        for event in writer.event_log.read_all()
        if event.type == "task.status_changed"
        and event.payload.get("source") == WORKFLOW_TASK_ACTIVATION_SOURCE
    )
    assert status_event.payload["from"] == "blocked"
    assert status_event.payload["workflow_request_rotation_event_id"] == (
        rotation.id
    )


def _research_result(
    tmp_path: Path,
    *,
    request_bound: bool = False,
) -> tuple[TaskStore, EventWriter, ZfEvent]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    store = TaskStore(state_dir / "kanban.json")
    task = mark_workflow_managed_task(Task(
        id="TASK-RESEARCH",
        title="Standalone research",
        status="in_progress",
    ))
    if request_bound:
        task = bind_workflow_request_to_task(
            task,
            request_id="REQ-1",
            request_revision=1,
            origin_binding_digest="a" * 64,
        )
    store.add(task)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    descriptor = write_sidecar_text(
        state_dir,
        "artifacts/research/TASK-RESEARCH/result.md",
        "# Research\n\nComplete.\n",
        kind="research_report",
        schema_version="research-report.v1",
        created_by="test",
        required=True,
    )
    descriptor.update({
        "task_id": task.id,
        "stage_id": "research-adaptive",
        "fanout_id": "fanout-1",
        "workflow_run_id": "run-1",
    })
    terminal = writer.emit(
        "fanout.aggregate.completed",
        actor="zf-cli",
        task_id=task.id,
        correlation_id="run-1",
        payload={
            "fanout_id": "fanout-1",
            "stage_id": "research-adaptive",
            "status": "completed",
            "artifact_refs": [descriptor],
        },
    )
    result = writer.emit(
        "workflow.result.available",
        actor="zf-cli",
        task_id=task.id,
        causation_id=terminal.id,
        correlation_id="run-1",
        payload={
            "schema_version": "workflow-result.v1",
            "result_kind": "research_report",
            "status": "available",
            "task_id": task.id,
            "workflow_run_id": "run-1",
            "terminal_event_id": terminal.id,
            "artifact_ref": descriptor["ref"],
            "artifact_digest": descriptor["sha256"],
        },
    )
    return store, writer, result


def test_standalone_research_closes_once_after_bound_artifact(
    tmp_path: Path,
) -> None:
    store, writer, result = _research_result(tmp_path)

    first = complete_standalone_research_task(
        task_store=store,
        event_writer=writer,
        result_event=result,
    )
    replay = complete_standalone_research_task(
        task_store=store,
        event_writer=writer,
        result_event=result,
    )

    assert first is not None
    assert first.status == "done"
    assert first.completed_at == result.ts
    assert replay is None
    assert store.get("TASK-RESEARCH").status == "done"  # type: ignore[union-attr]
    events = writer.event_log.read_all()
    assert sum(
        event.type == "task.status_changed"
        and event.payload.get("source")
        == RESEARCH_TASK_COMPLETION_SOURCE
        for event in events
    ) == 1
    assert sum(
        event.type == "task.done.evidence"
        and event.payload.get("source")
        == RESEARCH_TASK_COMPLETION_SOURCE
        for event in events
    ) == 1


def test_request_bound_research_keeps_parent_task_active(
    tmp_path: Path,
) -> None:
    store, writer, result = _research_result(
        tmp_path,
        request_bound=True,
    )

    completed = complete_standalone_research_task(
        task_store=store,
        event_writer=writer,
        result_event=result,
    )

    assert completed is None
    assert store.get("TASK-RESEARCH").status == "in_progress"  # type: ignore[union-attr]
    assert not any(
        event.type == "task.done.evidence"
        for event in writer.event_log.read_all()
    )


def test_run_goal_completion_closes_workflow_parent_task(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(mark_workflow_managed_task(Task(
        id="FLOW-1",
        title="Deliver workflow",
        status="in_progress",
    )))
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit(
        "workflow.invoke.accepted",
        actor="zf-cli",
        task_id="FLOW-1",
        correlation_id="run-1",
        payload={"task_id": "FLOW-1", "workflow_run_id": "run-1"},
    )
    terminal = writer.emit(
        "run.goal.completed",
        actor="zf-cli",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "evidence_refs": ["artifact:goal-dossier"],
        },
    )

    settled = settle_workflow_managed_task_from_run_terminal(
        task_store=store,
        event_writer=writer,
        terminal_event=terminal,
    )

    assert settled is not None and settled.status == "done"
    assert settled.completed_at == terminal.ts
    archived = store.get("FLOW-1")
    assert archived is not None and archived.status == "done"
    events = writer.event_log.read_all()
    assert any(
        event.type == "task.status_changed"
        and event.payload.get("source") == WORKFLOW_TASK_TERMINAL_SOURCE
        for event in events
    )
    done = next(event for event in events if event.type == "task.done.evidence")
    assert done.payload["evidence_refs"] == [
        terminal.id,
        "artifact:goal-dossier",
    ]


def test_replayed_blocked_goal_cannot_undo_later_run_recovery(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(mark_workflow_managed_task(Task(id="FLOW-1", title="Deliver", status="in_progress")))
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.emit("workflow.invoke.accepted", task_id="FLOW-1", correlation_id="run-1",
                payload={"task_id": "FLOW-1", "workflow_run_id": "run-1"})
    blocked = writer.emit("run.goal.blocked", correlation_id="run-1",
                          payload={"workflow_run_id": "run-1", "reason": "prior failure"})
    settle_workflow_managed_task_from_run_terminal(task_store=store, event_writer=writer, terminal_event=blocked)
    recovery = writer.emit("run.goal.updated", correlation_id="run-1",
                           payload={"workflow_run_id": "run-1", "status": "active"})
    reopen_workflow_managed_task_from_run_recovery(task_store=store, event_writer=writer, recovery_event=recovery)
    assert store.get("FLOW-1").status == "in_progress"

    settle_workflow_managed_task_from_run_terminal(task_store=store, event_writer=writer, terminal_event=blocked)
    assert store.get("FLOW-1").status == "in_progress"
    assert store.get("FLOW-1").blocked_reason == ""

    newer = writer.emit("run.goal.blocked", correlation_id="run-1",
                        payload={"workflow_run_id": "run-1", "reason": "new failure"})
    settle_workflow_managed_task_from_run_terminal(task_store=store, event_writer=writer, terminal_event=newer)
    assert store.get("FLOW-1").blocked_reason == "new failure"


def test_other_run_recovery_does_not_suppress_current_block(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(mark_workflow_managed_task(Task(id="FLOW-1", title="Deliver", status="in_progress")))
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    blocked = writer.emit("run.goal.blocked", task_id="FLOW-1", correlation_id="run-1",
                          payload={"workflow_run_id": "run-1", "reason": "current failure"})
    writer.emit("run.goal.updated", correlation_id="run-2",
                payload={"workflow_run_id": "run-2", "status": "active"})

    settle_workflow_managed_task_from_run_terminal(task_store=store, event_writer=writer, terminal_event=blocked)
    assert store.get("FLOW-1").blocked_reason == "current failure"
