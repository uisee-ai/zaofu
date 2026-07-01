"""WorkflowRun projection — aggregate one fanout/workflow run (doc 68 S1).

`workflow-run.v1` answers "this workflow request — where is it now?" by
aggregating the scattered `workflow.invoke.*` + `task.fanout.*` + `fanout.*`
events for one `fanout_id` into a single read-only run view.

Critical distinction (doc 68 S1 / 1137): **launch outcomes** (was a child
actually dispatched into runtime?) vs **execution outcomes** (did it complete /
fail?). A Web-recorded `fanout.requested(runtime_delivery=queued_no_runtime)`
is an intent, NOT a launched run — it must not show children as launched.

Pure function over already-read events; writes nothing (守 I1/I2/I7).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj

EventSlice = Sequence[tuple[int, ZfEvent]]

_FANOUT_TERMINAL = {
    "fanout.aggregate.completed", "fanout.synth.completed",
}
_FANOUT_FAILED = {"fanout.timed_out", "fanout.cancelled"}


def build_workflow_run(*, fanout_id: str, events: EventSlice) -> dict[str, Any]:
    """Aggregate all events for one ``fanout_id`` into workflow-run.v1."""

    fanout_events = [
        (seq, e) for seq, e in events
        if _payload(e).get("fanout_id") == fanout_id
    ]
    diagnostics: list[dict[str, str]] = []
    if not fanout_events:
        diagnostics.append({
            "kind": "fanout_not_found",
            "message": f"no events for fanout_id {fanout_id}",
        })
        return redact_obj({
            "schema_version": "workflow-run.v1",
            "fanout_id": fanout_id, "status": "unknown",
            "launch_outcomes": [], "execution_outcomes": [],
            "diagnostics": diagnostics, "source_event_ids": [],
        })

    by_type: dict[str, list[ZfEvent]] = {}
    trace_id = stage_id = topology = target_ref = task_id = ""
    pdd_id = feature_id = task_map_ref = source_index_ref = ""
    for _seq, e in fanout_events:
        by_type.setdefault(e.type, []).append(e)
        p = _payload(e)
        trace_id = trace_id or str(p.get("trace_id") or e.correlation_id or "")
        stage_id = stage_id or str(p.get("stage_id") or "")
        topology = topology or str(p.get("topology") or "")
        target_ref = target_ref or str(p.get("target_ref") or "")
        task_id = task_id or str(e.task_id or p.get("task_id") or "")
        pdd_id = pdd_id or str(p.get("pdd_id") or "")
        feature_id = feature_id or str(p.get("feature_id") or p.get("pdd_id") or "")
        task_map_ref = task_map_ref or str(p.get("task_map_ref") or "")
        source_index_ref = source_index_ref or str(p.get("source_index_ref") or "")

    # workflow.invoke linkage (precedes fanout_id; link by correlation/trace)
    pattern = _pattern(events, trace_id=trace_id, task_id=task_id)

    # recorded-only intent: fanout.requested with no runtime delivery and no
    # started/child.dispatched → NOT launched (doc 68 S1 guard).
    requested = by_type.get("fanout.requested", [])
    recorded_no_runtime = any(
        _payload(e).get("runtime_delivery") == "queued_no_runtime"
        for e in requested
    ) and not by_type.get("fanout.started") and not by_type.get("fanout.child.dispatched")

    launch_outcomes = _launch_outcomes(by_type)
    execution_outcomes = _execution_outcomes(by_type)
    status = _status(by_type, recorded_no_runtime=recorded_no_runtime)
    failure_reasons = [
        str(_payload(e).get("reason") or "")
        for e in by_type.get("fanout.child.failed", [])
        if _payload(e).get("reason")
    ]

    return redact_obj({
        "schema_version": "workflow-run.v1",
        "fanout_id": fanout_id,
        "trace_id": trace_id,
        "stage_id": stage_id,
        "topology": topology,
        "target_ref": target_ref,
        "task_id": task_id,
        "pdd_id": pdd_id,
        "feature_id": feature_id,
        "task_map_ref": task_map_ref,
        "source_index_ref": source_index_ref,
        "pattern": pattern,
        "status": status,
        "recorded_no_runtime": recorded_no_runtime,
        "launch_outcomes": launch_outcomes,
        "execution_outcomes": execution_outcomes,
        "aggregate": {
            "started": bool(by_type.get("fanout.aggregate.started")),
            "completed": bool(by_type.get("fanout.aggregate.completed")),
        },
        "failure_reasons": failure_reasons,
        "source_event_ids": [e.id for _s, e in fanout_events if e.id],
        "diagnostics": diagnostics,
    })


def _pattern(events: EventSlice, *, trace_id: str, task_id: str) -> dict[str, Any]:
    """workflow.invoke.* link — invoke precedes fanout_id, match by trace/task."""
    invoke_status = ""
    pattern_id = ""
    for _seq, e in events:
        if not e.type.startswith("workflow.invoke."):
            continue
        p = _payload(e)
        linked = (
            (trace_id and e.correlation_id == trace_id)
            or (task_id and str(p.get("task_id") or e.task_id or "") == task_id)
        )
        if not linked:
            continue
        pattern_id = pattern_id or str(p.get("pattern_id") or "")
        invoke_status = e.type.split(".")[-1]  # requested/accepted/rejected
    return {"pattern_id": pattern_id, "invoke_status": invoke_status}


def _launch_outcomes(by_type: dict[str, list[ZfEvent]]) -> list[dict[str, Any]]:
    """Was each child actually dispatched into runtime? (launch, not execution)"""
    expected: dict[str, dict[str, Any]] = {}
    for e in by_type.get("fanout.started", []):
        for child in _payload(e).get("expected_children") or []:
            cid = str((child or {}).get("child_id") or "")
            if cid:
                expected[cid] = {"child_id": cid, "dispatched": False,
                                 "role_instance": str((child or {}).get("role_instance") or "")}
    for e in by_type.get("fanout.child.dispatched", []):
        p = _payload(e)
        cid = str(p.get("child_id") or "")
        node = expected.setdefault(cid, {"child_id": cid, "dispatched": False,
                                         "role_instance": ""})
        node["dispatched"] = True
        node["role_instance"] = node["role_instance"] or str(p.get("role_instance") or "")
    return list(expected.values())


def _execution_outcomes(by_type: dict[str, list[ZfEvent]]) -> list[dict[str, Any]]:
    """Did each dispatched child complete / fail? (execution, not launch)"""
    out: dict[str, dict[str, Any]] = {}
    for e in by_type.get("fanout.child.completed", []):
        cid = str(_payload(e).get("child_id") or "")
        out[cid] = {"child_id": cid, "status": "completed", "reason": ""}
    for e in by_type.get("fanout.child.failed", []):
        cid = str(_payload(e).get("child_id") or "")
        # a later completed wins only if it arrived later; keep failed explicit
        out.setdefault(cid, {"child_id": cid, "status": "failed",
                             "reason": str(_payload(e).get("reason") or "")})
        if out[cid]["status"] != "completed":
            out[cid] = {"child_id": cid, "status": "failed",
                        "reason": str(_payload(e).get("reason") or "")}
    return list(out.values())


def _status(by_type: dict[str, list[ZfEvent]], *, recorded_no_runtime: bool) -> str:
    if recorded_no_runtime:
        return "recorded_no_runtime"
    if any(t in by_type for t in _FANOUT_FAILED):
        return "timed_out" if "fanout.timed_out" in by_type else "cancelled"
    if by_type.get("fanout.aggregate.completed") or by_type.get("fanout.synth.completed"):
        return "completed"
    if by_type.get("fanout.aggregate.started"):
        return "aggregating"
    if by_type.get("fanout.child.dispatched") or by_type.get("fanout.started"):
        return "running"
    if by_type.get("fanout.requested"):
        return "requested"
    return "unknown"


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}
