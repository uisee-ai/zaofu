"""Execution graph projection — planned task-map joined with actual runtime.

`execution-graph.v1` is the read-only reconciliation between the *planned*
DAG (an accepted ``task-map.v1``) and the *actual* runtime state (kanban
``Task`` rows + their evidence events). It is the net-new piece behind the
delivery trace (doc 65): the per-task "actual route" stays owned by
``execution_route`` and the per-task run panel by ``task_run_panel`` — this
module only joins planned nodes with their actual status and wires the
``blocked_by`` DAG.

Pure function over already-read inputs (task-map dict, kanban tasks, events),
so it is deterministic and trivially testable. It writes no runtime state and
introduces no second task schema (守 I1).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task

EventSlice = Sequence[tuple[int, ZfEvent]]

# Event types that count as acceptance/stage evidence for a task node. Kept in
# sync with the stage vocabulary in execution_route; drift_report (P2) consumes
# the same set to decide evidence drift. We collect the event *ids* only — the
# kernel already judged them; the projection never re-judges (守 I2/I7).
_EVIDENCE_TYPES: frozenset[str] = frozenset({
    "arch.proposal.done",
    "design.critique.done",
    "dev.build.done",
    "static_gate.passed",
    "static_gate.failed",
    "review.approved",
    "review.rejected",
    "verify.passed",
    "verify.failed",
    "test.passed",
    "test.failed",
    "judge.passed",
    "judge.failed",
    "discriminator.passed",
    "discriminator.failed",
    "task.done",
    "task.done.accepted",
    "scope.violation",
    # doc 69 S-a: rework / pause lifecycle, so phase rollup can count them.
    "task.rework.requested",
    "task.rework.triage.completed",
    "task.rework.capped",
    "task.fix_spawned",
    "dispatch.paused",
    "dispatch.resumed",
    "completion_audit.routed",
})

# doc 69 S-a / §4: gate event types → (gate_family, outcome). Drives pass_rate.
# Only kernel-emitted verdicts; the projection counts, it never re-judges (I2/I7).
_GATE_OUTCOMES: dict[str, tuple[str, str]] = {
    "judge.passed": ("judge", "pass"), "judge.failed": ("judge", "fail"),
    "discriminator.passed": ("discriminator", "pass"),
    "discriminator.failed": ("discriminator", "fail"),
    "test.passed": ("test", "pass"), "test.failed": ("test", "fail"),
    "verify.passed": ("verify", "pass"), "verify.failed": ("verify", "fail"),
    "review.approved": ("review", "pass"), "review.rejected": ("review", "fail"),
    "static_gate.passed": ("static", "pass"), "static_gate.failed": ("static", "fail"),
}

_DONE_STATES = {"done", "cancelled"}
_IN_PROGRESS_STATES = {"in_progress", "review", "test", "judge", "dispatched"}


def gate_outcomes_by_task(events: EventSlice) -> dict[str, dict[str, str]]:
    """Per task, the LATEST outcome (pass/fail) per gate family (doc 69 §4).

    events arrive in chronological order, so a later event overwrites = latest
    wins — avoids double-counting a fail then a pass after rework. Consumed by
    phase_rollup to compute pass_rate. Pure count of kernel verdicts (I2/I7).
    """
    out: dict[str, dict[str, str]] = {}
    for _seq, event in events:
        mapping = _GATE_OUTCOMES.get(event.type)
        if mapping is None:
            continue
        task_id = str(event.task_id or "").strip()
        if not task_id:
            continue
        family, outcome = mapping
        out.setdefault(task_id, {})[family] = outcome
    return out


def build_execution_graph(
    *,
    task_map: dict[str, Any] | None,
    tasks: dict[str, Task],
    events: EventSlice = (),
    feature_id: str = "",
    task_map_ref: str = "",
) -> dict[str, Any]:
    """Join an accepted task-map with actual kanban tasks + evidence events.

    ``tasks`` is keyed by task id. Degrades (not crashes) when ``task_map`` is
    missing — emits a diagnostic and falls back to a kanban-only node set so a
    legacy project still renders (doc 65 §16).
    """

    diagnostics: list[dict[str, str]] = []
    evidence_by_task = _evidence_by_task(events)
    fanout_by_task = _fanout_ids_by_task(events)  # doc 69 S-d
    instances_by_task = _dispatch_instances_by_task(events)  # doc 69 S-h §14.3
    timing_by_task = _timing_by_task(events)  # §14.7
    trace_by_task = _trace_by_task(events)  # §14.8

    planned_tasks = _planned_tasks(task_map)
    if not planned_tasks:
        diagnostics.append({
            "kind": "task_map_missing",
            "message": "no accepted task-map; rendering kanban-only graph",
        })
        return _kanban_only_graph(
            tasks=tasks,
            evidence_by_task=evidence_by_task,
            fanout_by_task=fanout_by_task,
            feature_id=feature_id,
            task_map_ref=task_map_ref,
            diagnostics=diagnostics,
        )

    planned_ids = {p["task_id"] for p in planned_tasks}
    nodes: list[dict[str, Any]] = []
    for planned in planned_tasks:
        task_id = planned["task_id"]
        task = tasks.get(task_id)
        if task is None:
            diagnostics.append({
                "kind": "kanban_task_missing",
                "task_id": task_id,
                "message": "task-map node has no kanban task yet",
            })
        actual = _actual(task, evidence_by_task.get(task_id, []),
                         fanout_by_task.get(task_id, []))
        _enrich_actual(actual, task, planned["planned"],
                       instances_by_task.get(task_id, []),
                       timing_by_task.get(task_id, {}),
                       trace_by_task.get(task_id, ""),
                       events)
        nodes.append({
            "task_id": task_id,
            "title": planned["title"] or (task.title if task else ""),
            "planned": planned["planned"],
            "actual": actual,
            "drift": [],  # filled by drift_report (P2); empty here
        })

    # kanban tasks bound to this feature but absent from the task-map
    for task_id, task in tasks.items():
        if task_id not in planned_ids:
            diagnostics.append({
                "kind": "task_not_in_task_map",
                "task_id": task_id,
                "message": "kanban task not present in accepted task-map",
            })

    node_status = {n["task_id"]: n["actual"]["status"] for n in nodes}
    edges = _edges(planned_tasks, node_status)
    waves = _waves(planned_tasks, node_status)

    return redact_obj({
        "schema_version": "execution-graph.v1",
        "feature_id": feature_id,
        "task_map_ref": task_map_ref,
        "task_count": len(nodes),
        "nodes": nodes,
        "edges": edges,
        "waves": waves,
        "diagnostics": diagnostics,
    })


def build_superseded_nodes(
    tasks: dict[str, Task], *, existing_ids: set[str], events: EventSlice = (),
) -> list[dict[str, Any]]:
    """Nodes for kanban tasks dropped from the current task-map (doc 69 §14.10, S-k).

    A re-plan can drop/replace a task: it lingers in kanban (often cancelled) but
    is absent from the accepted task-map, so ``build_execution_graph`` renders no
    node for it (only a diagnostic). This builds those nodes — ``planned={}`` and
    ``superseded=True`` — reusing the same actual-enrichment as planned nodes, so
    the delivery-trace graph can grey them out. Caller decides *when* (only on a
    detected re-plan); this stays a pure projection (守 I1/I2).
    """
    evidence_by_task = _evidence_by_task(events)
    fanout_by_task = _fanout_ids_by_task(events)
    instances_by_task = _dispatch_instances_by_task(events)
    timing_by_task = _timing_by_task(events)
    trace_by_task = _trace_by_task(events)
    out: list[dict[str, Any]] = []
    for task_id, task in tasks.items():
        if task_id in existing_ids:
            continue
        actual = _actual(task, evidence_by_task.get(task_id, []),
                         fanout_by_task.get(task_id, []))
        _enrich_actual(actual, task, {}, instances_by_task.get(task_id, []),
                       timing_by_task.get(task_id, {}),
                       trace_by_task.get(task_id, ""), events)
        out.append({
            "task_id": task_id,
            "title": task.title,
            "planned": {},
            "actual": actual,
            "drift": [],
            "superseded": True,
        })
    return out


def _planned_tasks(task_map: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(task_map, dict):
        return []
    raw_tasks = task_map.get("tasks")
    if not isinstance(raw_tasks, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task_id") or raw.get("id") or "").strip()
        if not task_id:
            continue
        out.append({
            "task_id": task_id,
            "title": str(raw.get("title") or "").strip(),
            "planned": {
                "owner_role": str(raw.get("owner_role") or "").strip(),
                "owner_instance": str(raw.get("owner_instance") or "").strip(),
                "phase": str(raw.get("phase") or "").strip(),  # doc 69 S-b
                "wave": _int(raw.get("wave")),
                "blocked_by": _str_list(raw.get("blocked_by")),
                "scope": _str_list(raw.get("scope")),
                "shared_files": _str_list(raw.get("shared_files")),
                "exclusive_files": _str_list(raw.get("exclusive_files")),
                "verification": str(raw.get("verification") or "").strip(),
            },
        })
    return out


def _actual(
    task: Task | None, evidence_events: list[str], fanout_ids: list[str] | None = None,
) -> dict[str, Any]:
    fanout_ids = fanout_ids or []
    if task is None:
        return {
            "status": "not_created",
            "assigned_to": "",
            "active_dispatch_id": "",
            "started_at": "",
            "completed_at": "",
            "evidence_events": evidence_events,
            "fanout_ids": fanout_ids,  # doc 69 S-d: agent-tree linkage
        }
    return {
        "status": task.status,
        "assigned_to": task.assigned_to or task.contract.owner_instance or "",
        "active_dispatch_id": task.active_dispatch_id or "",
        "started_at": task.started_at or "",
        "completed_at": task.completed_at or "",
        "evidence_events": evidence_events,
        "fanout_ids": fanout_ids,  # doc 69 S-d: agent-tree linkage
    }


def _fanout_ids_by_task(events: EventSlice) -> dict[str, list[str]]:
    """task_id → [fanout_id] via the fanout's trigger-event causation chain
    (doc 69 §12: general join, not assuming target_ref==task_id).

    `fanout.started.payload.trigger_event_id` points at the event that caused
    the fanout; that event's `task_id` is the owning task. Also accepts a direct
    `target_ref == task_id` match as a fallback.
    """
    event_task: dict[str, str] = {}
    for _seq, e in events:
        if e.id and e.task_id:
            event_task[e.id] = str(e.task_id)
    by_task: dict[str, list[str]] = {}
    for _seq, e in events:
        if e.type not in ("fanout.started", "fanout.requested"):
            continue
        p = e.payload if isinstance(e.payload, dict) else {}
        fid = str(p.get("fanout_id") or "").strip()
        if not fid:
            continue
        task_id = (
            event_task.get(str(p.get("trigger_event_id") or ""))
            or str(e.task_id or "")
            or str(p.get("target_ref") or "")
        ).strip()
        if not task_id:
            continue
        ids = by_task.setdefault(task_id, [])
        if fid not in ids:
            ids.append(fid)
    return by_task


def _enrich_actual(actual: dict, task: Task | None, planned: dict,
                   instances: list[str], timing: dict, trace_id: str,
                   events: EventSlice = ()) -> None:
    """doc 69 S-h: add affinity / time / trace_id / agent_summary / changed_files
    / health onto a node's actual block."""
    actual["affinity"] = _affinity(task, planned, instances)
    started = timing.get("started_at", "") or actual.get("started_at", "")
    completed = timing.get("completed_at", "") or actual.get("completed_at", "")
    actual["started_at"] = started
    actual["completed_at"] = completed
    actual["duration_seconds"] = _duration_seconds(started, completed)
    actual["trace_id"] = trace_id
    actual["changed_files"] = _changed_files(task)
    actual["agent_summary"] = _agent_summary(actual.get("fanout_ids", []), events)
    actual["health"] = _health(actual.get("status", ""), task, events)


def _changed_files(task: Task | None) -> list[str]:
    """doc 69 §14.9: files the task touched, from its evidence."""
    if task is None or task.evidence is None:
        return []
    return list(getattr(task.evidence, "files_touched", []) or [])


def _agent_summary(fanout_ids: list[str], events: EventSlice) -> dict[str, int]:
    """doc 69 §14.2/§14.4: roll the task's fanout runs into launched/executed/expected."""
    from zf.runtime.workflow_run import build_workflow_run
    launched = executed = expected = 0
    for fid in fanout_ids:
        run = build_workflow_run(fanout_id=fid, events=events)
        launched += sum(1 for o in run.get("launch_outcomes", []) if o.get("dispatched"))
        expected += len(run.get("launch_outcomes", []))
        executed += len(run.get("execution_outcomes", []))
    return {"launched": launched, "executed": executed, "expected": expected}


def _health(status: str, task: Task | None, events: EventSlice) -> dict[str, Any]:
    """doc 69 §14.2①/§14.4: heartbeat age + stuck. Deterministic — age measured
    against the latest event ts in the slice (not wall-clock), so it's testable."""
    from datetime import datetime
    if not events:
        return {"heartbeat_age_seconds": None, "stuck": False}
    tid = task.id if task is not None else ""
    last_hb = ""
    last_any = ""
    for _seq, e in events:
        if e.ts:
            last_any = e.ts
        if e.type == "worker.heartbeat" and str(e.task_id or "") == tid and e.ts:
            last_hb = e.ts
    age = None
    if last_hb and last_any:
        try:
            age = round((datetime.fromisoformat(last_any)
                         - datetime.fromisoformat(last_hb)).total_seconds(), 1)
        except (ValueError, TypeError):
            age = None
    stuck = bool(status in _IN_PROGRESS_STATES and age is not None and age > 300)
    return {"heartbeat_age_seconds": age, "stuck": stuck}


def _dispatch_instances_by_task(events: EventSlice) -> dict[str, list[str]]:
    """task_id → ordered distinct assignee instances across all task.dispatched
    (doc 69 §14.3 affinity drift). `task.dispatched.payload.assignee` is the
    canonical instance id (orchestrator_dispatch.py)."""
    by_task: dict[str, list[str]] = {}
    for _seq, e in events:
        if e.type != "task.dispatched":
            continue
        tid = str(e.task_id or "").strip()
        if not tid:
            continue
        p = e.payload if isinstance(e.payload, dict) else {}
        inst = str(p.get("assignee") or p.get("instance_id") or "").strip()
        if not inst:
            continue
        seq = by_task.setdefault(tid, [])
        if not seq or seq[-1] != inst:
            seq.append(inst)  # keep order, collapse consecutive repeats
    return by_task


def _affinity(task: Task | None, planned: dict, instances: list[str]) -> dict[str, Any]:
    """doc 69 §14.3: planned owner vs actual + cross-dispatch instance drift."""
    planned_owner = str(planned.get("owner_instance") or "").strip()
    planned_role = str(planned.get("owner_role") or "").strip()
    actual_owner = ""
    if task is not None:
        actual_owner = str(task.assigned_to or task.contract.owner_instance or "").strip()
    distinct = sorted(set(i for i in instances if i))
    planned_role_prefix = _role_prefix(planned_role)
    actual_owner_prefix = _role_prefix(actual_owner)
    history_role_prefixes = {_role_prefix(item) for item in instances if item}
    stage_handoff = (
        len(distinct) > 1
        and planned_role_prefix
        and planned_role_prefix in history_role_prefixes
        and actual_owner_prefix
        and actual_owner_prefix != planned_role_prefix
    )
    if stage_handoff:
        drift_kind = "none"
    elif len(distinct) > 1:
        drift_kind = "multi_instance"
    elif planned_owner and actual_owner and planned_owner != actual_owner:
        drift_kind = "owner_mismatch"
    else:
        drift_kind = "none"
    return {
        "planned_owner": planned_owner,
        "planned_role": planned_role,
        "actual_owner": actual_owner,
        "instances_history": instances,
        "stage_handoff": stage_handoff,
        "drifted": drift_kind != "none",
        "drift_kind": drift_kind,
    }


def _role_prefix(value: str) -> str:
    token = str(value or "").strip().lower()
    for sep in ("-", "_", ".", ":"):
        token = token.split(sep, 1)[0]
    return token


def _timing_by_task(events: EventSlice) -> dict[str, dict[str, str]]:
    """task_id → {started_at, completed_at} from dispatch/terminal events (§14.7)."""
    out: dict[str, dict[str, str]] = {}
    for _seq, e in events:
        tid = str(e.task_id or "").strip()
        if not tid:
            continue
        rec = out.setdefault(tid, {"started_at": "", "completed_at": ""})
        if e.type == "task.dispatched" and not rec["started_at"]:
            rec["started_at"] = str(e.ts or "")
        if e.type in ("task.done", "task.done.accepted"):
            rec["completed_at"] = str(e.ts or "")
    return out


def _trace_by_task(events: EventSlice) -> dict[str, str]:
    """task_id → correlation_id (latest) for trace cross-link (§14.8)."""
    out: dict[str, str] = {}
    for _seq, e in events:
        tid = str(e.task_id or "").strip()
        cid = str(e.correlation_id or "").strip()
        if tid and cid:
            out[tid] = cid
    return out


def _duration_seconds(started: str, completed: str) -> float | None:
    from datetime import datetime
    if not started or not completed:
        return None
    try:
        a = datetime.fromisoformat(started)
        b = datetime.fromisoformat(completed)
    except (ValueError, TypeError):
        return None
    return round((b - a).total_seconds(), 1)


def _evidence_by_task(events: EventSlice) -> dict[str, list[str]]:
    by_task: dict[str, list[str]] = {}
    for _seq, event in events:
        if event.type not in _EVIDENCE_TYPES:
            continue
        task_id = str(event.task_id or "").strip()
        if not task_id or not event.id:
            continue
        by_task.setdefault(task_id, []).append(event.id)
    return by_task


def _edges(
    planned_tasks: list[dict[str, Any]],
    node_status: dict[str, str],
) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for planned in planned_tasks:
        task_id = planned["task_id"]
        for blocker in planned["planned"]["blocked_by"]:
            satisfied = node_status.get(blocker, "") in _DONE_STATES
            edges.append({
                "from": blocker,
                "to": task_id,
                "kind": "blocked_by",
                "status": "satisfied" if satisfied else "pending",
            })
    return edges


def _waves(
    planned_tasks: list[dict[str, Any]],
    node_status: dict[str, str],
) -> list[dict[str, Any]]:
    by_wave: dict[int, list[str]] = {}
    for planned in planned_tasks:
        by_wave.setdefault(planned["planned"]["wave"], []).append(planned["task_id"])
    waves: list[dict[str, Any]] = []
    for wave in sorted(by_wave):
        task_ids = by_wave[wave]
        statuses = [node_status.get(tid, "not_created") for tid in task_ids]
        waves.append({
            "wave": wave,
            "task_ids": task_ids,
            "status": _wave_status(statuses),
        })
    return waves


def _wave_status(statuses: list[str]) -> str:
    if statuses and all(s in _DONE_STATES for s in statuses):
        return "done"
    if any(s in _IN_PROGRESS_STATES for s in statuses):
        return "in_progress"
    return "waiting"


def _kanban_only_graph(
    *,
    tasks: dict[str, Task],
    evidence_by_task: dict[str, list[str]],
    feature_id: str,
    task_map_ref: str,
    diagnostics: list[dict[str, str]],
    fanout_by_task: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    fanout_by_task = fanout_by_task or {}
    nodes = [
        {
            "task_id": task_id,
            "title": task.title,
            "planned": {},  # no task-map → no planned dimension
            "actual": _actual(task, evidence_by_task.get(task_id, []),
                              fanout_by_task.get(task_id, [])),
            "drift": [],
        }
        for task_id, task in tasks.items()
    ]
    node_status = {n["task_id"]: n["actual"]["status"] for n in nodes}
    return redact_obj({
        "schema_version": "execution-graph.v1",
        "feature_id": feature_id,
        "task_map_ref": task_map_ref,
        "task_count": len(nodes),
        "nodes": nodes,
        "edges": [],  # no planned blocked_by without a task-map
        "waves": [],
        "diagnostics": diagnostics,
        "kanban_only": True,
    })


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _int(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
