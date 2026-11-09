"""D0 read-only dynamic continuation Mock E2E for design 152."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    FanoutAggregateConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.dynamic_fragment_policy import canonical_fragment_digest
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)


class _MockTransport:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _config() -> ZfConfig:
    return ZfConfig(
        roles=[
            RoleConfig(name="research-api", backend="mock", role_kind="reader"),
            RoleConfig(name="research-test", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="dynamic-research-wave",
                trigger="workflow.invoke.requested",
                topology="fanout_reader",
                roles=["research-api", "research-test"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="research.bundle.ready",
                    failure_event="research.bundle.failed",
                ),
            ),
        ]),
    )


def _seed(state_dir: Path, writer: EventWriter) -> None:
    run_id = "RUN-DOC152-DYNAMIC"
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-DYNAMIC-RESEARCH",
        title="Inspect API and test surfaces",
        status="in_progress",
        assigned_to="research-api",
        active_dispatch_id="parent-dispatch",
    ))
    writer.emit(
        "run.goal.started",
        actor="zf-cli",
        correlation_id=run_id,
        payload={"run_id": run_id, "workflow_run_id": run_id},
    )
    writer.emit(
        "task_map.ready",
        actor="zf-cli",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "task_map_generation": "GEN-DYNAMIC-1",
        },
    )
    writer.emit(
        "plan.artifact_package.admitted",
        actor="zf-cli",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "package_id": "PKG-DYNAMIC-1",
            "package_ref": "artifacts/plan-packages/dynamic-1.json",
            "package_digest": "a" * 64,
            "plan_revision": "R-DYNAMIC-1",
            "task_map_generation": "GEN-DYNAMIC-1",
        },
    )
    WorkflowOperationService(
        state_dir=state_dir,
        event_log=writer.event_log,
        event_writer=writer,
    ).ensure_operation(
        workflow_run_id=run_id,
        operation_id="parent-research-op",
        operation_type="workflow",
        request={"prompt": "inspect current delivery gaps"},
        task_id="TASK-DYNAMIC-RESEARCH",
    )
    from zf.runtime.dynamic_checkpoint import create_checkpoint

    checkpoint = create_checkpoint(state_dir, writer.event_log.read_all(), workflow_run_id=run_id,
                                   parent_operation_id="parent-research-op", task_id="TASK-DYNAMIC-RESEARCH",
                                   task_map_generation="GEN-DYNAMIC-1")
    proposal = {
        "schema_version": "operation-plan-fragment.v1",
        "mode": "read_only",
        "workflow_run_id": run_id,
        "task_id": "TASK-DYNAMIC-RESEARCH",
        "fragment_id": "FRAGMENT-DYNAMIC-1",
        "parent_operation_id": "parent-research-op",
        "task_map_generation": "GEN-DYNAMIC-1",
        "current_plan_artifact_package": {
            "package_id": "PKG-DYNAMIC-1",
            "ref": "artifacts/plan-packages/dynamic-1.json",
            "sha256": "a" * 64,
        },
        "trigger_checkpoint": {
            "checkpoint_id": "CHECKPOINT-DYNAMIC-1",
            "ref": checkpoint["ref"],
            "sha256": checkpoint["sha256"],
        },
        "nodes": [{
            "node_id": "research-wave",
            "pattern_id": "dynamic-research-wave",
            "operation_type": "research",
            "expected_output": "research-bundle.v1",
        }],
        "budgets": {"max_children": 2, "max_depth": 1},
    }
    proposal["fragment_digest"] = canonical_fragment_digest(proposal)
    writer.emit(
        "workflow.fragment.proposed",
        actor="research-agent",
        task_id="TASK-DYNAMIC-RESEARCH",
        correlation_id=run_id,
        payload=proposal,
    )


@pytest.mark.parametrize("definition_change", ["unchanged", "modified", "removed", "queued_restart"])
def test_dynamic_read_only_continuation_reaches_existing_fanout_once(
    tmp_path: Path,
    definition_change: str,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    config = _config()
    config.workflow.stages[0].criteria.instructions = ["Preserve independent source verification."]
    _seed(state_dir, writer)

    tick = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )
    request = next(
        event
        for event in log.read_all()
        if event.type == "workflow.invoke.requested"
    )
    if definition_change == "modified":
        config.workflow.stages[0].roles = ["research-api"]
        config.workflow.stages[0].aggregate.success_event = "changed.result"
        config.workflow.stages[0].criteria.instructions = ["CHANGED METHOD MUST NOT EXECUTE"]
    elif definition_change == "removed":
        config.workflow.stages.clear()
    transport = _MockTransport()
    orchestrator = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    if definition_change == "queued_restart":
        orchestrator._dispatch_reader_fanout_child = lambda **kwargs: None
    orchestrator.run_once(events=[request])
    if definition_change == "queued_restart":
        assert not transport.sent
        config.workflow.stages.clear()
        orchestrator = Orchestrator(state_dir, config, transport)
    for _ in range(8):
        if len(transport.sent) >= 2:
            break
        orchestrator.run_once()

    assert tick.actions_applied == 1
    assert len(transport.sent) == 2
    for _role, briefing, _prompt, _context in transport.sent:
        body = briefing.read_text()
        assert "Preserve independent source verification." in body
        assert "CHANGED METHOD MUST NOT EXECUTE" not in body
    events = log.read_all()
    assert sum(event.type == "workflow.fragment.admitted" for event in events) == 1
    assert sum(event.type == "workflow.operation.reserved" for event in events) == 1
    assert sum(event.type == "workflow.invoke.requested" for event in events) == 1
    assert sum(event.type == "workflow.invoke.accepted" for event in events) == 1
    assert sum(event.type == "task.fanout.requested" for event in events) == 1
    assert sum(event.type == "fanout.child.dispatched" for event in events) == 2
    invoke = next(
        event for event in events
        if event.type == "workflow.invoke.accepted"
    )
    assert invoke.payload["provider_idempotency_key"].startswith("widem-")
    operations = reduce_workflow_operations(events)
    dynamic = next(
        row for row in operations.values()
        if row["operation_type"] == "dynamic_read_only_workflow"
    )
    assert dynamic["status"] == "running"
    assert dynamic["reservation_id"].startswith("wres-")

    restarted_transport = _MockTransport()
    restarted = Orchestrator(
        state_dir,
        config,
        restarted_transport,
    )  # type: ignore[arg-type]
    restarted.run_once(events=[request])
    second_tick = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )

    replayed = log.read_all()
    assert second_tick.actions_applied == 0
    assert sum(
        event.type == "workflow.invoke.accepted"
        for event in replayed
    ) == 1
    assert sum(
        event.type == "fanout.child.dispatched"
        for event in replayed
    ) == 2
    assert not any(
        event.type in {"task.rework.requested", "impl.rework.requested"}
        for event in replayed
    )

    # The existing recovery sweep must consume the parent fence, including
    # after restart and after removal of the source definition.
    parent = reduce_workflow_operations(replayed)["parent-research-op"]
    WorkflowOperationService(state_dir=state_dir, event_log=log, event_writer=writer).cancel(
        workflow_run_id="RUN-DOC152-DYNAMIC", operation_id="parent-research-op",
        request_hash=parent["request_hash"], reason="cancelled by owner",
    )
    restarted.run_once()
    fanout_id = next(e.payload["fanout_id"] for e in replayed if e.type == "fanout.started")
    assert restarted._fanout_manifest(fanout_id)["status"] == "cancelled"
    assert not restarted_transport.sent
