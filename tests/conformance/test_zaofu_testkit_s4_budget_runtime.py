"""PRV-13 through usage ingress, kernel ticks and durable terminal replay."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from zf.core.config.schema import ExecutionProfileLimitsConfig
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_operation import load_workflow_operation

from tests.test_workflow_budget_guard import _runtime, _start_operation
from tests.conformance.test_zaofu_testkit_s4 import _orchestrator_context
from tests.testkit.oracles import assert_no_duplicate_event_ids
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence


def _usage(runtime: Orchestrator, sample: str, tokens: int, cost: float | None) -> ZfEvent:
    payload = {
        "session_id": "budget-fixture-session",
        "usage_sample_id": sample,
        "usage": {"input_tokens": tokens, "output_tokens": 0},
        "backend": "mock",
        "model": "unpriced-test-model",
    }
    if cost is not None:
        payload["total_cost_usd"] = cost
    return runtime.event_writer.append(ZfEvent(
        type="agent.usage", actor="dev", payload=payload,
    ))


@pytest.mark.parametrize("dimension", ["tokens", "usd", "wall_clock", "pricing_unavailable"])
def test_usage_budget_tick_settles_attempt_and_task_across_rebuild(
    tmp_path: Path, request: pytest.FixtureRequest, dimension: str,
) -> None:
    limits = ExecutionProfileLimitsConfig(
        token_budget=10 if dimension == "tokens" else 0,
        cost_budget_usd=0.01 if dimension in {"usd", "pricing_unavailable"} else 0,
        timeout_seconds=2 if dimension == "wall_clock" else 0,
    )
    runtime, transport = _runtime(tmp_path, operation_limits=limits)
    runtime.config.budget_fail_closed = dimension == "pricing_unavailable"
    attach_runtime_case_evidence(
        request, runtime.state_dir,
        given="Given running operation/attempt, mock transport and pinned execution limits",
        when="ingest fake usage through run_once, cross one limit, consume ledger and rebuild",
        then="budget blocks operation and Task, fails attempt once, and replay does not re-charge",
        forbidden=["fake completion", "double charge", "duplicate termination", "terminal regression"],
        task_id="TASK-BUDGET",
        inputs={"dimension": dimension, "clock": "injected only for wall_clock", "provider": "mock"},
    )
    operation_id, attempt_id = _start_operation(runtime)
    operation = load_workflow_operation(runtime.event_log, operation_id)
    assert operation is not None
    started = datetime.fromisoformat(operation["started_at"]).timestamp()
    if dimension == "wall_clock":
        runtime._now = lambda: started + 0.25

    below = _usage(runtime, "below-limit", 4, 0.004)
    runtime.run_once(events=[below])
    assert load_workflow_operation(runtime.event_log, operation_id)["status"] == "running"
    assert runtime.task_store.get("TASK-BUDGET").status == "in_progress"
    assert transport.terminated == []
    assert not any(e.type == "workflow.budget.exceeded" for e in runtime.event_log.read_all())

    if dimension == "wall_clock":
        runtime._now = lambda: started + 2
    reached = _usage(runtime, "at-limit", 6, None if dimension == "pricing_unavailable" else 0.006)
    runtime.run_once(events=[reached])
    # Consume the actual guard-produced ledger events, not hand-called settlement helpers.
    runtime.run_once()
    runtime.run_once()
    [budget] = [e for e in runtime.event_log.read_all() if e.type == "workflow.budget.exceeded"]
    assert budget.payload["exceeded_dimensions"] == [dimension]
    assert budget.payload["scope_id"] == operation_id
    assert budget.payload["measurement"]["total_tokens"] == 10
    assert budget.payload["measurement"]["total_usd"] == pytest.approx(
        0.004 if dimension == "pricing_unavailable" else 0.01,
    )
    assert transport.terminated == ["dev"]
    assert load_workflow_operation(runtime.event_log, operation_id)["status"] == "blocked"
    attempt_store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    assert attempt_store.get(attempt_id)["status"] == "failed"
    task = runtime.task_store.get("TASK-BUDGET")
    assert task.status == "blocked"
    assert task.blocked_reason == "workflow_budget_exceeded:" + dimension
    assert not task.active_dispatch_id
    assert not task.assigned_to
    totals = runtime.cost_tracker.usage_totals(instance_id="dev")
    assert totals["entries"] == 2
    terminal_types = {
        "workflow.budget.exceeded", "workflow.operation.blocked",
        "run.goal.blocked", "task.attempt.failed", "task.status_changed",
    }
    before = [(e.id, e.type) for e in runtime.event_log.read_all() if e.type in terminal_types]
    assert {kind: sum(t == kind for _, t in before) for kind in terminal_types} == {
        kind: 1 for kind in terminal_types
    }
    for _ in range(3):
        runtime = Orchestrator(
            runtime.state_dir, runtime.config, transport, project_root=tmp_path,
        )
        runtime.run_once(events=[below, reached])
        runtime.run_once()
        assert runtime.cost_tracker.usage_totals(instance_id="dev") == totals
        assert runtime.task_store.get("TASK-BUDGET") == task
        assert attempt_store.get(attempt_id)["status"] == "failed"
        assert load_workflow_operation(runtime.event_log, operation_id)["status"] == "blocked"
    events = runtime.event_log.read_all()
    assert [(e.id, e.type) for e in events if e.type in terminal_types] == before
    assert transport.terminated == ["dev"]
    assert not any(e.type in {
        "task.done", "task.done.evidence", "candidate.ready", "task.dispatched",
        "kernel.housekeeping.failed",
    } for e in events)
    assert_no_duplicate_event_ids(runtime.event_log)


def test_unpriced_usage_is_not_invented_usd_without_fail_closed(
    tmp_path: Path, request: pytest.FixtureRequest,
) -> None:
    runtime, transport = _runtime(
        tmp_path, operation_limits=ExecutionProfileLimitsConfig(cost_budget_usd=0.01),
    )
    runtime.config.budget_fail_closed = False
    attach_runtime_case_evidence(
        request, runtime.state_dir,
        given="running mock operation with a USD cap and explicit fail-open pricing policy",
        when="large unknown-model usage arrives through agent.usage and is replayed",
        then="unpriced usage stays visible, is counted once, and is not fabricated into USD",
        forbidden=["invented USD", "double charge", "fake completion"], task_id="TASK-BUDGET",
    )
    operation_id, _ = _start_operation(runtime)
    usage = _usage(runtime, "unknown-price", 150_000, None)
    runtime.run_once(events=[usage])
    runtime.run_once(events=[usage])
    totals = runtime.cost_tracker.usage_totals(instance_id="dev")
    assert totals["entries"] == totals["unpriced_entries"] == 1
    assert totals["total_tokens"] == 150_000
    assert totals["total_usd"] == 0
    assert load_workflow_operation(runtime.event_log, operation_id)["status"] == "running"
    assert runtime.task_store.get("TASK-BUDGET").status == "in_progress"
    assert transport.terminated == []
    assert not any(e.type in {"workflow.budget.exceeded", "kernel.housekeeping.failed"}
                   for e in runtime.event_log.read_all())


@pytest.mark.parametrize("cost", [0.009, 0.01])
def test_usage_is_accounted_before_new_work_dispatch(
    tmp_path: Path, request: pytest.FixtureRequest, cost: float,
) -> None:
    project, state, store, log, new_runtime = _orchestrator_context(tmp_path)
    runtime = new_runtime()
    runtime.config.global_budget_usd = 0.01
    attach_runtime_case_evidence(
        request, state,
        given="active session, backlog task and global USD cap, dry-run transport",
        when="one pushed usage event reaches the same run_once as dispatch selection",
        then="below-cap dispatches once; at-cap stays backlog across reconstruction",
        forbidden=["dispatch before accounting", "double charge", "fake completion"],
        task_id=project.task_id, inputs={"cost_usd": cost, "cap_usd": 0.01},
    )
    store.add(Task(id=project.task_id, title="budget guarded dispatch", status="backlog"))
    usage = _usage(runtime, "before-dispatch", 10, cost)
    runtime.run_once(events=[usage])
    blocked = cost >= 0.01
    for _ in range(3):
        runtime = new_runtime()
        runtime.run_once(events=[usage])
        runtime.run_once()
        assert store.get(project.task_id).status == ("backlog" if blocked else "in_progress")
        assert runtime.cost_tracker.total_usd() == pytest.approx(cost)
        assert runtime.cost_tracker.usage_totals()["entries"] == 1
    events = log.read_all()
    assert sum(e.type == "task.dispatched" for e in events) == (0 if blocked else 1)
    assert any(e.type == "cost.budget.exceeded" for e in events) is blocked
    if blocked:
        assert any(e.type == "orchestrator.dispatch_skipped"
                   and e.payload.get("reason") == "budget_exceeded" for e in events)
    assert not any(e.type in {"task.done", "candidate.ready", "kernel.housekeeping.failed"}
                   for e in events)
    assert_no_duplicate_event_ids(log)
