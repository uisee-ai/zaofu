"""MetricsCollector — 12-metric health snapshot from events.jsonl + kanban + cost.

Four groups (from plan-long-horizon-v2 §3):

  A. 持续性 Sustainability
     - mtts: mean turns-to-stuck (events between consecutive worker.stuck).
     - stuck_recovery_rate: fraction of worker.stuck events followed by
       forward progress within the next few events.
     - crash_free_hours: wall-clock span covered by the events window
       (proxy; refined later to "between two crashes").
     - resume_fidelity: 1.0 if no orphan/dispatch gaps; 0.0 on the first
       task.orphaned sighting in window.

  B. 对齐 Alignment
     - vcr: delegated to calculate_vcr; fraction of attempted tasks
       reaching status=done with evidence.
     - scope_violation_rate: scope.violation / task.dispatched.
     - discriminator_catch_rate: discriminator.failed / (passed+failed)
       — placeholder 0.0 when neither fires.
     - goal_drift: worker.drift.detected with signal=thrashing OR
       repeat_decisions / count(dispatched) — placeholder 0 when absent.

  C. 进度 Progress
     - throughput_per_hour: done tasks / wall-clock hours.
     - rework_ratio: rework-triggering failures / count(done). High = dev
       churns or terminal verification keeps rejecting completion claims.
     - causal_depth_mean: placeholder — proper causation graph is LH-5.
     - memory_hit_rate: placeholder — LH-2.

  D. 经济 Economic
     - cost_per_task: cost tracker total USD / count(done).
     - token_per_task: total (input+output) tokens / count(done).
     - recycle_freq_per_hour: count(worker.recycled) / hours.
     - budget_breach_rate: count(cost.budget.exceeded) / count(dispatched).

Pure function: reads are O(events + tasks + cost entries), never
writes. Safe to call from briefing builder on every wake.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.metrics.vcr import calculate_vcr
from zf.core.task.store import TaskStore


@dataclass
class MetricsSnapshot:
    # Group A
    mtts: float = 0.0
    stuck_recovery_rate: float = 0.0
    crash_free_hours: float = 0.0
    resume_fidelity: float = 1.0
    # Group B
    vcr: float = 0.0
    scope_violation_rate: float = 0.0
    discriminator_catch_rate: float = 0.0
    goal_drift: float = 0.0
    # Group C
    throughput_per_hour: float = 0.0
    rework_ratio: float = 0.0
    causal_depth_mean: float = 0.0
    memory_hit_rate: float = 0.0
    # LH-5.T5: trace health
    trace_complete_rate: float = 0.0
    avg_task_duration_minutes: float = 0.0
    avg_events_per_task: float = 0.0
    # Group D
    cost_per_task: float = 0.0
    token_per_task: float = 0.0
    recycle_freq_per_hour: float = 0.0
    budget_breach_rate: float = 0.0
    # 1204: backend breakdown (claude-code / codex / …). Empty when
    # cost tracker has no entries or entries lack a backend field.
    cost_by_backend: dict[str, float] = field(default_factory=dict)
    tokens_by_backend: dict[str, int] = field(default_factory=dict)
    # Meta
    window_hours: float = 0.0
    events_considered: int = 0
    tasks_done: int = 0
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def flag_alerts(self) -> list[str]:
        """LH-1.T5: thresholded alerts for briefing ⚠️ markers."""
        out: list[str] = []
        if self.tasks_done > 0 and self.vcr < 0.5:
            out.append(f"VCR={self.vcr:.2f} < 0.5")
        if (
            self.events_considered > 10
            and self.stuck_recovery_rate < 0.9
            and self._count_stucks() > 0
        ):
            out.append(f"StuckRecovery={self.stuck_recovery_rate:.2f} < 0.9")
        if self.budget_breach_rate > 0.05:
            out.append(f"BudgetBreach={self.budget_breach_rate:.2f} > 0.05")
        return out

    def _count_stucks(self) -> int:
        # Proxy kept on instance for threshold decision; fall back 0.
        return getattr(self, "_stuck_count", 0) or 0


_FAILURE_TYPES = {
    "gate.failed",
    "review.rejected",
    "test.failed",
    "judge.failed",
    "discriminator.failed",
}


class MetricsCollector:
    """Stateless — all data flows through compute()."""

    @staticmethod
    def compute(
        *,
        events: EventLog,
        tasks: TaskStore,
        cost: CostTracker,
        hours_window: float | None = None,
    ) -> MetricsSnapshot:
        """Aggregate a MetricsSnapshot.

        ``hours_window`` overrides the wall-clock span derived from the
        event log timestamps; used when the test fixture has too few
        events to produce a meaningful span.
        """
        all_events = events.read_all() if events.path.exists() else []
        snap = MetricsSnapshot(events_considered=len(all_events))

        # Wall-clock span.
        if hours_window is not None:
            snap.window_hours = max(hours_window, 1e-9)
        elif all_events:
            try:
                first = datetime.fromisoformat(all_events[0].ts)
                last = datetime.fromisoformat(all_events[-1].ts)
                snap.window_hours = max(
                    (last - first).total_seconds() / 3600.0, 1 / 3600.0,
                )
            except Exception:
                snap.window_hours = 1 / 3600.0
        else:
            snap.window_hours = 0.0

        # --- Group C: Throughput / Rework ---
        task_list = tasks.list_all_with_archive()
        done = [t for t in task_list if t.status == "done"]
        snap.tasks_done = len(done)
        if snap.window_hours > 0:
            snap.throughput_per_hour = len(done) / snap.window_hours
            # recycle freq uses same span
            snap.recycle_freq_per_hour = (
                sum(1 for e in all_events if e.type == "worker.recycled")
                / snap.window_hours
            )

        if len(done) > 0:
            fails = sum(1 for e in all_events if e.type in _FAILURE_TYPES)
            snap.rework_ratio = fails / len(done)

        # --- Group B: VCR / Scope violation / Discriminator / Drift ---
        try:
            vcr = calculate_vcr(tasks)
            snap.vcr = vcr.rate
        except Exception:
            snap.vcr = 0.0

        dispatched = sum(1 for e in all_events if e.type == "task.dispatched")
        if dispatched > 0:
            snap.scope_violation_rate = (
                sum(1 for e in all_events if e.type == "scope.violation")
                / dispatched
            )
            snap.budget_breach_rate = (
                sum(1 for e in all_events if e.type == "cost.budget.exceeded")
                / dispatched
            )

        disc_passed = sum(1 for e in all_events
                          if e.type == "discriminator.passed")
        disc_failed = sum(1 for e in all_events
                          if e.type == "discriminator.failed")
        if disc_passed + disc_failed > 0:
            snap.discriminator_catch_rate = (
                disc_failed / (disc_passed + disc_failed)
            )
        # Goal drift — count thrashing/repeat_decisions events.
        drift_bad = sum(
            1 for e in all_events
            if e.type == "worker.drift.detected"
            and isinstance(e.payload, dict)
            and e.payload.get("signal") in ("thrashing", "repeat_decisions")
        )
        if dispatched > 0:
            snap.goal_drift = drift_bad / dispatched

        # --- Group A: Stuck / Recovery / Crash-free ---
        stuck_indices = [
            i for i, e in enumerate(all_events) if e.type == "worker.stuck"
        ]
        setattr(snap, "_stuck_count", len(stuck_indices))
        if len(stuck_indices) >= 2:
            gaps = [stuck_indices[i + 1] - stuck_indices[i]
                    for i in range(len(stuck_indices) - 1)]
            snap.mtts = sum(gaps) / len(gaps)
        elif len(stuck_indices) == 1:
            snap.mtts = float(len(all_events) - stuck_indices[0])

        if stuck_indices:
            recovered = 0
            progress_types = {
                "task.assigned",
                "arch.proposal.done",
                "design.critique.done",
                "dev.build.done", "review.approved", "review.rejected",
                "verify.passed", "verify.failed",
                "test.passed", "test.failed", "judge.passed",
                "judge.failed", "gate.failed", "task.status_changed",
                "task.requeued", "worker.stuck.recovered",
            }
            for si in stuck_indices:
                window = all_events[si + 1: si + 8]
                if any(e.type in progress_types for e in window):
                    recovered += 1
            snap.stuck_recovery_rate = recovered / len(stuck_indices)

        # crash_free_hours ~ window_hours while no task.orphaned / pane.crash
        crashes = [
            e for e in all_events
            if e.type in ("task.orphaned", "pane.crash",
                          "worker.respawn.failed")
        ]
        if not crashes:
            snap.crash_free_hours = snap.window_hours
        else:
            # Time to first crash
            try:
                t0 = datetime.fromisoformat(all_events[0].ts)
                tc = datetime.fromisoformat(crashes[0].ts)
                snap.crash_free_hours = max(
                    (tc - t0).total_seconds() / 3600.0, 0.0,
                )
            except Exception:
                snap.crash_free_hours = 0.0

        # resume_fidelity: 1.0 if no orphans in window, else 0.0
        orphans = sum(1 for e in all_events if e.type == "task.orphaned")
        snap.resume_fidelity = 1.0 if orphans == 0 else 0.0

        # --- LH-5 trace health ---
        by_task: dict[str, list] = {}
        for e in all_events:
            if e.task_id:
                by_task.setdefault(e.task_id, []).append(e)
        if by_task:
            snap.avg_events_per_task = (
                sum(len(v) for v in by_task.values()) / len(by_task)
            )
        if done:
            complete = 0
            total_min = 0.0
            for t in done:
                tid = t.id
                evts = by_task.get(tid, [])
                if not evts:
                    continue
                has_created = any(e.type == "task.created" for e in evts)
                has_final = any(
                    e.type in ("judge.passed", "verify.passed", "task.status_changed")
                    for e in evts
                )
                if has_created and has_final:
                    complete += 1
                try:
                    t0 = datetime.fromisoformat(evts[0].ts)
                    t1 = datetime.fromisoformat(evts[-1].ts)
                    total_min += (t1 - t0).total_seconds() / 60.0
                except Exception:
                    pass
            snap.trace_complete_rate = complete / len(done)
            snap.avg_task_duration_minutes = total_min / len(done)

        # --- Group D: Cost ---
        try:
            totals = cost.per_role_totals()
            total_usd = sum(s.total_usd for s in totals.values())
            total_tokens = sum(
                s.input_tokens + s.output_tokens for s in totals.values()
            )
        except Exception:
            total_usd = 0.0
            total_tokens = 0
        if len(done) > 0:
            snap.cost_per_task = total_usd / len(done)
            snap.token_per_task = total_tokens / len(done)

        # 1204: per-backend breakdown. Skip "unknown" only when no other
        # backends exist (keeps legacy-only deployments visible).
        try:
            by_backend = cost.summary_by_backend()
        except Exception:
            by_backend = {}
        if by_backend:
            snap.cost_by_backend = {
                k: v.total_usd for k, v in by_backend.items()
            }
            snap.tokens_by_backend = {
                k: v.input_tokens + v.output_tokens
                for k, v in by_backend.items()
            }

        snap.alerts = snap.flag_alerts()
        return snap
