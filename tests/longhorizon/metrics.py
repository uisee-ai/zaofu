"""LH-6.T2: wrap MetricsCollector.compute() to produce ResultRow shape.

The autoresearch loop treats MetricsSnapshot as the verify-phase
result; this module converts a snapshot into a ResultRow ready to
append to results.tsv.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.metrics.collector import MetricsCollector, MetricsSnapshot
from zf.core.task.store import TaskStore

from tests.longhorizon.results_log import ResultRow


def compute_snapshot(state_dir: Path) -> MetricsSnapshot:
    return MetricsCollector.compute(
        events=EventLog(state_dir / "events.jsonl"),
        tasks=TaskStore(state_dir / "kanban.json"),
        cost=CostTracker(state_dir / "cost.jsonl"),
    )


def snapshot_to_row(
    snap: MetricsSnapshot,
    *,
    iteration: int,
    commit: str,
    guard_status: str,
    status: str,
    note: str = "",
) -> ResultRow:
    note_text = f"{status}: {note}" if note else status
    return ResultRow(
        iteration=iteration,
        commit=commit,
        vcr=snap.vcr,
        mtts=snap.mtts,
        cost_per_task=snap.cost_per_task,
        rework_ratio=snap.rework_ratio,
        guard_status=guard_status,
        note=note_text,
    )
