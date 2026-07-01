"""Loop projection (doc94).

``loop.v1`` is a read-only projection over EventLog events. It groups owner
signals such as gate failures, rework, stuck workers, fanout retry markers,
autoresearch, and replan events into feedback loops. It never writes runtime
truth and does not re-judge outcomes beyond mapping existing event status.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.delivery_projection_common import dedupe, event_status, payload
from zf.runtime.loop_diagnosis import attach_loop_diagnoses
from zf.runtime.loop_learning import build_loop_learning
from zf.runtime.loop_projection_signals import (
    RECOVERY_EVENTS,
    autoresearch_key as _autoresearch_key,
    autoresearch_status as _autoresearch_status,
    event_key as _event_key,
    fanout_ids as _fanout_ids,
    fanout_key as _fanout_key,
    fanout_loop_status as _fanout_loop_status,
    feature_ids as _feature_ids,
    gate_key as _gate_key,
    is_fanout_retry as _is_fanout_retry,
    is_gate_failure as _is_gate_failure,
    is_gate_recovery as _is_gate_recovery,
    is_missing_evidence as _is_missing_evidence,
    replan_key as _replan_key,
    replan_status as _replan_status,
    signal_summary as _summary,
    stable_id as _stable_id,
    suggested_action as _suggested_action,
    task_ids as _task_ids,
    trace_ids as _trace_ids,
    worker_key as _worker_key,
)
from zf.runtime.loop_verify import attach_verifications, build_loop_verifications

EventSlice = Sequence[tuple[int, ZfEvent]]


def build_loop_projection(
    *,
    events: EventSlice,
    generated_at: str,
    project_id: str = "",
) -> dict[str, Any]:
    """Build ``loop.v1`` from an already-loaded event slice."""

    loops: dict[str, dict[str, Any]] = {}
    behaviors: list[dict[str, Any]] = []
    evals: list[dict[str, Any]] = []
    candidates: dict[str, dict[str, Any]] = {}
    source_event_ids: list[str] = []

    for _seq, event in events:
        data = payload(event)
        if event.id:
            source_event_ids.append(event.id)

        if _is_gate_failure(event):
            loop = _upsert_loop(
                loops,
                key=_gate_key(event),
                kind="gate_failure",
                event=event,
                data=data,
                status="open",
                title="Gate failure",
                summary=_summary(data, "gate failure"),
            )
            _attach_eval(evals, loop, "functional_check", event, data, "failed")
            _attach_candidate(candidates, loop, source_kind="gate_failure")
            continue

        if _is_gate_recovery(event):
            loop = loops.get(_gate_key(event))
            if loop is not None:
                _update_loop(loop, event, data, status="recovered")
                _attach_eval(evals, loop, "functional_check", event, data, "passed")
            continue

        if _is_missing_evidence(event, data):
            loop = _upsert_loop(
                loops,
                key=_event_key("missing_evidence", event, data),
                kind="missing_evidence",
                event=event,
                data=data,
                status="open",
                title="Missing evidence",
                summary=_summary(data, "required evidence is missing"),
            )
            _attach_behavior(behaviors, loop, "missing_evidence", event, data, "failed")
            _attach_candidate(candidates, loop, source_kind="missing_evidence")
            continue

        if event.type == "task.rework.requested":
            loop = _upsert_loop(
                loops,
                key=_event_key("rework", event, data),
                kind="rework",
                event=event,
                data=data,
                status="verifying",
                title="Rework",
                summary=_summary(data, "rework requested"),
            )
            _attach_candidate(candidates, loop, source_kind="rework")
            continue

        if event.type == "task.rework.capped":
            loop = _upsert_loop(
                loops,
                key=_event_key("rework", event, data),
                kind="rework",
                event=event,
                data=data,
                status="exhausted",
                title="Rework capped",
                summary=_summary(data, "rework exhausted"),
            )
            _attach_candidate(candidates, loop, source_kind="rework")
            continue

        if event.type in {"worker.stuck", "worker.probe.silent"}:
            status = "open" if event.type == "worker.stuck" else "verifying"
            loop = _upsert_loop(
                loops,
                key=_worker_key(event, data),
                kind="stuck_worker",
                event=event,
                data=data,
                status=status,
                title="Stuck worker",
                summary=_summary(data, "worker heartbeat stalled"),
            )
            _attach_behavior(behaviors, loop, "stuck_worker", event, data, event_status(event))
            _attach_candidate(candidates, loop, source_kind="stuck_worker")
            continue

        if event.type == "worker.stuck.recovered":
            loop = loops.get(_worker_key(event, data))
            if loop is not None:
                _update_loop(loop, event, data, status="recovered")
            continue

        if _is_fanout_retry(event, data):
            loop = _upsert_loop(
                loops,
                key=_fanout_key(event, data),
                kind="fanout_retry",
                event=event,
                data=data,
                status=_fanout_loop_status(event, data),
                title="Fanout retry",
                summary=_summary(data, "fanout retry observed"),
            )
            _attach_candidate(candidates, loop, source_kind="fanout_retry")
            continue

        if event.type.startswith("autoresearch."):
            loop = _upsert_loop(
                loops,
                key=_autoresearch_key(event, data),
                kind="autoresearch",
                event=event,
                data=data,
                status=_autoresearch_status(event),
                title="Autoresearch",
                summary=_summary(data, "autoresearch loop"),
            )
            if event_status(event) in {"failed", "blocked"} or event.type.endswith(".failed"):
                _attach_candidate(candidates, loop, source_kind="autoresearch")
            continue

        if event.type.startswith("replan."):
            loop = _upsert_loop(
                loops,
                key=_replan_key(event, data),
                kind="replan",
                event=event,
                data=data,
                status=_replan_status(event, data),
                title="Replan",
                summary=_summary(data, "replan loop"),
            )
            if loop["status"] in {"open", "verifying", "exhausted"}:
                _attach_candidate(candidates, loop, source_kind="replan")
            continue

        if event.type in RECOVERY_EVENTS and event.task_id:
            _recover_task_loops(loops, event, data)

    diagnoses = attach_loop_diagnoses(loops=loops, candidates=candidates)
    actions = _action_rows(events)
    _attach_actions_to_candidates(candidates, actions)
    loop_list = sorted(
        loops.values(),
        key=lambda item: (str(item.get("updated_at") or ""), str(item.get("loop_id") or "")),
        reverse=True,
    )
    candidate_list = sorted(candidates.values(), key=lambda item: str(item.get("candidate_id") or ""))
    verifications = build_loop_verifications(
        events=events,
        actions=actions,
        loops=loop_list,
        candidates=candidate_list,
    )
    attach_verifications(
        loops=loop_list,
        candidates=candidate_list,
        actions=actions,
        verifications=verifications,
    )
    learning = build_loop_learning(
        events=list(events),
        loops=loop_list,
        candidates=candidate_list,
        verifications=verifications,
    )
    result = {
        "schema_version": "loop.v1",
        "generated_at": generated_at,
        "project_id": project_id,
        "summary": _summary_counts(loop_list, behaviors, evals, candidates, actions, verifications, learning),
        "loops": loop_list,
        "behaviors": behaviors,
        "evals": evals,
        "diagnoses": diagnoses,
        "candidates": candidate_list,
        "actions": actions,
        "verifications": verifications,
        "learning": learning,
        "source_event_ids": dedupe(source_event_ids),
        "diagnostics": [],
    }
    return redact_obj(result)


def related_loop_ids_for_delivery_trace(
    *,
    trace: dict[str, Any],
    loop_projection: dict[str, Any],
) -> list[str]:
    """Return loop ids related to a delivery trace without copying loop bodies."""

    feature_id = str(trace.get("feature_id") or "")
    trace_id = str(trace.get("trace_id") or "")
    task_ids = {
        str(node.get("task_id") or "")
        for node in ((trace.get("execution_graph") or {}).get("nodes") or [])
        if isinstance(node, dict)
    }
    task_ids.discard("")
    related: list[str] = []
    for loop in loop_projection.get("loops") or []:
        if not isinstance(loop, dict):
            continue
        loop_id = str(loop.get("loop_id") or "")
        if not loop_id:
            continue
        if feature_id and feature_id in set(_string_list(loop.get("feature_ids"))):
            related.append(loop_id)
            continue
        if trace_id and trace_id in set(_string_list(loop.get("trace_ids"))):
            related.append(loop_id)
            continue
        if task_ids.intersection(_string_list(loop.get("task_ids"))):
            related.append(loop_id)
    return dedupe(related)


def _upsert_loop(
    loops: dict[str, dict[str, Any]],
    *,
    key: str,
    kind: str,
    event: ZfEvent,
    data: dict[str, Any],
    status: str,
    title: str,
    summary: str,
) -> dict[str, Any]:
    loop = loops.get(key)
    if loop is None:
        loop = {
            "loop_id": f"loop:{kind}:{_stable_id(key)}",
            "kind": kind,
            "status": status,
            "title": title,
            "summary": summary,
            "task_ids": [],
            "feature_ids": [],
            "fanout_ids": [],
            "trace_ids": [],
            "event_ids": [],
            "source_event_types": [],
            "behavior_ids": [],
            "eval_ids": [],
            "candidate_ids": [],
            "started_at": event.ts,
            "updated_at": event.ts,
            "trigger_event_id": event.id,
        }
        loops[key] = loop
    _update_loop(loop, event, data, status=status, summary=summary)
    return loop


def _update_loop(
    loop: dict[str, Any],
    event: ZfEvent,
    data: dict[str, Any],
    *,
    status: str,
    summary: str = "",
) -> None:
    loop["status"] = status
    loop["updated_at"] = event.ts or loop.get("updated_at") or ""
    if summary:
        loop["summary"] = summary
    _extend(loop, "event_ids", [event.id])
    _extend(loop, "source_event_types", [event.type])
    _extend(loop, "task_ids", _task_ids(event, data))
    _extend(loop, "feature_ids", _feature_ids(data))
    _extend(loop, "fanout_ids", _fanout_ids(data))
    _extend(loop, "trace_ids", _trace_ids(event, data))


def _attach_behavior(
    rows: list[dict[str, Any]],
    loop: dict[str, Any],
    kind: str,
    event: ZfEvent,
    data: dict[str, Any],
    status: str,
) -> None:
    behavior_id = f"behavior:{kind}:{_stable_id(event.id, loop['loop_id'])}"
    row = {
        "behavior_id": behavior_id,
        "loop_id": loop["loop_id"],
        "kind": kind,
        "status": status,
        "task_ids": _task_ids(event, data),
        "event_ids": [event.id] if event.id else [],
        "summary": _summary(data, kind),
        "owner_event_type": event.type,
        "detector": str(data.get("detector") or event.actor or ""),
    }
    rows.append(row)
    _extend(loop, "behavior_ids", [behavior_id])


def _attach_eval(
    rows: list[dict[str, Any]],
    loop: dict[str, Any],
    kind: str,
    event: ZfEvent,
    data: dict[str, Any],
    status: str,
) -> None:
    eval_id = f"eval:{kind}:{_stable_id(event.id, loop['loop_id'])}"
    row = {
        "eval_id": eval_id,
        "loop_id": loop["loop_id"],
        "kind": kind,
        "status": status,
        "task_ids": _task_ids(event, data),
        "event_ids": [event.id] if event.id else [],
        "score": data.get("score"),
        "detail": data.get("detail") or data.get("reason") or data.get("failed_checks") or {},
        "evaluator": str(data.get("evaluator") or event.actor or ""),
        "owner_event_type": event.type,
    }
    rows.append(row)
    _extend(loop, "eval_ids", [eval_id])


def _attach_candidate(candidates: dict[str, dict[str, Any]], loop: dict[str, Any], *, source_kind: str) -> None:
    fingerprint = _stable_id(source_kind, ",".join(loop.get("task_ids") or []), ",".join(loop.get("feature_ids") or []))
    candidate_id = f"candidate:{source_kind}:{fingerprint}"
    candidates[candidate_id] = {
        "candidate_id": candidate_id,
        "loop_id": loop["loop_id"],
        "kind": "improvement_candidate",
        "source_kind": source_kind,
        "status": "candidate",
        "fingerprint": fingerprint,
        "task_ids": list(loop.get("task_ids") or []),
        "event_ids": list(loop.get("event_ids") or []),
        "summary": f"Improve delivery loop for {source_kind}",
        "suggested_action": _suggested_action(source_kind),
    }
    _extend(loop, "candidate_ids", [candidate_id])


def _summary_counts(
    loops: list[dict[str, Any]],
    behaviors: list[dict[str, Any]],
    evals: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    actions: list[dict[str, Any]] | None = None,
    verifications: list[dict[str, Any]] | None = None,
    learning: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    counts = {"open": 0, "verifying": 0, "recovered": 0, "exhausted": 0}
    by_kind: dict[str, int] = {}
    for loop in loops:
        status = str(loop.get("status") or "open")
        counts[status] = counts.get(status, 0) + 1
        kind = str(loop.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "total": len(loops),
        **counts,
        "behavior_count": len(behaviors),
        "eval_count": len(evals),
        "candidate_count": len(candidates),
        "action_count": len(actions or []),
        "verification_count": len(verifications or []),
        "learning_count": len(learning or []),
        "by_kind": by_kind,
    }


def _recover_task_loops(loops: dict[str, dict[str, Any]], event: ZfEvent, data: dict[str, Any]) -> None:
    event_tasks = set(_task_ids(event, data))
    if not event_tasks:
        return
    for loop in loops.values():
        if loop.get("status") not in {"open", "verifying"}:
            continue
        if str(loop.get("kind") or "") not in {"gate_failure", "rework", "missing_evidence"}:
            continue
        if event_tasks.intersection(_string_list(loop.get("task_ids"))):
            _update_loop(loop, event, data, status="recovered")


def _action_rows(events: EventSlice) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    downstream_index: dict[str, str] = {}
    for _seq, event in events:
        data = payload(event)
        if event.type == "loop.action.requested":
            action_id = str(data.get("action_id") or event.id or "")
            if not action_id:
                continue
            record = _ensure_action(records, action_id)
            record.update({
                "action_id": action_id,
                "loop_id": str(data.get("loop_id") or ""),
                "candidate_id": str(data.get("candidate_id") or ""),
                "suggested_action": str(data.get("suggested_action") or ""),
                "source_kind": str(data.get("source_kind") or ""),
                "status": "pending",
                "request_event_id": event.id,
                "requested_at": event.ts,
                "updated_at": event.ts,
                "idempotency_key": str(data.get("idempotency_key") or ""),
                "proposal_only": bool(data.get("proposal_only")),
            })
            _extend(record, "event_ids", [event.id])
            _extend(record, "task_ids", _task_ids(event, data))
            _extend(record, "evidence_refs", _string_list(data.get("evidence_refs")))
            continue

        if event.type == "loop.action.mapped":
            action_id = str(data.get("action_id") or "")
            if not action_id:
                continue
            record = _ensure_action(records, action_id)
            record.update({
                "action_id": action_id,
                "loop_id": str(data.get("loop_id") or record.get("loop_id") or ""),
                "candidate_id": str(data.get("candidate_id") or record.get("candidate_id") or ""),
                "suggested_action": str(data.get("suggested_action") or record.get("suggested_action") or ""),
                "status": "mapped",
                "mapped_event_id": str(data.get("mapped_event_id") or ""),
                "mapped_event_type": str(data.get("mapped_event_type") or ""),
                "mapped_action": str(data.get("mapped_action") or ""),
                "downstream_action_id": str(data.get("downstream_action_id") or ""),
                "proposal_only": bool(data.get("proposal_only")),
                "updated_at": event.ts,
            })
            _extend(record, "event_ids", [event.id, str(data.get("mapped_event_id") or "")])
            _extend(record, "evidence_refs", _string_list(data.get("evidence_refs")))
            mapped_event_id = str(data.get("mapped_event_id") or "")
            if mapped_event_id:
                downstream_index[mapped_event_id] = action_id
            continue

        if event.type == "loop.action.rejected":
            action_id = str(data.get("action_id") or "")
            if not action_id:
                continue
            record = _ensure_action(records, action_id)
            record.update({
                "action_id": action_id,
                "loop_id": str(data.get("loop_id") or record.get("loop_id") or ""),
                "candidate_id": str(data.get("candidate_id") or record.get("candidate_id") or ""),
                "suggested_action": str(data.get("suggested_action") or record.get("suggested_action") or ""),
                "status": "rejected",
                "terminal_event_id": event.id,
                "terminal_event_type": event.type,
                "reason": str(data.get("reason") or ""),
                "updated_at": event.ts,
            })
            _extend(record, "event_ids", [event.id])
            continue

        action_id = _terminal_action_id(event, data, downstream_index)
        if action_id:
            record = _ensure_action(records, action_id)
            status, outcome = _terminal_status(event, data)
            record.update({
                "status": status,
                "terminal_event_id": event.id,
                "terminal_event_type": event.type,
                "outcome": outcome,
                "reason": str(data.get("reason") or data.get("status") or data.get("decision") or ""),
                "updated_at": event.ts,
            })
            _extend(record, "event_ids", [event.id])

    return sorted(records.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def _ensure_action(records: dict[str, dict[str, Any]], action_id: str) -> dict[str, Any]:
    record = records.get(action_id)
    if record is None:
        record = {
            "action_id": action_id,
            "loop_id": "",
            "candidate_id": "",
            "suggested_action": "",
            "source_kind": "",
            "status": "pending",
            "request_event_id": "",
            "mapped_event_id": "",
            "mapped_event_type": "",
            "mapped_action": "",
            "downstream_action_id": "",
            "terminal_event_id": "",
            "terminal_event_type": "",
            "outcome": "",
            "reason": "",
            "task_ids": [],
            "event_ids": [],
            "evidence_refs": [],
            "proposal_only": False,
            "requested_at": "",
            "updated_at": "",
            "idempotency_key": "",
        }
        records[action_id] = record
    return record


def _terminal_action_id(event: ZfEvent, data: dict[str, Any], downstream_index: dict[str, str]) -> str:
    direct = str(data.get("source_loop_action_id") or "")
    if direct:
        return direct
    if event.causation_id and event.causation_id in downstream_index:
        return downstream_index[event.causation_id]
    return ""


def _terminal_status(event: ZfEvent, data: dict[str, Any]) -> tuple[str, str]:
    if event.type.endswith(".rejected") or event.type.endswith(".failed"):
        return "rejected", str(data.get("reason") or data.get("status") or "failed")
    if event.type == "replan.contract_eval.completed":
        decision = str(data.get("decision") or data.get("status") or "")
        if decision in {"reject", "rejected", "blocked"}:
            return "rejected", decision
        if decision in {"adopt", "accepted", "approved"}:
            return "applied", decision
        return "completed", decision or "completed"
    if event.type.endswith(".completed"):
        return "completed", str(data.get("status") or "completed")
    if event.type.endswith(".applied"):
        return "applied", str(data.get("reason") or "applied")
    if event.type.endswith(".started") or event.type.endswith(".accepted"):
        return "running", str(data.get("status") or "running")
    return "mapped", str(data.get("status") or "")


def _attach_actions_to_candidates(
    candidates: dict[str, dict[str, Any]],
    actions: list[dict[str, Any]],
) -> None:
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for action in actions:
        candidate_id = str(action.get("candidate_id") or "")
        if candidate_id:
            by_candidate.setdefault(candidate_id, []).append(action)
    for candidate_id, rows in by_candidate.items():
        candidate = candidates.get(candidate_id)
        if candidate is None:
            continue
        candidate["action_ids"] = [str(row.get("action_id") or "") for row in rows if row.get("action_id")]
        latest = rows[0] if rows else {}
        if latest:
            candidate["latest_action_status"] = latest.get("status") or ""
            candidate["latest_action_id"] = latest.get("action_id") or ""


def _extend(row: dict[str, Any], key: str, values: Sequence[str]) -> None:
    row[key] = dedupe([*row.get(key, []), *values])


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]
