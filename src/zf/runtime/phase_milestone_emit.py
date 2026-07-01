"""Kernel emission of phase-milestone events (doc 69 §3.3, slice S-f).

Deterministic, idempotent sweep: for each active feature, compute phase rollups
and emit ``delivery.phase.started/evaluated/completed`` for any (feature, phase)
transition not already in the log. The KERNEL emits these (mechanical, no LLM,
no re-judge — rates are *counted* from kernel verdicts), so a downstream phase
gate can later consume "previous phase completed" as an open condition. Mirrors
the ``feature_liveness`` sweep shape: pure helper, appends via ``event_writer``,
returns the emitted events; the orchestrator wraps it in ``_safe_housekeeping``.

Storm-safe: ``delivery.phase.*`` is in no wake pattern / reactor handler, so an
emit never triggers another wake (verified), and ``emitted_milestone_keys``
makes re-runs no-ops (守 I1/I2/I7).
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.feature.store import FeatureStore
from zf.core.task.store import TaskStore
from zf.runtime.execution_graph import build_execution_graph
from zf.runtime.phase_milestones import (
    compute_phase_milestones, emitted_milestone_keys,
)
from zf.runtime.phase_rollup import build_phase_rollups


def emit_phase_milestones(
    *,
    state_dir: Path,
    task_store: TaskStore,
    event_log: EventLog,
    event_writer: EventWriter,
) -> list[ZfEvent]:
    """Emit phase milestones for active features. Idempotent; never re-judges.

    Phase grouping uses ``Task.contract.phase`` (the canonical phase carrier,
    doc 69 §1) via the kanban-only graph, so no task-map load is needed here.
    """
    feature_store = FeatureStore(state_dir / "feature_list.json")
    features = [f for f in feature_store.list_all() if f.status == "active"]
    if not features:
        return []

    all_events = list(enumerate(event_log.read_all()))
    emitted_keys = emitted_milestone_keys(all_events)
    all_tasks = task_store.list_all()

    emitted: list[ZfEvent] = []
    for feature in features:
        tasks = {
            t.id: t for t in all_tasks
            if getattr(getattr(t, "contract", None), "feature_id", "") == feature.id
        }
        if not tasks:
            continue
        graph = build_execution_graph(
            task_map=None, tasks=tasks, events=all_events, feature_id=feature.id,
        )
        phases = build_phase_rollups(graph=graph, events=all_events, tasks=tasks)
        for event_type, payload in compute_phase_milestones(
            feature_id=feature.id, phases=phases, emitted_keys=emitted_keys,
        ):
            emitted.append(event_writer.append(ZfEvent(
                type=event_type, actor="zf-cli",
                task_id=feature.id, payload=payload,
            )))
            # guard against double-emit if the same key recurs within one sweep
            emitted_keys.add(
                (event_type, feature.id, str(payload.get("phase_id") or ""))
            )
    return emitted
