"""Delivery trace projection — feature-level idea→ship spine (doc 65 P0).

`delivery-trace.v1` answers "did the work run the way the plan said" at the
*feature* level, composing the planned task-map, the actual execution graph
(``execution_graph``), and lightweight plan/idea/ship summaries. It is a
read-only projection: it writes no runtime state, owns no task truth, and
re-judges nothing the kernel already decided (守 I1/I2/I7).

Pure function over already-read inputs (feature_id, idea/plan summaries,
task-map dict, kanban tasks, events) — deterministic and testable; the caller
(CLI / Web) does the resolution + loading.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.module_parity import (
    MODULE_PARITY_SCAN_FAILED_EVENTS,
    MODULE_PARITY_SCAN_REQUESTED,
    MODULE_PARITY_SCAN_RESULT_EVENTS,
)
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.runtime.delivery_cycles import build_delivery_cycles
from zf.runtime.delivery_summaries import (
    build_deposition_summary,
    build_observability_refs,
    build_score_summary,
)
from zf.runtime.execution_graph import build_execution_graph, build_superseded_nodes
from zf.runtime.goal_closure_projection import build_goal_closure_loop
from zf.runtime.observability_projection import build_delivery_closed_loop
from zf.runtime.delivery_flow_metrics import build_delivery_flow_metrics
from zf.runtime.task_lifecycle_trace import build_task_lifecycle
from zf.runtime.phase_rollup import build_phase_rollups
from zf.runtime.task_map_history import build_task_map_history

EventSlice = Sequence[tuple[int, ZfEvent]]

_DONE_STATES = {"done", "cancelled"}
_IN_PROGRESS_STATES = {"in_progress", "review", "test", "judge", "dispatched"}
_BLOCKED_STATES = {"blocked"}


def build_delivery_trace(
    *,
    feature_id: str,
    generated_at: str,
    tasks: dict[str, Task],
    task_map: dict[str, Any] | None = None,
    idea: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    events: EventSlice = (),
    project_id: str = "",
    task_map_ref: str = "",
    drift_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose a feature-level delivery-trace.v1 from already-read inputs.

    ``feature_id`` empty → a synthetic trace (``synthetic: true``), so
    feature-less legacy tasks still render without being misattributed
    (doc 65 §20.2).
    """

    synthetic = not feature_id.strip()
    trace_id = (
        f"trace-{feature_id}" if not synthetic
        else f"synthetic:{project_id or 'project'}"
    )

    graph = build_execution_graph(
        task_map=task_map, tasks=tasks, events=events,
        feature_id=feature_id, task_map_ref=task_map_ref,
    )
    counts = _node_counts(graph["nodes"])
    drift = drift_report or {"status": "ok", "items": []}
    ship = _ship_readiness(graph["nodes"], counts, drift, events, feature_id)
    status = _trace_status(counts)
    phases = build_phase_rollups(graph=graph, events=events, tasks=tasks)  # doc 69 S-c
    flow_metrics = build_delivery_flow_metrics(events)  # 2026-06-10 slice 1
    task_lifecycle = build_task_lifecycle(events)  # 2026-06-11 S-A

    # doc 69 §14.10 (S-k): re-plan version chain from artifact.manifest.published
    # task_map refs. Read-only derivation; never re-judges (守 I1/I2).
    task_map_history = build_task_map_history(events, feature_id=feature_id)
    # On a detected re-plan (>=2 task_map versions), surface tasks dropped from
    # the current task-map as greyed superseded nodes. Appended AFTER metrics
    # (counts/ship/phases) so they stay a visual overlay and never skew rates.
    if len(task_map_history) >= 2:
        existing_ids = {n["task_id"] for n in graph["nodes"]}
        graph["nodes"].extend(
            build_superseded_nodes(tasks, existing_ids=existing_ids, events=events)
        )

    diagnostics = list(graph.get("diagnostics", []))
    if synthetic:
        diagnostics.append({
            "kind": "synthetic_trace",
            "message": "no feature_id; grouped as synthetic trace",
        })
    workflow_spine = _workflow_spine(
        events=events,
        feature_id=feature_id,
        task_ids=set(tasks.keys()),
        task_map_ref=task_map_ref,
    )
    replan_contract_gate = _replan_contract_gate(
        events=events,
        feature_id=feature_id,
        task_map_ref=task_map_ref,
    )
    cycle_projection = build_delivery_cycles(
        events=events,
        phases=phases,
        feature_id=feature_id,
        task_ids=set(tasks.keys()),
        task_map_ref=task_map_ref,
        replan_contract_gate=replan_contract_gate,
    )

    module_parity_loop = _module_parity_loop(
        events=events,
        feature_id=feature_id,
        task_ids=set(tasks.keys()),
        task_map_ref=task_map_ref,
    )
    goal_closure_loop = build_goal_closure_loop(
        module_parity_loop,
        events=events,
        feature_id=feature_id,
    )
    control_room = _control_room_projection(
        events=events,
        tasks=tasks,
        counts=counts,
        feature_id=feature_id,
    )

    result = {
        "schema_version": "delivery-trace.v1",
        "generated_at": generated_at,
        "project_id": project_id,
        "feature_id": feature_id,
        "trace_id": trace_id,
        "synthetic": synthetic,
        "status": status,
        "idea": idea or {},
        "plan": plan or {},
        "task_map": {
            "status": "accepted" if task_map else "missing",
            "task_map_ref": task_map_ref,
            "task_count": _task_map_count(task_map),
            "wave_count": len(graph.get("waves", [])),
        },
        "execution_graph": {
            "task_count": counts["total"],
            "done_count": counts["done"],
            "in_progress_count": counts["in_progress"],
            "blocked_count": counts["blocked"],
            "waiting_count": counts["waiting"],
            "nodes": graph["nodes"],
            "edges": graph["edges"],
            "waves": graph.get("waves", []),
        },
        "phases": phases,  # doc 69 S-c: phase-level rollup
        "workflow_archetype": flow_metrics["workflow_archetype"],  # 2026-06-10 slice 1
        "flow_metrics": flow_metrics,
        "task_lifecycle": task_lifecycle,  # 2026-06-11 S-A (task-lifecycle.v1)
        "phase_count": len(phases),
        "cycles": cycle_projection["cycles"],
        "autoresearch_cycles": cycle_projection["autoresearch_cycles"],
        "task_map_history": task_map_history,  # doc 69 S-k: re-plan version chain
        "workflow_spine": workflow_spine,
        "replan_contract_gate": replan_contract_gate,
        "module_parity_loop": module_parity_loop,
        "goal_closure_loop": goal_closure_loop,
        "control_room": control_room,
        # doc 82 P3: trace-level cockpit summaries (additive, read-only)
        "score_summary": build_score_summary(
            cycle_projection["autoresearch_cycles"]),
        "deposition_summary": build_deposition_summary(
            cycle_projection["autoresearch_cycles"], replan_contract_gate),
        "observability_refs": build_observability_refs(
            events, trace_id=trace_id),
        "drift_report": {
            "status": drift.get("status", "ok"),
            "summary": drift.get("summary", {"error": 0, "warning": 0, "info": 0}),
            "items": drift.get("items", []),
        },
        "ship": ship,
        "source_event_ids": _source_event_ids(events),
        "diagnostics": diagnostics,
    }
    result["closed_loop"] = build_delivery_closed_loop(result)
    return redact_obj(result)


def _node_counts(nodes: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(nodes), "done": 0, "in_progress": 0,
              "blocked": 0, "waiting": 0}
    for node in nodes:
        status = str(node.get("actual", {}).get("status") or "")
        if status in _DONE_STATES:
            counts["done"] += 1
        elif status in _IN_PROGRESS_STATES:
            counts["in_progress"] += 1
        elif status in _BLOCKED_STATES:
            counts["blocked"] += 1
        else:
            counts["waiting"] += 1
    return counts


def _trace_status(counts: dict[str, int]) -> str:
    if counts["total"] == 0:
        return "empty"
    if counts["done"] == counts["total"]:
        return "done"
    if counts["in_progress"] or counts["done"]:
        return "in_progress"
    if counts["blocked"]:
        return "blocked"
    return "not_started"


def _ship_readiness(
    nodes: list[dict[str, Any]],
    counts: dict[str, int],
    drift: dict[str, Any],
    events: EventSlice = (),
    feature_id: str = "",
) -> dict[str, Any]:
    """Ship readiness derivation + real ship/merge terminal (doc 69 S-e / §7).

    `readiness` = can-ship projection (all nodes done, no error drift).
    `shipped`/`merge_ref`/`ship_status` = actual ship truth, consumed from
    `ship.*` / `candidate.*` events (never re-derived, never auto-ships).
    """
    node_ids = {n["task_id"] for n in nodes}
    missing = [
        {"task_id": n["task_id"], "status": n.get("actual", {}).get("status", "")}
        for n in nodes
        if str(n.get("actual", {}).get("status") or "") not in _DONE_STATES
    ]
    has_error_drift = any(
        str(item.get("severity") or "") == "error"
        for item in drift.get("items", [])
    )
    if counts["total"] == 0:
        readiness = "unknown"
    elif missing or has_error_drift:
        readiness = "blocked"
    else:
        readiness = "ready"

    # Consume real ship/candidate events (S-e). Conservative attribution:
    # only events tied to one of the feature's tasks or carrying feature_id —
    # avoids cross-feature contamination (under-reports rather than mislead).
    shipped, ship_status, merge_ref, candidate_status = False, "", "", ""
    blockers: list[dict[str, Any]] = []
    for _seq, e in events:
        if not (e.type.startswith("ship.") or e.type.startswith("candidate.")):
            continue
        p = e.payload if isinstance(e.payload, dict) else {}
        tid = str(e.task_id or p.get("task_id") or "")
        fid = str(p.get("feature_id") or "")
        if tid not in node_ids and not (feature_id and fid == feature_id):
            continue  # not this feature
        if e.type in ("ship.completed", "ship.done"):
            shipped, ship_status = True, "completed"
            merge_ref = str(p.get("final_commit") or p.get("candidate_ref") or "") or merge_ref
        elif e.type in ("ship.blocked", "ship.conflict", "ship.failed"):
            ship_status = e.type.split(".")[-1]
            blockers.append({
                "kind": e.type, "severity": "error",
                "evidence_event_id": e.id,
                "recommended_action": "resolve ship blocker via orchestrator",
            })
        elif e.type == "candidate.integration.completed":
            candidate_status = "integrated"
        elif e.type in ("candidate.quality.passed", "candidate.quality.failed"):
            candidate_status = e.type.split(".")[-1]

    return {
        "readiness": readiness,
        "status": readiness,  # back-compat: existing consumers read `status`
        "shipped": shipped,
        "ship_status": ship_status,
        "merge_ref": merge_ref,
        "candidate_status": candidate_status,
        "required_tasks": counts["total"],
        "done_tasks": counts["done"],
        "missing_evidence": missing,
        "release_blockers": blockers,
    }


def _task_map_count(task_map: dict[str, Any] | None) -> int:
    if not isinstance(task_map, dict):
        return 0
    raw = task_map.get("tasks")
    return len(raw) if isinstance(raw, list) else 0


def _source_event_ids(events: EventSlice) -> list[str]:
    ids = [event.id for _seq, event in events if event.id]
    return ids[-80:]


def _control_room_projection(
    *,
    events: EventSlice,
    tasks: dict[str, Task],
    counts: dict[str, int],
    feature_id: str,
) -> dict[str, Any]:
    latest_event: ZfEvent | None = events[-1][1] if events else None
    latest_payload = (
        latest_event.payload
        if latest_event is not None and isinstance(latest_event.payload, dict)
        else {}
    )
    blocked_event = _latest_event(
        events,
        {
            "flow.goal.blocked",
            "goal.closure.blocked",
            "module.parity.blocked",
            "human.escalate",
            "fanout.cancelled",
        },
    )
    pending_event = _latest_event(
        events,
        {
            "flow.goal.blocked",
            "goal.closure.blocked",
            "module.parity.blocked",
            "flow.gap_plan.ready",
            "goal.gap_plan.ready",
            "task_map.ready",
            "workflow.resume.applied",
        },
    )
    latest_evidence = _latest_refs(events)
    blocked_tasks = [task for task in tasks.values() if task.blocked_reason]
    active_tasks = [
        task
        for task in tasks.values()
        if task.status in {"in_progress", "review", "test", "judge", "blocked"}
    ]
    next_owner = _task_owner(active_tasks[0]) if active_tasks else ""
    if not next_owner and pending_event is not None:
        payload = pending_event.payload if isinstance(pending_event.payload, dict) else {}
        next_owner = str(
            payload.get("owner_role")
            or payload.get("next_owner")
            or payload.get("target_role")
            or ""
        )
    blocked_reason = ""
    if blocked_event is not None:
        payload = blocked_event.payload if isinstance(blocked_event.payload, dict) else {}
        blocked_reason = str(payload.get("reason") or blocked_event.type)
    elif blocked_tasks:
        blocked_reason = blocked_tasks[0].blocked_reason
    return {
        "schema_version": "control-room.v1",
        "feature_id": feature_id,
        "current_stage": str(
            latest_payload.get("stage_id")
            or latest_payload.get("current_stage")
            or (latest_event.type if latest_event is not None else "")
        ),
        "blocked_reason": blocked_reason,
        "next_owner": next_owner,
        "pending_action": _pending_action(pending_event),
        "latest_evidence": latest_evidence,
        "gap_count": _gap_count(events),
        "counts": {
            "total": counts["total"],
            "done": counts["done"],
            "in_progress": counts["in_progress"],
            "blocked": counts["blocked"],
        },
        "source_event_id": latest_event.id if latest_event is not None else "",
    }


def _latest_event(events: EventSlice, event_types: set[str]) -> ZfEvent | None:
    for _seq, event in reversed(events):
        if event.type in event_types:
            return event
    return None


def _task_owner(task: Task) -> str:
    return (
        str(getattr(task, "assigned_to", "") or "")
        or str(getattr(task.contract, "owner_instance", "") or "")
        or str(getattr(task.contract, "owner_role", "") or "")
    )


def _pending_action(event: ZfEvent | None) -> str:
    if event is None:
        return ""
    if event.type in {"flow.goal.blocked", "goal.closure.blocked", "module.parity.blocked"}:
        return "run_manager_diagnose_gap"
    if event.type in {"flow.gap_plan.ready", "goal.gap_plan.ready"}:
        return "apply_gap_task_map"
    if event.type == "task_map.ready":
        return "dispatch_ready_tasks"
    if event.type == "workflow.resume.applied":
        return "continue_workflow"
    return event.type


def _latest_refs(events: EventSlice) -> list[str]:
    keys = (
        "artifact_refs",
        "evidence_refs",
        "test_refs",
        "demo_refs",
        "e2e_refs",
        "provider_refs",
        "parity_refs",
        "regression_refs",
        "gap_plan_ref",
        "task_map_ref",
    )
    for _seq, event in reversed(events):
        payload = event.payload if isinstance(event.payload, dict) else {}
        refs: list[str] = []
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                refs.append(value)
            elif isinstance(value, list):
                refs.extend(str(item) for item in value if str(item or ""))
        if refs:
            return list(dict.fromkeys(refs))[:12]
    return []


def _gap_count(events: EventSlice) -> int:
    count = 0
    for _seq, event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type in {"flow.gap_plan.ready", "goal.gap_plan.ready", "gap_plan.ready"}:
            tasks = payload.get("gap_tasks")
            if isinstance(tasks, list):
                count += len(tasks)
            else:
                count += 1
            continue
        try:
            count = max(count, int(payload.get("open_p0_p1_gap_count") or 0))
        except (TypeError, ValueError):
            pass
    return count


def _workflow_spine(
    *,
    events: EventSlice,
    feature_id: str,
    task_ids: set[str],
    task_map_ref: str,
) -> dict[str, Any]:
    fanout_meta: dict[str, dict[str, Any]] = {}
    for _seq, event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "")
        if not fanout_id:
            continue
        meta = fanout_meta.setdefault(fanout_id, {})
        for key in (
            "trace_id",
            "stage_id",
            "topology",
            "pdd_id",
            "feature_id",
            "task_map_ref",
            "source_index_ref",
            "pipeline_id",
            "root_fanout_id",
        ):
            if payload.get(key) not in (None, "") and not meta.get(key):
                meta[key] = payload.get(key)
        if event.type in {"fanout.aggregate.completed", "fanout.cancelled", "fanout.timed_out"}:
            meta["status"] = str(payload.get("status") or event.type.rsplit(".", 1)[-1])
        elif event.type == "fanout.started":
            meta.setdefault("status", "running")

    nodes: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []
    for seq, event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not _workflow_event_linked(
            event=event,
            payload=payload,
            fanout_meta=fanout_meta,
            feature_id=feature_id,
            task_ids=task_ids,
            task_map_ref=task_map_ref,
        ):
            continue
        kind = _workflow_node_kind(event.type, payload, fanout_meta)
        if not kind:
            continue
        fanout_id = str(payload.get("fanout_id") or "")
        meta = fanout_meta.get(fanout_id, {})
        nodes.append({
            "kind": kind,
            "seq": seq,
            "event_type": event.type,
            "event_id": event.id,
            "task_id": str(event.task_id or payload.get("task_id") or ""),
            "fanout_id": fanout_id,
            "workflow_run_id": fanout_id,
            "child_id": str(payload.get("child_id") or payload.get("child_run") or ""),
            "stage_id": str(payload.get("stage_id") or meta.get("stage_id") or ""),
            "topology": str(payload.get("topology") or meta.get("topology") or ""),
            "status": str(payload.get("status") or meta.get("status") or ""),
            "task_map_ref": str(payload.get("task_map_ref") or meta.get("task_map_ref") or ""),
            "source_index_ref": str(
                payload.get("source_index_ref") or meta.get("source_index_ref") or ""
            ),
            "pipeline_id": str(payload.get("pipeline_id") or meta.get("pipeline_id") or ""),
            "root_fanout_id": str(
                payload.get("root_fanout_id") or meta.get("root_fanout_id") or ""
            ),
            "lane_id": str(payload.get("lane_id") or ""),
            "stage_slot": str(payload.get("stage_slot") or ""),
            "next_stage_slot": str(payload.get("next_stage_slot") or ""),
            "upstream_fanout_id": str(payload.get("upstream_fanout_id") or ""),
            "upstream_child_id": str(payload.get("upstream_child_id") or ""),
            "candidate_ref": str(payload.get("candidate_ref") or payload.get("branch") or ""),
        })
    if not nodes:
        diagnostics.append({
            "kind": "workflow_spine_empty",
            "message": "no fanout/product_delivery/task/candidate events linked to this feature",
        })
    return {
        "schema_version": "workflow-spine.v1",
        "node_count": len(nodes),
        "nodes": nodes[-120:],
        "diagnostics": diagnostics,
    }


def _module_parity_loop(
    *,
    events: EventSlice,
    feature_id: str,
    task_ids: set[str],
    task_map_ref: str,
) -> dict[str, Any]:
    """Project verify-triggered module parity scan and gap task-map amend.

    This is a read-only cockpit aid for long refactors: verify can discover
    module gaps, synthesize gap tasks, and re-enter writer fanout. The kernel
    truth remains the event log and task map files.
    """

    event_types = {
        MODULE_PARITY_SCAN_REQUESTED,
        *MODULE_PARITY_SCAN_RESULT_EVENTS,
        "module.parity.closed",
        "module.parity.blocked",
        "flow.discovery.requested",
        "flow.discovery.completed",
        "flow.discovery.failed",
        "flow.gap_plan.ready",
        "flow.goal.closed",
        "flow.goal.blocked",
        "goal.rescan.requested",
        "goal.rescan.completed",
        "goal.rescan.failed",
        "goal.gap.detected",
        "goal.gap_plan.ready",
        "goal.closure.closed",
        "goal.closure.blocked",
        "gap_plan.ready",
        "task_map.amend.requested",
        "task_map.amended",
        "task_map.amend.failed",
        "task_map.ready",
    }
    scan_requests: list[dict[str, Any]] = []
    scan_results: list[dict[str, Any]] = []
    gap_plans: list[dict[str, Any]] = []
    amends: list[dict[str, Any]] = []
    gap_task_ids: list[str] = []
    latest_task_map_ref = ""
    latest_gap_plan_ref = ""
    latest_replan_history_ref = ""
    source_event_ids: list[str] = []
    status = "idle"

    for seq, event in events:
        if event.type not in event_types:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not _module_parity_event_linked(
            event=event,
            payload=payload,
            feature_id=feature_id,
            task_ids=task_ids,
            task_map_ref=task_map_ref,
        ):
            continue
        source_event_ids.append(event.id)
        fanout_id = str(payload.get("fanout_id") or "")
        event_task_ids = _event_task_ids(event, payload)
        gap_ids = _gap_task_ids(payload)
        for task_id in gap_ids:
            if task_id not in gap_task_ids:
                gap_task_ids.append(task_id)
        gap_plan_ref = str(payload.get("gap_plan_ref") or "")
        replan_history_ref = str(payload.get("replan_history_ref") or "")
        new_task_map_ref = str(payload.get("new_task_map_ref") or payload.get("task_map_ref") or "")
        if gap_plan_ref:
            latest_gap_plan_ref = gap_plan_ref
        if replan_history_ref:
            latest_replan_history_ref = replan_history_ref
        if new_task_map_ref:
            latest_task_map_ref = new_task_map_ref
        row = {
            "seq": seq,
            "event_id": event.id,
            "event_type": event.type,
            "fanout_id": fanout_id,
            "task_id": str(event.task_id or payload.get("task_id") or ""),
            "task_ids": event_task_ids,
            "gap_task_ids": gap_ids,
            "gap_plan_ref": gap_plan_ref,
            "replan_history_ref": replan_history_ref,
            "task_map_ref": str(payload.get("task_map_ref") or ""),
            "new_task_map_ref": str(payload.get("new_task_map_ref") or ""),
            "status": str(payload.get("status") or ""),
            "reason": str(payload.get("reason") or ""),
        }
        if event.type == MODULE_PARITY_SCAN_REQUESTED:
            scan_requests.append(row)
            status = "scan_requested"
        elif event.type in {"goal.rescan.requested", "flow.discovery.requested"}:
            scan_requests.append(row)
            status = "scan_requested"
        elif event.type in MODULE_PARITY_SCAN_RESULT_EVENTS:
            scan_results.append(row)
            status = (
                "scan_failed"
                if event.type in MODULE_PARITY_SCAN_FAILED_EVENTS
                else "scan_completed"
            )
        elif event.type.startswith("goal.rescan.") or event.type.startswith("flow.discovery."):
            scan_results.append(row)
            status = "scan_failed" if event.type.endswith(".failed") else "scan_completed"
        elif event.type == "module.parity.closed":
            scan_results.append(row)
            status = "closed"
        elif event.type in {"goal.closure.closed", "flow.goal.closed"}:
            scan_results.append(row)
            status = "closed"
        elif event.type == "module.parity.blocked":
            scan_results.append(row)
            status = "blocked"
        elif event.type in {"goal.closure.blocked", "flow.goal.blocked"}:
            scan_results.append(row)
            status = "blocked"
        elif event.type == "gap_plan.ready":
            gap_plans.append(row)
            status = "gap_planned"
        elif event.type in {
            "goal.gap.detected",
            "goal.gap_plan.ready",
            "flow.gap_plan.ready",
        }:
            gap_plans.append(row)
            status = "gap_planned"
        elif event.type.startswith("task_map.amend"):
            amends.append(row)
            status = "amend_failed" if event.type.endswith(".failed") else "amended"
        elif event.type == "task_map.ready" and str(payload.get("resume_scope") or "") == "gap_tasks_only":
            amends.append(row)
            status = "gap_tasks_dispatched"

    return {
        "schema_version": "module-parity-loop.v1",
        "status": status,
        "scan_request_count": len(scan_requests),
        "scan_result_count": len(scan_results),
        "gap_plan_count": len(gap_plans),
        "amend_count": len(amends),
        "scan_requests": scan_requests[-20:],
        "scan_results": scan_results[-20:],
        "gap_plans": gap_plans[-20:],
        "task_map_amends": amends[-20:],
        "gap_task_ids": gap_task_ids[-80:],
        "latest_gap_plan_ref": latest_gap_plan_ref,
        "latest_replan_history_ref": latest_replan_history_ref,
        "latest_task_map_ref": latest_task_map_ref,
        "source_event_ids": source_event_ids[-80:],
    }


def _module_parity_event_linked(
    *,
    event: ZfEvent,
    payload: dict[str, Any],
    feature_id: str,
    task_ids: set[str],
    task_map_ref: str,
) -> bool:
    event_task_ids = _event_task_ids(event, payload)
    if task_ids and any(task_id in task_ids for task_id in event_task_ids):
        return True
    payload_feature = str(payload.get("feature_id") or payload.get("pdd_id") or "")
    if feature_id and payload_feature == feature_id:
        return True
    refs = {
        str(payload.get("task_map_ref") or ""),
        str(payload.get("new_task_map_ref") or ""),
        str(payload.get("old_task_map_ref") or ""),
        str(payload.get("amend_of") or ""),
    }
    if task_map_ref and task_map_ref in refs:
        return True
    return not feature_id and not task_map_ref


def _event_task_ids(event: ZfEvent, payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for value in (
        event.task_id,
        payload.get("task_id"),
    ):
        if value:
            ids.append(str(value))
    for key in ("task_ids", "completed_task_ids", "gap_task_ids"):
        raw = payload.get(key)
        if isinstance(raw, list):
            ids.extend(str(value) for value in raw if value)
    for task_id in _gap_task_ids(payload):
        ids.append(task_id)
    deduped: list[str] = []
    for task_id in ids:
        if task_id not in deduped:
            deduped.append(task_id)
    return deduped


def _gap_task_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    raw_ids = payload.get("gap_task_ids")
    if isinstance(raw_ids, list):
        ids.extend(str(value) for value in raw_ids if value)
    raw = payload.get("gap_tasks")
    if not isinstance(raw, list):
        return ids
    for item in raw:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or item.get("id") or "").strip()
        if task_id and task_id not in ids:
            ids.append(task_id)
    return ids


def _workflow_event_linked(
    *,
    event: ZfEvent,
    payload: dict[str, Any],
    fanout_meta: dict[str, dict[str, Any]],
    feature_id: str,
    task_ids: set[str],
    task_map_ref: str,
) -> bool:
    event_task_id = str(event.task_id or payload.get("task_id") or "")
    if event_task_id and event_task_id in task_ids:
        return True
    payload_feature = str(payload.get("feature_id") or payload.get("pdd_id") or "")
    if feature_id and payload_feature == feature_id:
        return True
    payload_task_map_ref = str(payload.get("task_map_ref") or "")
    if task_map_ref and payload_task_map_ref == task_map_ref:
        return True
    for task_id in payload.get("task_ids") or payload.get("completed_task_ids") or []:
        if str(task_id) in task_ids:
            return True
    fanout_id = str(payload.get("fanout_id") or "")
    meta = fanout_meta.get(fanout_id, {})
    if feature_id and str(meta.get("feature_id") or meta.get("pdd_id") or "") == feature_id:
        return True
    if task_map_ref and str(meta.get("task_map_ref") or "") == task_map_ref:
        return True
    return not feature_id and not task_map_ref and event.type.startswith("fanout.")


def _workflow_node_kind(
    event_type: str,
    payload: dict[str, Any],
    fanout_meta: dict[str, dict[str, Any]],
) -> str:
    if event_type.startswith("refactor.scan."):
        return "planning_reader_fanout"
    if event_type == "fanout.started":
        topology = str(payload.get("topology") or "")
        if topology == "fanout_writer_scoped":
            return "writer_fanout"
        if topology == "fanout_reader":
            return "reader_fanout"
        return "fanout_run"
    if event_type == "fanout.child.dispatched":
        topology = str(
            payload.get("topology")
            or fanout_meta.get(str(payload.get("fanout_id") or ""), {}).get("topology")
            or ""
        )
        return "writer_child_run" if topology == "fanout_writer_scoped" else "reader_child_run"
    if event_type == "product_delivery.task_map.accepted":
        return "accepted_delivery_bundle"
    if event_type == "product_delivery.wave.ready":
        return "delivery_wave_ready"
    if event_type == "lane.stage.completed":
        return "lane_stage_completed"
    if event_type == "lane.stage.failed":
        return "lane_stage_failed"
    if event_type == "task.created":
        return "kanban_task"
    if event_type.startswith("candidate."):
        return "candidate_gate"
    if event_type in {
        "flow.goal.closed", "module.parity.closed",
    }:
        return "goal_execution_closed"
    if event_type == "workflow.call.result.admitted" and str(
        payload.get("control_result_schema") or ""
    ) == "goal-closure-result.v1":
        return "goal_result_admitted"
    if event_type.startswith("goal.closure."):
        return "goal_closure_judge"
    if event_type.startswith("run.goal.completion."):
        return "goal_completion_gate"
    if event_type.startswith("run.delivery."):
        return "goal_delivery"
    if event_type == "run.goal.completed":
        return "goal_completed"
    if event_type in {"fanout.aggregate.completed", "fanout.cancelled", "fanout.timed_out"}:
        return "fanout_barrier"
    return ""


def _replan_contract_gate(
    *,
    events: EventSlice,
    feature_id: str,
    task_map_ref: str,
) -> dict[str, Any]:
    evals: list[dict[str, Any]] = []
    adoption_events: list[dict[str, Any]] = []
    status = "none"
    for seq, event in events:
        if event.type not in {
            "replan.contract_eval.completed",
            "replan.contract_eval.adoption_blocked",
            "replan.adoption.prepared",
            "replan.adoption.completed",
            "replan.adoption.stale_rejected",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        nested_eval = payload.get("eval") if isinstance(payload.get("eval"), dict) else {}
        linked = _replan_event_linked(
            payload=payload,
            nested_eval=nested_eval,
            feature_id=feature_id,
            task_map_ref=task_map_ref,
        )
        if not linked:
            continue
        if event.type in {
            "replan.contract_eval.completed",
            "replan.contract_eval.adoption_blocked",
            "replan.adoption.stale_rejected",
        }:
            eval_payload = nested_eval or payload
            evals.append({
                "seq": seq,
                "event_id": event.id,
                "event_type": event.type,
                "eval_id": str(eval_payload.get("eval_id") or payload.get("eval_id") or ""),
                "decision": str(eval_payload.get("decision") or payload.get("decision") or ""),
                "profile": str(eval_payload.get("profile") or payload.get("profile") or ""),
                "trigger_failure_class": str(
                    eval_payload.get("trigger_failure_class")
                    or payload.get("trigger_failure_class")
                    or ""
                ),
                "old_task_map_ref": str(
                    eval_payload.get("old_task_map_ref")
                    or payload.get("old_task_map_ref")
                    or ""
                ),
                "new_task_map_ref": str(
                    eval_payload.get("new_task_map_ref")
                    or payload.get("new_task_map_ref")
                    or payload.get("task_map_ref")
                    or ""
                ),
                "failed_checks": [
                    str(item)
                    for item in eval_payload.get("failed_checks") or []
                    if str(item).strip()
                ][:20],
                "check_summary": dict(eval_payload.get("check_summary") or {}),
                "contract_delta_counts": dict(
                    eval_payload.get("contract_delta_counts") or {}
                ),
                "artifact_ref": _artifact_ref(eval_payload),
            })
        if event.type.startswith("replan.adoption."):
            adoption_state = event.type.rsplit(".", 1)[-1]
            if adoption_state == "completed":
                status = "adopted"
            elif adoption_state == "stale_rejected":
                status = "stale_rejected"
            elif adoption_state == "prepared" and status == "none":
                status = "prepared"
            adoption_events.append({
                "seq": seq,
                "event_id": event.id,
                "event_type": event.type,
                "state": adoption_state,
                "task_map_ref": str(payload.get("task_map_ref") or ""),
                "supersedes_task_map_ref": str(
                    payload.get("supersedes_task_map_ref") or ""
                ),
                "idempotency_key": str(payload.get("idempotency_key") or ""),
            })
        elif event.type == "replan.contract_eval.adoption_blocked":
            status = "blocked"
    latest = evals[-1] if evals else {}
    if status == "none" and latest:
        decision = str(latest.get("decision") or "")
        status = "ready_to_adopt" if decision == "adopt" else decision or "evaluated"
    return {
        "schema_version": "replan-contract-gate-projection.v1",
        "status": status,
        "latest_eval": latest,
        "eval_count": len(evals),
        "adoption_state": status,
        "adoption_events": adoption_events[-20:],
    }


def _replan_event_linked(
    *,
    payload: dict[str, Any],
    nested_eval: dict[str, Any],
    feature_id: str,
    task_map_ref: str,
) -> bool:
    if feature_id and str(payload.get("feature_id") or nested_eval.get("feature_id") or "") == feature_id:
        return True
    refs = {
        str(payload.get("task_map_ref") or ""),
        str(payload.get("old_task_map_ref") or ""),
        str(payload.get("new_task_map_ref") or ""),
        str(payload.get("expected_current_task_map_ref") or ""),
        str(nested_eval.get("old_task_map_ref") or ""),
        str(nested_eval.get("new_task_map_ref") or ""),
        str(nested_eval.get("expected_current_task_map_ref") or ""),
    }
    return bool(task_map_ref and task_map_ref in refs)


def _artifact_ref(payload: dict[str, Any]) -> str:
    direct = str(payload.get("artifact_ref") or "").strip()
    if direct:
        return direct
    refs = payload.get("refs")
    if isinstance(refs, dict):
        return str(refs.get("artifact_ref") or "").strip()
    return ""
