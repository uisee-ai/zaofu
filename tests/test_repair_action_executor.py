from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.integration_queue import build_integration_queue
from zf.runtime.transport import AttachHandle, TransportAdapter
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)
from zf.runtime.task_contract_snapshot import effective_contract_revision


class _RepairTransport(TransportAdapter):
    def __init__(self) -> None:
        self.alive: dict[str, bool] = {}
        self.spawn_calls: list[str] = []
        self.terminate_calls: list[str] = []
        self.sent: list[tuple[str, str]] = []

    def init(self): pass
    def is_session_running(self): return True

    def spawn(self, role, argv, *, cwd=None):
        self.spawn_calls.append(role.instance_id)
        self.alive[role.instance_id] = True

    def is_alive(self, role_name):
        return self.alive.get(role_name, True)

    def wait_ready(self, role_name, pattern, timeout): return True

    def send_task(self, role_name, briefing_path, prompt):
        self.sent.append((role_name, prompt))

    def capture_log(self, role_name, lines=200):
        return "ready"

    def poll_events(self): return []
    def attach_handle(self, role_name): return AttachHandle()

    def terminate(self, role_name):
        self.terminate_calls.append(role_name)
        self.alive[role_name] = False

    def shutdown(self): pass


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    (sd / "kanban.json").write_text("[]\n", encoding="utf-8")
    (sd / "feature_list.json").write_text("[]\n", encoding="utf-8")
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    return sd


@pytest.fixture
def config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )


def _append_request(
    state_dir: Path,
    *,
    action_id: str,
    kind: str,
    idempotency_key: str,
    task_id: str | None = None,
    **payload,
) -> ZfEvent:
    event = ZfEvent(
        type="repair.action.requested",
        actor="supervisor",
        task_id=task_id,
        payload={
            "action_id": action_id,
            "kind": kind,
            "idempotency_key": idempotency_key,
            **payload,
        },
    )
    EventWriter(EventLog(state_dir / "events.jsonl")).append(event)
    return event


def _events(state_dir: Path) -> list[ZfEvent]:
    return EventLog(state_dir / "events.jsonl").read_all()


def _enqueue_integration_entry(
    state_dir: Path,
    *,
    entry_id: str = "iq-1",
    task_id: str = "TASK-1",
    needs_review: bool = False,
) -> None:
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        type="task.integration_enqueued",
        actor="zf-cli",
        task_id=task_id,
        payload={
            "queue_entry_id": entry_id,
            "source_ref": "refs/worktrees/dev",
            "base_ref": "HEAD",
            "handoff_ref": "artifacts/dev/handoff.md",
        },
    ))
    if needs_review:
        writer.append(ZfEvent(
            type="integration.queue.needs_review",
            actor="integrator",
            task_id=task_id,
            payload={
                "queue_entry_id": entry_id,
                "reason": "merge conflict",
            },
        ))


def _write_fanout_manifest(
    state_dir: Path,
    *,
    fanout_id: str = "fanout-1",
    child_status: str = "dispatched",
) -> dict:
    manifest = {
        "fanout_id": fanout_id,
        "trace_id": "trace-1",
        "stage_id": "impl",
        "target_ref": "HEAD",
        "aggregate_config": {
            "success_event": "review.approved",
            "failure_event": "review.rejected",
            "child_success_event": "fanout.child.completed",
            "child_failure_event": "fanout.child.failed",
        },
        "children": [{
            "child_id": "dev-child",
            "role_instance": "dev",
            "status": child_status,
            "task_id": "TASK-1",
            "payload": {"scope": "gateway"},
        }],
    }
    fanout_dir = state_dir / "fanouts" / fanout_id
    fanout_dir.mkdir(parents=True)
    (fanout_dir / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return manifest


def test_repair_action_requested_wakes_orchestrator() -> None:
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "repair.action.requested" in WAKE_PATTERNS


def test_requeue_task_repair_action_applies_kernel_state(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="BLOCK", title="blocking work", status="in_progress"))
    store.add(Task(
        id="TASK-1",
        title="needs requeue",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-1",
        blocked_by=["BLOCK"],
    ))
    event = _append_request(
        state_dir,
        action_id="ra-requeue",
        kind="requeue_task",
        idempotency_key="repair:TASK-1:requeue",
        task_id="TASK-1",
        reason="missing review rework dispatch",
    )

    Orchestrator(state_dir, config, _RepairTransport()).run_once([event])

    task = TaskStore(state_dir / "kanban.json").get("TASK-1")
    assert task is not None
    assert task.status == "backlog"
    assert task.assigned_to == ""
    assert task.active_dispatch_id == ""
    events = _events(state_dir)
    assert any(e.type == "task.requeued" and e.task_id == "TASK-1" for e in events)
    applied = [e for e in events if e.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-requeue"
    assert applied[-1].payload["kind"] == "requeue_task"


def test_duplicate_repair_action_is_rejected_without_second_requeue(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="needs requeue", status="in_progress"))
    first = _append_request(
        state_dir,
        action_id="ra-1",
        kind="requeue_task",
        idempotency_key="repair:once",
        task_id="TASK-1",
    )
    second = _append_request(
        state_dir,
        action_id="ra-2",
        kind="requeue_task",
        idempotency_key="repair:once",
        task_id="TASK-1",
    )

    Orchestrator(state_dir, config, _RepairTransport()).run_once([first, second])

    events = _events(state_dir)
    requeued = [e for e in events if e.type == "task.requeued"]
    assert len(requeued) == 1
    rejected = [e for e in events if e.type == "repair.action.rejected"]
    assert rejected[-1].payload["action_id"] == "ra-2"
    assert rejected[-1].payload["reason"] == "duplicate idempotency key for ra-1"


def test_reemit_trigger_repair_action_appends_source_event_copy(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-1", title="needs rework", status="in_progress"))
    source = EventWriter(EventLog(state_dir / "events.jsonl")).append(ZfEvent(
        type="review.rejected",
        actor="review",
        task_id="TASK-1",
        payload={"reason": "missing regression evidence"},
    ))
    event = _append_request(
        state_dir,
        action_id="ra-reemit",
        kind="reemit_trigger",
        idempotency_key="repair:TASK-1:reemit",
        task_id="TASK-1",
        source_event_id=source.id,
        reason="rework trigger was missed",
    )

    Orchestrator(state_dir, config, _RepairTransport()).run_once([event])

    events = _events(state_dir)
    reemitted = [
        item for item in events
        if item.type == "review.rejected"
        and item.payload.get("repair_action_id") == "ra-reemit"
    ]
    assert len(reemitted) == 1
    assert reemitted[0].task_id == "TASK-1"
    assert reemitted[0].causation_id == event.id
    assert reemitted[0].payload["reason"] == "missing regression evidence"
    assert reemitted[0].payload["reemit_source_event_id"] == source.id
    applied = [item for item in events if item.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-reemit"
    assert applied[-1].payload["kind"] == "reemit_trigger"
    assert applied[-1].payload["source_event_id"] == source.id


def test_reemit_trigger_repair_action_rejects_missing_source_event(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="needs rework", status="in_progress"),
    )
    event = _append_request(
        state_dir,
        action_id="ra-reemit-missing",
        kind="reemit_trigger",
        idempotency_key="repair:TASK-1:reemit-missing",
        task_id="TASK-1",
    )

    Orchestrator(state_dir, config, _RepairTransport()).run_once([event])

    rejected = [
        item for item in _events(state_dir)
        if item.type == "repair.action.rejected"
    ]
    assert rejected[-1].payload["action_id"] == "ra-reemit-missing"
    assert rejected[-1].payload["reason"] == "missing_source_event_id"


def test_cancel_worker_repair_action_terminates_and_blocks_respawn(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    transport = _RepairTransport()
    transport.alive["dev"] = True
    event = _append_request(
        state_dir,
        action_id="ra-cancel",
        kind="cancel_worker",
        idempotency_key="repair:dev:cancel",
        role="dev",
    )
    orch = Orchestrator(state_dir, config, transport)

    orch.run_once([event])
    orch._dead_threshold = 1  # type: ignore[attr-defined]
    decisions = orch._capture_logs()  # type: ignore[attr-defined]

    events = _events(state_dir)
    assert transport.terminate_calls == ["dev"]
    assert decisions == []
    assert transport.spawn_calls == []
    state_events = [
        item for item in events
        if item.type == "worker.state.changed"
        and item.actor == "dev"
    ]
    assert [item.payload["to"] for item in state_events[-2:]] == [
        "cancelling",
        "blocked_human",
    ]
    assert state_events[-1].payload["to"] == "blocked_human"
    applied = [item for item in events if item.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-cancel"
    assert applied[-1].payload["kind"] == "cancel_worker"
    assert applied[-1].payload["worker_id"] == "dev"


@pytest.mark.parametrize("recovery_action", ["", "child-rebuild"])
def test_rerun_fanout_child_repair_action_dispatches_retry(
    state_dir: Path,
    recovery_action: str,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="fanout child task", status="in_progress"),
    )
    _write_fanout_manifest(state_dir)
    previous = EventWriter(EventLog(state_dir / "events.jsonl")).append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "stage_id": "impl",
            "child_id": "dev-child",
            "run_id": "run-fanout-1-dev-child",
            "role_instance": "dev",
            "task_id": "TASK-1",
        },
    ))
    event = _append_request(
        state_dir,
        action_id="ra-rerun",
        kind="rerun_fanout_child",
        idempotency_key="repair:fanout-1:dev-child:rerun",
        task_id="TASK-1",
        fanout_id="fanout-1",
        fanout_child_id="dev-child",
        recovery_action=recovery_action,
    )
    transport = _RepairTransport()

    Orchestrator(state_dir, config, transport).run_once([event])

    events = _events(state_dir)
    dispatched = [
        item for item in events
        if item.type == "fanout.child.dispatched"
        and item.payload.get("fanout_id") == "fanout-1"
        and item.payload.get("child_id") == "dev-child"
    ]
    assert [sent[0] for sent in transport.sent] == ["dev"]
    assert dispatched[-1].payload["run_id"] == "run-fanout-1-dev-child-retry-1"
    assert dispatched[-1].payload["retry_of_run_id"] == "run-fanout-1-dev-child"
    assert dispatched[-1].causation_id == previous.id
    applied = [item for item in events if item.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-rerun"
    assert applied[-1].payload["kind"] == "rerun_fanout_child"
    assert applied[-1].payload["fanout_id"] == "fanout-1"
    assert applied[-1].payload["fanout_child_id"] == "dev-child"
    assert applied[-1].payload["worker_id"] == "dev"


def test_rerun_fanout_child_repair_action_rejects_terminal_child(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="fanout child task", status="in_progress"),
    )
    _write_fanout_manifest(state_dir, child_status="completed")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "dev-child",
            "run_id": "run-fanout-1-dev-child",
            "role_instance": "dev",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.completed",
        actor="dev",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "dev-child",
            "run_id": "run-fanout-1-dev-child",
            "role_instance": "dev",
            "status": "completed",
        },
    ))
    event = _append_request(
        state_dir,
        action_id="ra-rerun-terminal",
        kind="rerun_fanout_child",
        idempotency_key="repair:fanout-1:dev-child:terminal",
        task_id="TASK-1",
        fanout_id="fanout-1",
        fanout_child_id="dev-child",
    )
    transport = _RepairTransport()

    Orchestrator(state_dir, config, transport).run_once([event])

    assert transport.sent == []
    rejected = [
        item for item in _events(state_dir)
        if item.type == "repair.action.rejected"
    ]
    assert rejected[-1].payload["action_id"] == "ra-rerun-terminal"
    assert rejected[-1].payload["reason"].startswith("fanout_child_terminal:")


def test_rerun_fanout_child_repair_action_rebuilds_failed_child(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="fanout child task", status="in_progress"),
    )
    _write_fanout_manifest(state_dir, child_status="failed")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    previous = writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "dev-child",
            "run_id": "run-fanout-1-dev-child",
            "role_instance": "dev",
            "task_id": "TASK-1",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="dev",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "dev-child",
            "run_id": "run-fanout-1-dev-child",
            "role_instance": "dev",
            "task_id": "TASK-1",
            "reason": "provider transport stalled",
        },
    ))
    event = _append_request(
        state_dir,
        action_id="ra-rerun-failed",
        kind="rerun_fanout_child",
        idempotency_key="repair:fanout-1:dev-child:failed",
        task_id="TASK-1",
        fanout_id="fanout-1",
        fanout_child_id="dev-child",
    )
    transport = _RepairTransport()

    Orchestrator(state_dir, config, transport).run_once([event])

    events = _events(state_dir)
    dispatched = [
        item for item in events
        if item.type == "fanout.child.dispatched"
        and item.payload.get("fanout_id") == "fanout-1"
        and item.payload.get("child_id") == "dev-child"
    ]
    assert [sent[0] for sent in transport.sent] == ["dev"]
    assert dispatched[-1].payload["run_id"] == "run-fanout-1-dev-child-retry-1"
    assert dispatched[-1].payload["retry_of_run_id"] == previous.payload["run_id"]
    applied = [item for item in events if item.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-rerun-failed"


def test_rerun_fanout_child_rebuilds_suspended_newer_dispatch(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="fanout child task", status="backlog"),
    )
    manifest = _write_fanout_manifest(state_dir)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    first = writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "dev-child",
            "run_id": "run-fanout-1-dev-child",
            "role_instance": "dev",
            "task_id": "TASK-1",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="dev",
        task_id="TASK-1",
        payload={**first.payload, "reason": "provider failed"},
    ))
    operation_id = "operation-retry-1"
    retry = writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            **first.payload,
            "run_id": "run-fanout-1-dev-child-retry-1",
            "operation_id": operation_id,
        },
    ))
    writer.append(ZfEvent(
        type="workflow.operation.requested",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "trace-1",
            "operation_id": operation_id,
            "request_hash": "request-retry-1",
        },
    ))
    writer.append(ZfEvent(
        type="workflow.operation.started",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "trace-1",
            "operation_id": operation_id,
            "request_hash": "request-retry-1",
        },
    ))
    writer.append(ZfEvent(
        type="workflow.operation.interrupted",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "workflow_run_id": "trace-1",
            "operation_id": operation_id,
            "request_hash": "request-retry-1",
            "reason": "graceful_stop",
        },
    ))
    manifest["children"][0].update({
        "status": "dispatched",
        "run_id": retry.payload["run_id"],
        "operation_id": operation_id,
    })
    TaskStore(state_dir / "kanban.json").update(
        "TASK-1",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id=str(retry.payload["run_id"]),
    )
    (state_dir / "fanouts/fanout-1/manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    event = _append_request(
        state_dir,
        action_id="ra-rerun-suspended",
        kind="rerun_fanout_child",
        idempotency_key="repair:fanout-1:dev-child:suspended",
        task_id="TASK-1",
        fanout_id="fanout-1",
        fanout_child_id="dev-child",
    )
    transport = _RepairTransport()

    Orchestrator(state_dir, config, transport).run_once([event])

    dispatched = [
        item for item in _events(state_dir)
        if item.type == "fanout.child.dispatched"
        and item.payload.get("fanout_id") == "fanout-1"
    ]
    assert [sent[0] for sent in transport.sent] == ["dev"]
    assert dispatched[-1].payload["run_id"].endswith("-retry-2")
    assert dispatched[-1].payload["retry_of_run_id"] == retry.payload["run_id"]
    released = [
        item for item in _events(state_dir)
        if item.type == "fanout.child.dispatch_lost"
        and item.payload.get("run_id") == retry.payload["run_id"]
    ]
    assert len(released) == 1
    assert released[0].payload["reason"] == (
        "suspended_operation_replaced_by_controlled_rerun"
    )
    applied = [
        item for item in _events(state_dir)
        if item.type == "repair.action.applied"
    ]
    assert applied[-1].payload["action_id"] == "ra-rerun-suspended"


def test_rerun_fanout_child_rebuilds_suspended_initial_dispatch(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="fanout child task", status="in_progress"),
    )
    manifest = _write_fanout_manifest(state_dir)
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    operation_id = "operation-initial"
    initial = writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "dev-child",
            "run_id": "run-fanout-1-dev-child",
            "role_instance": "dev",
            "task_id": "TASK-1",
            "operation_id": operation_id,
        },
    ))
    for event_type in (
        "workflow.operation.requested",
        "workflow.operation.started",
        "workflow.operation.interrupted",
    ):
        writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            task_id="TASK-1",
            payload={
                "workflow_run_id": "trace-1",
                "operation_id": operation_id,
                "request_hash": "request-initial",
                **(
                    {"reason": "graceful_stop"}
                    if event_type == "workflow.operation.interrupted"
                    else {}
                ),
            },
        ))
    manifest["children"][0].update({
        "status": "dispatched",
        "run_id": initial.payload["run_id"],
        "operation_id": operation_id,
    })
    TaskStore(state_dir / "kanban.json").update(
        "TASK-1",
        assigned_to="dev",
        active_dispatch_id=str(initial.payload["run_id"]),
    )
    (state_dir / "fanouts/fanout-1/manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    event = _append_request(
        state_dir,
        action_id="ra-rerun-suspended-initial",
        kind="rerun_fanout_child",
        idempotency_key="repair:fanout-1:dev-child:suspended-initial",
        task_id="TASK-1",
        fanout_id="fanout-1",
        fanout_child_id="dev-child",
    )
    transport = _RepairTransport()

    Orchestrator(state_dir, config, transport).run_once([event])

    events = _events(state_dir)
    released = [
        item for item in events
        if item.type == "fanout.child.dispatch_lost"
        and item.payload.get("run_id") == initial.payload["run_id"]
    ]
    dispatched = [
        item for item in events
        if item.type == "fanout.child.dispatched"
        and item.payload.get("fanout_id") == "fanout-1"
    ]
    assert len(released) == 1
    assert [sent[0] for sent in transport.sent] == ["dev"]
    assert dispatched[-1].payload["run_id"].endswith("-retry-1")
    applied = [item for item in events if item.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-rerun-suspended-initial"


@pytest.mark.parametrize("preparation_error", [False, True])
def test_rerun_fanout_child_repair_action_rejects_deferred_dispatch(
    state_dir: Path,
    config: ZfConfig,
    preparation_error: bool,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="fanout child task", status="in_progress"),
    )
    _write_fanout_manifest(state_dir, child_status="failed")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "dev-child",
            "run_id": "run-fanout-1-dev-child",
            "role_instance": "dev",
            "task_id": "TASK-1",
        },
    ))
    writer.append(ZfEvent(
        type="fanout.child.failed",
        actor="dev",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "dev-child",
            "run_id": "run-fanout-1-dev-child",
            "role_instance": "dev",
            "task_id": "TASK-1",
            "reason": "provider transport stalled",
        },
    ))
    event = _append_request(
        state_dir,
        action_id="ra-rerun-deferred",
        kind="rerun_fanout_child",
        idempotency_key="repair:fanout-1:dev-child:deferred",
        task_id="TASK-1",
        fanout_id="fanout-1",
        fanout_child_id="dev-child",
    )
    orch = Orchestrator(state_dir, config, _RepairTransport())

    patched = patch.object(orch, "_retry_fanout_child",
        side_effect=ValueError("reader retry target could not be pinned")) if preparation_error else patch.object(
        orch, "_ensure_fanout_role_dispatchable", return_value=False)
    with patched:
        orch.run_once([event])

    events = _events(state_dir)
    assert not [
        item
        for item in events
        if item.type == "repair.action.applied"
        and item.payload.get("action_id") == "ra-rerun-deferred"
    ]
    rejected = [
        item
        for item in events
        if item.type == "repair.action.rejected"
        and item.payload.get("action_id") == "ra-rerun-deferred"
    ]
    assert rejected[-1].payload["reason"] == (
        "reader retry preparation failed: reader retry target could not be pinned"
        if preparation_error else "fanout child rerun deferred before transport dispatch"
    )


def test_mark_stale_projection_repair_action_requests_rebuild(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    event = _append_request(
        state_dir,
        action_id="ra-projection",
        kind="mark_stale_projection_for_rebuild",
        idempotency_key="repair:projection:fanout-identity",
        projection="fanout_identity.v1",
        reason="stale current pointer observed by supervisor",
        evidence_refs=["events/supervisor/projection-stale.json"],
    )

    Orchestrator(state_dir, config, _RepairTransport()).run_once([event])

    events = _events(state_dir)
    rebuild = [
        item for item in events
        if item.type == "projection.rebuild.requested"
    ]
    assert rebuild[-1].payload["source"] == "repair_action"
    assert rebuild[-1].payload["action_id"] == "ra-projection"
    assert rebuild[-1].payload["projection"] == "fanout_identity.v1"
    assert rebuild[-1].payload["evidence_refs"] == [
        "events/supervisor/projection-stale.json",
    ]
    assert rebuild[-1].causation_id == event.id
    applied = [item for item in events if item.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-projection"
    assert applied[-1].payload["kind"] == "mark_stale_projection_for_rebuild"
    assert applied[-1].payload["projection"] == "fanout_identity.v1"
    assert applied[-1].payload["rebuild_event_id"] == rebuild[-1].id


def test_retry_integration_queue_repair_action_requests_retry(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="integration queue item", status="in_progress"),
    )
    _enqueue_integration_entry(state_dir, needs_review=True)
    event = _append_request(
        state_dir,
        action_id="ra-iq-retry",
        kind="retry_integration_queue_entry",
        idempotency_key="repair:iq-1:retry",
        task_id="TASK-1",
        queue_entry_id="iq-1",
        target_status="queued",
        evidence_refs=["artifacts/supervisor/iq-retry.json"],
    )

    Orchestrator(state_dir, config, _RepairTransport()).run_once([event])

    events = _events(state_dir)
    retry_events = [
        item for item in events
        if item.type == "integration.queue.retry_requested"
    ]
    assert retry_events[-1].payload["source"] == "repair_action"
    assert retry_events[-1].payload["action_id"] == "ra-iq-retry"
    assert retry_events[-1].payload["queue_entry_id"] == "iq-1"
    assert retry_events[-1].payload["target_status"] == "queued"
    assert retry_events[-1].causation_id == event.id
    projection = build_integration_queue(events)
    entries = {item["id"]: item for item in projection["entries"]}
    assert entries["iq-1"]["status"] == "queued"
    assert entries["iq-1"]["retry_count"] == 1
    applied = [item for item in events if item.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-iq-retry"
    assert applied[-1].payload["kind"] == "retry_integration_queue_entry"
    assert applied[-1].payload["queue_entry_id"] == "iq-1"
    assert applied[-1].payload["queue_event_id"] == retry_events[-1].id


def test_retry_integration_queue_redrives_matching_blocked_v4_operation(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="integration queue item", status="in_progress"),
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    operation = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="iq-1",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "integration",
            "operation_generation": 1,
            "task_map_generation": "map-g1",
        },
        parent_stage_id="integration",
        task_id="TASK-1",
        role_instance="candidate-integrator",
    )
    service.block(
        operation_id="iq-1",
        request_hash=operation.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-1",
        reason="candidate_incremental_failed:CandidateIncrementalError",
        details={"detail": "rolling smoke left candidate worktree dirty"},
    )
    _enqueue_integration_entry(state_dir, needs_review=True)
    event = _append_request(
        state_dir,
        action_id="ra-iq-v4-retry",
        kind="retry_integration_queue_entry",
        idempotency_key="repair:iq-1:v4-retry",
        task_id="TASK-1",
        queue_entry_id="iq-1",
        target_status="queued",
    )

    Orchestrator(state_dir, config, _RepairTransport()).run_once([event])

    events = _events(state_dir)
    redrives = [
        item
        for item in events
        if item.type == "workflow.operation.redrive_admitted"
        and item.payload.get("operation_id") == "iq-1"
    ]
    assert len(redrives) == 1
    assert redrives[0].payload["recovery_decision_owner"] == "controlled_action"
    assert redrives[0].payload["recovery_decision_event_id"] == event.id
    assert reduce_workflow_operations(events)["iq-1"]["status"] == "requested"
    applied = [
        item
        for item in events
        if item.type == "repair.action.applied"
        and item.payload.get("action_id") == "ra-iq-v4-retry"
    ]
    assert applied[-1].payload["operation_redrive_event_id"] == redrives[0].id


def test_retry_integration_queue_rotates_after_contract_revision_divergence(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    task = Task(id="TASK-1", title="integration contract refresh", status="in_progress")
    task.contract.validation = {
        "commands": [{
            "id": "rolling-smoke",
            "command": "true",
            "tier": "runtime",
            "rolling_smoke": True,
        }],
    }
    TaskStore(state_dir / "kanban.json").add(task)
    current_revision = effective_contract_revision(task)
    previous_revision = "contract-before-rolling-smoke"
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    from zf.runtime import workflow_operation_task_pipeline

    legacy_request_keys = tuple(
        key
        for key in workflow_operation_task_pipeline.TASK_PIPELINE_REQUEST_KEYS
        if key != "contract_revision"
    )
    with patch.object(
        workflow_operation_task_pipeline,
        "TASK_PIPELINE_REQUEST_KEYS",
        legacy_request_keys,
    ):
        operation = service.ensure_operation(
            workflow_run_id="run-1",
            operation_id="iq-1",
            operation_type="task-stage",
            request={
                "task_pipeline_stage": "integration",
                "operation_generation": 1,
                "task_map_generation": "map-g1",
                "contract_revision": previous_revision,
            },
            parent_stage_id="integration",
            task_id="TASK-1",
            role_instance="candidate-integrator",
        )
    service.block(
        operation_id="iq-1",
        request_hash=operation.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-1",
        reason="candidate_incremental_failed:CandidateIncrementalError",
        details={"detail": "rolling_smoke_command_missing"},
    )
    _enqueue_integration_entry(state_dir, needs_review=True)

    first_retry = _append_request(
        state_dir,
        action_id="ra-iq-same-operation-retry",
        kind="retry_integration_queue_entry",
        idempotency_key="repair:iq-1:same-operation",
        task_id="TASK-1",
        queue_entry_id="iq-1",
    )
    Orchestrator(state_dir, config, _RepairTransport()).run_once([first_retry])
    divergent = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="iq-1",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "integration",
            "operation_generation": 1,
            "task_map_generation": "map-g1",
            "contract_revision": current_revision,
        },
        parent_stage_id="integration",
        task_id="TASK-1",
        role_instance="candidate-integrator",
    )
    assert divergent.status == "divergent"
    assert build_integration_queue(_events(state_dir))["entries"][0]["status"] == "queued"

    rotate = _append_request(
        state_dir,
        action_id="ra-iq-contract-rotation",
        kind="retry_integration_queue_entry",
        idempotency_key="repair:iq-1:contract-rotation",
        task_id="TASK-1",
        queue_entry_id="iq-1",
        rotate_operation_generation=True,
        expected_previous_contract_revision=previous_revision,
        expected_current_contract_revision=current_revision,
    )
    Orchestrator(state_dir, config, _RepairTransport()).run_once([rotate])

    events = _events(state_dir)
    operation_view = reduce_workflow_operations(events)["iq-1"]
    assert operation_view["status"] == "superseded"
    queue_entry = build_integration_queue(events)["entries"][0]
    assert queue_entry["status"] == "discarded"
    applied = [
        item for item in events
        if item.type == "repair.action.applied"
        and item.payload.get("action_id") == "ra-iq-contract-rotation"
    ]
    assert applied[-1].payload["previous_contract_revision"] == previous_revision
    assert applied[-1].payload["current_contract_revision"] == current_revision
    assert applied[-1].payload["next_operation_generation"] == 2


def test_discard_integration_queue_repair_action_requests_discard(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="integration queue item", status="in_progress"),
    )
    _enqueue_integration_entry(state_dir)
    event = _append_request(
        state_dir,
        action_id="ra-iq-discard",
        kind="discard_integration_queue_entry",
        idempotency_key="repair:iq-1:discard",
        task_id="TASK-1",
        queue_entry_id="iq-1",
        reason="operator chose another candidate",
    )

    Orchestrator(state_dir, config, _RepairTransport()).run_once([event])

    events = _events(state_dir)
    discarded = [
        item for item in events
        if item.type == "integration.queue.discarded"
    ]
    assert discarded[-1].payload["source"] == "repair_action"
    assert discarded[-1].payload["action_id"] == "ra-iq-discard"
    assert discarded[-1].payload["queue_entry_id"] == "iq-1"
    assert discarded[-1].payload["reason"] == "operator chose another candidate"
    projection = build_integration_queue(events)
    entries = {item["id"]: item for item in projection["entries"]}
    assert entries["iq-1"]["status"] == "discarded"
    applied = [item for item in events if item.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-iq-discard"
    assert applied[-1].payload["kind"] == "discard_integration_queue_entry"
    assert applied[-1].payload["queue_entry_id"] == "iq-1"
    assert applied[-1].payload["queue_event_id"] == discarded[-1].id


def test_retry_integration_queue_repair_action_rejects_non_review_entry(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    TaskStore(state_dir / "kanban.json").add(
        Task(id="TASK-1", title="integration queue item", status="in_progress"),
    )
    _enqueue_integration_entry(state_dir)
    event = _append_request(
        state_dir,
        action_id="ra-iq-retry-queued",
        kind="retry_integration_queue_entry",
        idempotency_key="repair:iq-1:retry-queued",
        task_id="TASK-1",
        queue_entry_id="iq-1",
    )

    Orchestrator(state_dir, config, _RepairTransport()).run_once([event])

    events = _events(state_dir)
    assert not any(item.type == "integration.queue.retry_requested" for item in events)
    rejected = [
        item for item in events
        if item.type == "repair.action.rejected"
    ]
    assert rejected[-1].payload["action_id"] == "ra-iq-retry-queued"
    assert rejected[-1].payload["reason"] == "integration_queue_entry_not_retryable:queued"
    assert rejected[-1].payload["queue_entry_id"] == "iq-1"


def test_restart_worker_repair_action_uses_respawn_path(
    state_dir: Path,
    config: ZfConfig,
) -> None:
    transport = _RepairTransport()
    transport.spawn(RoleConfig(name="dev"), argv=[])
    event = _append_request(
        state_dir,
        action_id="ra-restart",
        kind="restart_worker",
        idempotency_key="repair:dev:restart",
        role="dev",
    )

    Orchestrator(state_dir, config, transport).run_once([event])

    events = _events(state_dir)
    assert transport.terminate_calls == ["dev"]
    assert transport.spawn_calls.count("dev") == 2
    assert any(e.type == "worker.respawned" and e.actor == "dev" for e in events)
    applied = [e for e in events if e.type == "repair.action.applied"]
    assert applied[-1].payload["action_id"] == "ra-restart"
    assert applied[-1].payload["kind"] == "restart_worker"
    assert applied[-1].payload["worker_id"] == "dev"
