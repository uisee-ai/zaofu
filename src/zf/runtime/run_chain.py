"""run-chain.v1 — Airflow dag_run-style end-to-end chain for one delivery (S-D).

Assembles the stage chain for a feature run from already-recorded events:
``workflow.dag.stage_order`` is the DAG definition, the first/last matching
event per stage marks traversal, ``causation_id`` carries the edge back to
its trigger. Pure projection over events.jsonl — no second control plane.

ZaoFu extends the Airflow model with two semantics Airflow lacks: terminal
``done`` requires gate closure (judge AND-closure), and replan/supersede
forks live in ``task_map_history`` (linked by the consuming view, not
duplicated here).
"""

from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent

SCHEMA_VERSION = "run-chain.v1"


def _normalize(events: Iterable[ZfEvent | tuple[int, ZfEvent]]) -> list[ZfEvent]:
    return [item[1] if isinstance(item, tuple) else item for item in events]


def _seq_index(
    events: Iterable[ZfEvent | tuple[int, ZfEvent]],
) -> dict[str, int]:
    # event id -> EventSlice seq, the cross-view anchor ([seq_first..seq_last]
    # on stages jumps straight to the same window in Observability Events).
    index: dict[str, int] = {}
    for item in events:
        if isinstance(item, tuple):
            index[item[1].id] = int(item[0])
    return index


def build_run_chain(
    events: Iterable[ZfEvent | tuple[int, ZfEvent]],
    *,
    stage_order: list[str] | None = None,
) -> dict[str, Any]:
    events = list(events)
    seq_of = _seq_index(events)
    ordered = _normalize(events)
    stage_order = [s for s in (stage_order or []) if s]
    if not stage_order:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "no_stage_order",
            "trigger": None,
            "stages": [],
        }

    trigger = next((e for e in ordered if e.type == stage_order[0]), None)
    if trigger is None:
        trigger = ordered[0] if ordered else None

    stages: list[dict[str, Any]] = []
    previous_done_ts: str | None = trigger.ts if trigger else None
    previous_done = trigger is not None
    for stage_type in stage_order:
        matches = [e for e in ordered if e.type == stage_type]
        first, last = (matches[0], matches[-1]) if matches else (None, None)
        if matches:
            status = "done"
        elif previous_done:
            status = "active"
        else:
            status = "waiting"
        window_start = previous_done_ts
        window_end = last.ts if last else None
        task_ids: list[str] = []
        for e in ordered:
            if e.type != "task.dispatched" or not e.task_id:
                continue
            if window_start is not None and e.ts < window_start:
                continue
            if window_end is not None and e.ts > window_end:
                continue
            if e.task_id not in task_ids:
                task_ids.append(e.task_id)
        stages.append({
            "stage": stage_type,
            "status": status,
            "entered_at": first.ts if first else None,
            "completed_at": last.ts if last else None,
            "via_event_id": last.id if last else None,
            "causation_id": last.causation_id if last else None,
            "occurrences": len(matches),
            "seq_first": seq_of.get(first.id) if first else None,
            "seq_last": seq_of.get(last.id) if last else None,
            # done: tasks inside the closed window; active: tasks dispatched
            # since the previous stage completed (open window) so the Run
            # Graph can expand the in-flight fanout group.
            "task_ids": task_ids if status in {"done", "active"} else [],
        })
        if matches:
            previous_done_ts = last.ts
            previous_done = True
        else:
            # only the first not-yet-reached stage is "active"
            previous_done = False

    done_count = sum(1 for s in stages if s["status"] == "done")
    status = (
        "completed" if done_count == len(stages)
        else "in_progress" if done_count else "not_started"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "trigger": (
            {
                "event_id": trigger.id,
                "type": trigger.type,
                "ts": trigger.ts,
                "actor": trigger.actor,
            }
            if trigger
            else None
        ),
        "stages": stages,
    }
