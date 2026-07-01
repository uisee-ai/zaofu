"""LH-1.T1: MetricsCollector — 12-metric snapshot from events.jsonl.

Four groups:
  A. 持续性: MTTS, StuckRecoveryRate, CrashFreeHours, ResumeFidelity
  B. 对齐:   VCR, ScopeViolationRate, DiscriminatorCatch, GoalDrift
  C. 进度:   Throughput, ReworkRatio, CausalDepth, MemoryHit
  D. 经济:   CostPerTask, TokenPerTask, RecycleFreq, BudgetBreach

Implementation is pure function over the events log + kanban + cost
tracker — zero runtime-hot-path cost.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.metrics.collector import MetricsCollector, MetricsSnapshot
from zf.core.task.schema import Task, TaskEvidence
from zf.core.task.store import TaskStore


@pytest.fixture
def state(tmp_path: Path):
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]\n")
    return {
        "events": EventLog(sd / "events.jsonl"),
        "tasks": TaskStore(sd / "kanban.json"),
        "cost": CostTracker(sd / "cost.jsonl"),
        "root": tmp_path,
    }


class TestSnapshotShape:
    def test_snapshot_has_all_twelve_metrics(self, state):
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        assert isinstance(snap, MetricsSnapshot)
        # Group A
        assert hasattr(snap, "mtts")
        assert hasattr(snap, "stuck_recovery_rate")
        assert hasattr(snap, "crash_free_hours")
        assert hasattr(snap, "resume_fidelity")
        # Group B
        assert hasattr(snap, "vcr")
        assert hasattr(snap, "scope_violation_rate")
        # Group C
        assert hasattr(snap, "throughput_per_hour")
        assert hasattr(snap, "rework_ratio")
        assert hasattr(snap, "causal_depth_mean")
        # Group D
        assert hasattr(snap, "cost_per_task")
        assert hasattr(snap, "recycle_freq_per_hour")
        assert hasattr(snap, "budget_breach_rate")

    def test_empty_events_snapshot_is_zeroes(self, state):
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        assert snap.throughput_per_hour == 0
        assert snap.vcr == 0.0
        assert snap.cost_per_task == 0.0
        assert snap.rework_ratio == 0.0


class TestThroughput:
    def test_throughput_counts_done_tasks(self, state):
        # Two done tasks over 2 hours → throughput ~1/h
        state["tasks"].add(Task(id="T1", title="a",
                                evidence=TaskEvidence(commit="a1")))
        state["tasks"].add(Task(id="T2", title="b",
                                evidence=TaskEvidence(commit="b2")))
        # Force archive by moving to done
        state["tasks"].update("T1", status="done")
        state["tasks"].update("T2", status="done")
        # Event span: 2 hours
        import time
        now = time.time()
        state["events"].append(ZfEvent(type="loop.started", actor="zf-cli"))
        # Manually force old ts on first event — use a fresh file workaround
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
            hours_window=2.0,
        )
        assert snap.throughput_per_hour == pytest.approx(1.0, abs=0.5)


class TestVCR:
    def test_vcr_verified_over_attempted(self, state):
        # Two attempted tasks (review/testing/done), one with evidence
        state["tasks"].add(Task(id="T1", title="a",
                                evidence=TaskEvidence(commit="a1")))
        state["tasks"].update("T1", status="done")
        state["tasks"].add(Task(id="T2", title="b", status="review"))
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        assert snap.vcr == pytest.approx(0.5)


class TestMTTSAndRecovery:
    def test_mtts_counts_turns_between_stucks(self, state):
        # turns between stuck events, approximated by events between
        state["events"].append(ZfEvent(type="loop.started", actor="zf-cli"))
        for _ in range(5):
            state["events"].append(ZfEvent(type="agent.tool.use",
                                            actor="dev", payload={}))
        state["events"].append(ZfEvent(type="worker.stuck", actor="dev"))
        for _ in range(10):
            state["events"].append(ZfEvent(type="agent.tool.use",
                                            actor="dev", payload={}))
        state["events"].append(ZfEvent(type="worker.stuck", actor="dev"))
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        # 10 turns between the two stuck events
        assert snap.mtts > 0

    def test_stuck_recovery_rate_when_task_progresses(self, state):
        state["events"].append(ZfEvent(type="worker.stuck", actor="dev"))
        state["events"].append(ZfEvent(type="dev.build.done",
                                        actor="dev", task_id="T1"))
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        assert snap.stuck_recovery_rate > 0


class TestReworkRatio:
    def test_rework_ratio_counts_rework_per_done(self, state):
        state["tasks"].add(Task(id="T1", title="a"))
        state["tasks"].update("T1", status="done")
        state["events"].append(ZfEvent(
            type="review.rejected", actor="review", task_id="T1",
        ))
        state["events"].append(ZfEvent(
            type="gate.failed", actor="critic", task_id="T1",
            payload={"gate": "design_critique"},
        ))
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        assert snap.rework_ratio == pytest.approx(2.0)


class TestCostPerTask:
    def test_cost_per_task_from_tracker(self, state):
        # Record two usage events then mark 1 task done
        state["cost"].record_usage("dev", 1000, 500, "default", "dev-1")
        state["cost"].record_usage("dev", 1000, 500, "default", "dev-1")
        state["tasks"].add(Task(id="T1", title="a"))
        state["tasks"].update("T1", status="done")
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        assert snap.cost_per_task > 0


class TestBudgetBreach:
    def test_budget_breach_from_events(self, state):
        state["events"].append(ZfEvent(
            type="task.dispatched", actor="zf-cli", task_id="T1",
        ))
        state["events"].append(ZfEvent(
            type="cost.budget.exceeded", actor="zf-cli",
            payload={"scope": "global"},
        ))
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        assert snap.budget_breach_rate == pytest.approx(1.0)


class TestScopeViolation:
    def test_scope_violation_from_events(self, state):
        state["events"].append(ZfEvent(
            type="task.dispatched", actor="zf-cli", task_id="T1",
        ))
        state["events"].append(ZfEvent(
            type="task.dispatched", actor="zf-cli", task_id="T2",
        ))
        state["events"].append(ZfEvent(
            type="scope.violation", actor="dev", task_id="T1",
        ))
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        assert snap.scope_violation_rate == pytest.approx(0.5)


class TestSerialisation:
    def test_to_dict(self, state):
        snap = MetricsCollector.compute(
            events=state["events"], tasks=state["tasks"], cost=state["cost"],
        )
        d = snap.to_dict()
        assert isinstance(d, dict)
        assert "vcr" in d
        assert "throughput_per_hour" in d
        assert len(d) >= 12
