from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from zf.core.config.schema import (
    FanoutAggregateConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.read_only_dynamic_continuation import (
    canonical_fragment_digest,
    execute_read_only_continuation,
    pending_read_only_continuation_actions,
    reconcile_reserved_read_only_continuations,
)
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)


class _RecordingTransport:
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


class _OperatorRaceWriter(EventWriter):
    def __init__(self, event_log: EventLog) -> None:
        super().__init__(event_log)
        self.injected = False

    def emit(self, event_type: str, **kwargs):  # noqa: ANN003
        event = super().emit(event_type, **kwargs)
        if event_type == "workflow.invoke.requested" and not self.injected:
            self.injected = True
            super().emit(
                "operator.action.proposed",
                actor="operator",
                correlation_id="RUN-DYN-1",
                payload={
                    "proposal_id": "OP-RACE-1",
                    "workflow_run_id": "RUN-DYN-1",
                    "status": "proposed",
                },
            )
        return event


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def _config(*, writer_pattern: bool = False, budget: float | None = None) -> ZfConfig:
    role_kind = "writer" if writer_pattern else "reader"
    topology = "fanout_writer_scoped" if writer_pattern else "fanout_reader"
    return ZfConfig(
        roles=[RoleConfig(name="researcher", role_kind=role_kind)],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="research-wave",
                trigger="workflow.invoke.requested",
                topology=topology,
                roles=["researcher"],
                aggregate=FanoutAggregateConfig(mode="wait_for_all"),
            ),
        ]),
        global_budget_usd=budget,
    )


def _seed(
    state_dir: Path,
    log: EventLog,
    writer: EventWriter,
    *,
    config: ZfConfig,
) -> dict:
    run_id = "RUN-DYN-1"
    log.append(ZfEvent(
        id="goal-start",
        type="run.goal.started",
        correlation_id=run_id,
        payload={"run_id": run_id, "workflow_run_id": run_id},
    ))
    log.append(ZfEvent(
        id="map-gen-1",
        type="task_map.ready",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "task_map_generation": "GEN-1",
        },
    ))
    log.append(ZfEvent(
        id="package-gen-1",
        type="plan.artifact_package.admitted",
        correlation_id=run_id,
        payload={
            "workflow_run_id": run_id,
            "package_id": "PKG-1",
            "package_ref": "artifacts/plan-packages/pkg-1.json",
            "package_digest": "a" * 64,
            "plan_revision": "R1",
            "task_map_generation": "GEN-1",
        },
    ))
    operations = WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    )
    operations.ensure_operation(
        workflow_run_id=run_id,
        operation_id="parent-op-1",
        operation_type="workflow",
        request={"prompt": "parent research"},
        task_id="TASK-DYN-1",
    )
    payload = {
        "schema_version": "operation-plan-fragment.v1",
        "mode": "read_only",
        "workflow_run_id": run_id,
        "task_id": "TASK-DYN-1",
        "fragment_id": "FRAG-1",
        "parent_operation_id": "parent-op-1",
        "task_map_generation": "GEN-1",
        "current_plan_artifact_package": {
            "package_id": "PKG-1",
            "ref": "artifacts/plan-packages/pkg-1.json",
            "sha256": "a" * 64,
        },
        "trigger_checkpoint": {
            "checkpoint_id": "CK-1",
            "ref": "artifacts/checkpoints/ck-1.json",
            "sha256": "b" * 64,
        },
        "nodes": [{
            "node_id": "research-1",
            "pattern_id": "research-wave",
            "operation_type": "research",
            "expected_output": "research-bundle.v1",
        }],
        "budgets": {"max_children": 1},
    }
    payload["fragment_digest"] = canonical_fragment_digest(payload)
    proposal = writer.emit(
        "workflow.fragment.proposed",
        actor="research-agent",
        task_id="TASK-DYN-1",
        correlation_id=run_id,
        payload=payload,
    )
    actions = pending_read_only_continuation_actions(
        state_dir,
        config=config,
        events=log.read_all(),
    )
    assert len(actions) == 1
    assert actions[0]["source_event_id"] == proposal.id
    return actions[0]


def test_concurrent_reservation_dispatches_once(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state_dir, log, writer, config=config)

    def run_once():
        return execute_read_only_continuation(
            state_dir,
            config=config,
            event_log=log,
            writer=writer,
            action=action,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_once(), range(2)))

    assert {result.status for result in results} == {
        "dispatched",
        "already_dispatched",
    }
    events = log.read_all()
    assert sum(event.type == "workflow.operation.reserved" for event in events) == 1
    assert sum(event.type == "workflow.invoke.requested" for event in events) == 1
    assert len({result.idempotency_key for result in results}) == 1


def test_operator_action_after_reservation_supersedes_before_dispatch(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state_dir, log, writer, config=config)

    result = execute_read_only_continuation(
        state_dir,
        config=config,
        event_log=log,
        writer=writer,
        action=action,
        after_reserve=lambda: writer.emit(
            "operator.action.proposed",
            actor="operator",
            correlation_id="RUN-DYN-1",
            payload={
                "proposal_id": "OP-1",
                "workflow_run_id": "RUN-DYN-1",
                "status": "proposed",
            },
        ),
    )

    assert result.status == "superseded"
    assert result.reason == "pending_operator_or_control_action"
    events = log.read_all()
    assert not any(event.type == "workflow.invoke.requested" for event in events)
    operation = reduce_workflow_operations(events)[result.operation_id]
    assert operation["status"] == "superseded"
    assert operation["reason"] == "pending_operator_or_control_action"


def test_resolved_exact_operator_proposal_does_not_block_dispatch(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state_dir, log, writer, config=config)
    writer.emit(
        "operator.action.proposed",
        actor="operator",
        correlation_id="RUN-DYN-1",
        payload={
            "workflow_run_id": "RUN-DYN-1",
            "proposal": {
                "proposal_id": "OP-RESOLVED-1",
                "action": "workflow-start",
                "valid": True,
            },
        },
    )
    proposed = log.read_all()[-1]
    writer.emit(
        "operator.action.resolved",
        actor="operator",
        correlation_id="RUN-DYN-1",
        payload={
            "proposal_event_id": proposed.id,
            "proposal_id": "OP-RESOLVED-1",
            "resolution": "executed",
        },
    )

    result = execute_read_only_continuation(
        state_dir,
        config=config,
        event_log=log,
        writer=writer,
        action=action,
    )

    assert result.status == "dispatched"
    assert any(
        event.type == "workflow.invoke.requested"
        for event in log.read_all()
    )


def test_operator_race_before_dispatch_consumption_supersedes_operation(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-DYN-1",
        title="Read-only dynamic research",
    ))
    action = _seed(state_dir, log, writer, config=config)
    racing_writer = _OperatorRaceWriter(log)

    result = execute_read_only_continuation(
        state_dir,
        config=config,
        event_log=log,
        writer=racing_writer,
        action=action,
    )

    assert result.status == "superseded"
    assert result.reason == "pending_operator_or_control_action"
    request = next(
        event
        for event in log.read_all()
        if event.id == result.dispatch_event_id
    )
    orchestrator = Orchestrator(
        state_dir,
        config,
        _RecordingTransport(),
    )  # type: ignore[arg-type]
    decision = orchestrator.run_once(events=[request])
    assert decision is not None
    assert not any(
        event.type == "workflow.invoke.accepted"
        for event in log.read_all()
    )
    assert not any(
        event.type == "task.fanout.requested"
        for event in log.read_all()
    )


def test_package_generation_change_releases_reservation_without_rework(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state_dir, log, writer, config=config)

    def admit_generation_two() -> None:
        writer.emit(
            "plan.artifact_package.admitted",
            actor="zf-cli",
            correlation_id="RUN-DYN-1",
            payload={
                "workflow_run_id": "RUN-DYN-1",
                "package_id": "PKG-2",
                "package_ref": "artifacts/plan-packages/pkg-2.json",
                "package_digest": "c" * 64,
                "plan_revision": "R2",
                "task_map_generation": "GEN-2",
            },
        )

    result = execute_read_only_continuation(
        state_dir,
        config=config,
        event_log=log,
        writer=writer,
        action=action,
        after_reserve=admit_generation_two,
    )

    assert result.status == "superseded"
    assert result.reason == "stale_plan_artifact_package_package_id"
    superseded = [
        event
        for event in log.read_all()
        if event.type == "workflow.operation.superseded"
    ]
    assert superseded[-1].payload["semantic_attempt_consumed"] is False
    assert not any(
        event.type in {"task.rework.requested", "impl.rework.requested"}
        for event in log.read_all()
    )


def test_budget_change_after_reservation_supersedes(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config(budget=0.01)
    action = _seed(state_dir, log, writer, config=config)

    result = execute_read_only_continuation(
        state_dir,
        config=config,
        event_log=log,
        writer=writer,
        action=action,
        after_reserve=lambda: CostTracker(state_dir / "cost.jsonl").record_usage(
            role="researcher",
            input_tokens=1,
            output_tokens=1,
            provider_cost_usd=0.02,
        ),
    )

    assert result.status == "superseded"
    assert result.reason == "budget_exhausted"
    assert not any(event.type == "workflow.invoke.requested" for event in log.read_all())


def test_writer_pattern_fails_closed_before_operation_reservation(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config(writer_pattern=True)
    action = _seed(state_dir, log, writer, config=config)

    result = execute_read_only_continuation(
        state_dir,
        config=config,
        event_log=log,
        writer=writer,
        action=action,
    )

    assert result.status == "rejected"
    assert result.reason == "execution_pattern_is_not_read_only"
    events = log.read_all()
    assert not any(event.type == "workflow.operation.reserved" for event in events)
    assert any(event.type == "workflow.fragment.rejected" for event in events)


def test_restart_reconciles_lost_started_receipt_without_redispatch(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state_dir, log, writer, config=config)
    dispatched = execute_read_only_continuation(
        state_dir,
        config=config,
        event_log=log,
        writer=writer,
        action=action,
    )
    writer.emit(
        "workflow.invoke.accepted",
        actor="zf-cli",
        task_id="TASK-DYN-1",
        correlation_id="RUN-DYN-1",
        payload={
            "workflow_run_id": "RUN-DYN-1",
            "workflow_operation_id": dispatched.operation_id,
            "fanout_request_event_id": "fanout-request-1",
        },
    )

    restarted_log = EventLog(state_dir / "events.jsonl")
    restarted_writer = EventWriter(restarted_log)
    assert reconcile_reserved_read_only_continuations(
        state_dir,
        event_log=restarted_log,
        writer=restarted_writer,
    ) == 1
    assert reconcile_reserved_read_only_continuations(
        state_dir,
        event_log=restarted_log,
        writer=restarted_writer,
    ) == 0

    events = restarted_log.read_all()
    assert sum(event.type == "workflow.invoke.requested" for event in events) == 1
    assert sum(event.type == "workflow.operation.started" for event in events) == 1
    operation = reduce_workflow_operations(events)[dispatched.operation_id]
    assert operation["status"] == "running"
    assert operation["idempotency_key"] == dispatched.idempotency_key


def test_restart_reuses_reserved_idempotency_before_dispatch(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state_dir, log, writer, config=config)

    with pytest.raises(RuntimeError, match="simulated crash"):
        execute_read_only_continuation(
            state_dir,
            config=config,
            event_log=log,
            writer=writer,
            action=action,
            after_reserve=lambda: (_ for _ in ()).throw(
                RuntimeError("simulated crash"),
            ),
        )
    reserved = next(
        row
        for row in reduce_workflow_operations(log.read_all()).values()
        if row["status"] == "reserved"
    )

    restarted_log = EventLog(state_dir / "events.jsonl")
    restarted = execute_read_only_continuation(
        state_dir,
        config=config,
        event_log=restarted_log,
        writer=EventWriter(restarted_log),
        action=action,
    )

    assert restarted.status == "dispatched"
    assert restarted.idempotency_key == reserved["idempotency_key"]
    assert sum(
        event.type == "workflow.operation.reserved"
        for event in restarted_log.read_all()
    ) == 1
    assert sum(
        event.type == "workflow.invoke.requested"
        for event in restarted_log.read_all()
    ) == 1


def test_run_manager_selects_fragment_as_unique_continuation_action(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    _seed(state_dir, log, writer, config=config)

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )

    assert result.actions_applied == 1
    applied = [
        event
        for event in log.read_all()
        if event.type == "run.manager.action.applied"
        and event.payload.get("action") == "read-only-dynamic-continuation"
    ]
    assert len(applied) == 1
    assert applied[0].payload["semantic_attempt_consumed"] is False
    assert sum(
        event.type == "workflow.invoke.requested"
        for event in log.read_all()
    ) == 1


def test_run_manager_owner_fence_blocks_wrc_owned_frontier(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    config.workflow.orchestration.normal_frontier_owner = "wrc"
    _seed(state_dir, log, writer, config=config)

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )

    assert result.actions_applied == 0
    assert not any(
        event.type == "workflow.invoke.requested"
        for event in log.read_all()
    )
    blocked = [
        event for event in log.read_all()
        if event.type == "workflow.normal_frontier.owner_blocked"
    ]
    assert len(blocked) == 1
    assert blocked[0].payload["owner"] == "wrc"


def test_reserved_continuation_reuses_existing_workflow_invoke_runtime(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    config = _config()
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-DYN-1",
        title="Read-only dynamic research",
    ))
    action = _seed(state_dir, log, writer, config=config)
    dispatched = execute_read_only_continuation(
        state_dir,
        config=config,
        event_log=log,
        writer=writer,
        action=action,
    )
    request = next(
        event
        for event in log.read_all()
        if event.id == dispatched.dispatch_event_id
    )
    transport = _RecordingTransport()
    orchestrator = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]

    first = orchestrator.run_once(events=[request])
    replay = orchestrator.run_once(events=[request])

    assert first is not None
    assert replay is not None
    events = log.read_all()
    accepted = [
        event for event in events
        if event.type == "workflow.invoke.accepted"
    ]
    assert len(accepted) == 1
    assert accepted[0].payload["provider_idempotency_key"] == (
        dispatched.idempotency_key
    )
    assert sum(event.type == "task.fanout.requested" for event in events) == 1
    operation = reduce_workflow_operations(events)[dispatched.operation_id]
    assert operation["status"] == "running"
    assert operation["reservation_id"] == dispatched.reservation_id
