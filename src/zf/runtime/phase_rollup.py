"""Phase-level rollup over an execution-graph (doc 69 §3.2/§4/§6, slice S-c).

Groups execution-graph nodes by delivery `phase` (the semantic stage a feature
is split into, orthogonal to wave) and computes per-phase metrics:
completion_rate, pass_rate, eval verdict rollup, rework/pause counts. Pure
function over already-built inputs; counts kernel verdicts, never re-judges
(守 I1/I2/I7).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.execution_graph import gate_outcomes_by_task
from zf.runtime.workflow_run import build_workflow_run

EventSlice = Sequence[tuple[int, ZfEvent]]

_DONE_STATES = {"done", "cancelled"}
_IN_PROGRESS_STATES = {"in_progress", "review", "test", "judge", "dispatched"}
_GATE_FAMILIES = ("judge", "discriminator", "review", "test", "static")
_REWORK_TYPES = {"task.rework.requested", "task.fix_spawned"}


def build_phase_rollups(
    *,
    graph: dict[str, Any],
    events: EventSlice = (),
    tasks: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return phases[] rollup. `tasks` (kanban) used only for actual-phase fallback."""
    nodes = graph.get("nodes", [])
    gate_outcomes = gate_outcomes_by_task(events)
    rework_by_task, pause_by_task = _lifecycle_counts(events)

    by_phase: dict[str, list[dict[str, Any]]] = {}
    min_wave: dict[str, int] = {}
    for node in nodes:
        phase = _phase_of(node, tasks)
        by_phase.setdefault(phase, []).append(node)
        wave = int(node.get("planned", {}).get("wave") or 0)
        min_wave[phase] = min(min_wave.get(phase, wave), wave)

    # phases roughly progress with waves; order by (min wave, phase_id)
    ordered = sorted(by_phase, key=lambda p: (min_wave.get(p, 0), p))
    out: list[dict[str, Any]] = []
    for order, phase_id in enumerate(ordered):
        pnodes = by_phase[phase_id]
        out.append(_one_phase(
            phase_id, order, pnodes, gate_outcomes, rework_by_task, pause_by_task,
            events,
        ))
    return out


def _agent_runs(pnodes: list[dict[str, Any]], events: EventSlice) -> list[dict[str, Any]]:
    """doc 69 §5/§12 (S-d): per-task fanout/workflow runs (the agent tree).

    Joins each node's fanout_ids (set by execution_graph via the trigger-event
    chain) to a compact workflow-run summary. Topology(line/star/fanout_reader/
    writer) comes from the run; reused build_workflow_run, no re-derivation."""
    runs: list[dict[str, Any]] = []
    for node in pnodes:
        for fanout_id in node.get("actual", {}).get("fanout_ids", []) or []:
            run = build_workflow_run(fanout_id=fanout_id, events=events)
            launched = sum(1 for o in run.get("launch_outcomes", []) if o.get("dispatched"))
            runs.append({
                "task_id": node["task_id"],
                "fanout_id": fanout_id,
                "topology": run.get("topology", ""),
                "status": run.get("status", ""),
                "launched": launched,
                "expected": len(run.get("launch_outcomes", [])),
                "executed": len(run.get("execution_outcomes", [])),
                "workflow_run_ref": fanout_id,
            })
    return runs


def _one_phase(phase_id, order, pnodes, gate_outcomes, rework_by_task, pause_by_task, events):
    task_count = len(pnodes)
    statuses = [_status(n) for n in pnodes]
    done_count = sum(1 for s in statuses if s in _DONE_STATES)

    passed = failed = 0
    eval_rollup = {fam: {"passed": 0, "failed": 0} for fam in _GATE_FAMILIES}
    rework_count = paused_count = 0
    lifecycle_events: list[dict[str, str]] = []
    for node in pnodes:
        tid = node["task_id"]
        for fam, outcome in gate_outcomes.get(tid, {}).items():
            bucket = eval_rollup.setdefault(fam, {"passed": 0, "failed": 0})
            if outcome == "pass":
                passed += 1
                bucket["passed"] += 1
            else:
                failed += 1
                bucket["failed"] += 1
        rework_count += rework_by_task.get(tid, 0)
        paused_count += pause_by_task.get(tid, 0)
        if rework_by_task.get(tid):
            lifecycle_events.append({"task_id": tid, "kind": "rework"})
        if pause_by_task.get(tid):
            lifecycle_events.append({"task_id": tid, "kind": "pause"})

    total_gates = passed + failed
    pass_rate = round(passed / total_gates, 4) if total_gates else None
    has_pending = any(s not in _DONE_STATES for s in statuses)
    verdict = _verdict(passed, failed, has_pending)
    completion_rate = round(done_count / task_count, 4) if task_count else 0.0
    # doc 69 §14.3: phase-level affinity health
    drifted = sum(1 for n in pnodes if n.get("actual", {}).get("affinity", {}).get("drifted"))
    affinity = {"status": "drifted" if drifted else "stable", "drifted_count": drifted}

    return redact_obj({
        "phase_id": phase_id,
        "order": order,
        "status": _phase_status(statuses, rework_count),
        "task_count": task_count,
        "done_count": done_count,
        "completion_rate": completion_rate,
        "pass_rate": pass_rate,
        "eval": {**eval_rollup, "verdict": verdict},
        "rework_count": rework_count,
        "paused_count": paused_count,
        "lifecycle_events": lifecycle_events,
        "agent_runs": _agent_runs(pnodes, events),
        "affinity": affinity,
        "task_ids": [n["task_id"] for n in pnodes],
    })


def _phase_of(node: dict[str, Any], tasks: dict[str, Any] | None) -> str:
    planned = node.get("planned", {}) or {}
    phase = str(planned.get("phase") or "").strip()
    if phase:
        return phase
    if tasks is not None:
        task = tasks.get(node.get("task_id"))
        contract_phase = str(getattr(getattr(task, "contract", None), "phase", "") or "").strip()
        if contract_phase:
            return contract_phase
    return "default"


def _lifecycle_counts(events: EventSlice) -> tuple[dict[str, int], dict[str, int]]:
    rework: dict[str, int] = {}
    pause: dict[str, int] = {}
    for _seq, event in events:
        tid = str(event.task_id or "").strip()
        if not tid:
            continue
        if event.type in _REWORK_TYPES:
            rework[tid] = rework.get(tid, 0) + 1
        elif event.type == "dispatch.paused":  # one episode per paused
            pause[tid] = pause.get(tid, 0) + 1
    return rework, pause


def _verdict(passed: int, failed: int, has_pending: bool) -> str:
    if has_pending:
        return "pending"
    if failed and passed:
        return "mixed"
    if failed:
        return "fail"
    if passed:
        return "pass"
    return "pending"  # no gates, nothing terminal-with-evidence


def _phase_status(statuses: list[str], rework_count: int) -> str:
    if statuses and all(s in _DONE_STATES for s in statuses):
        return "done"
    if rework_count and not all(s in _DONE_STATES for s in statuses):
        return "rework"
    if any(s in _IN_PROGRESS_STATES for s in statuses):
        return "in_progress"
    if any(s == "blocked" for s in statuses):
        return "blocked"
    return "waiting"


def _status(node: dict[str, Any]) -> str:
    return str(node.get("actual", {}).get("status") or "")
