"""Verification rows for Loop action closure."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.delivery_projection_common import dedupe, payload

EventSlice = Sequence[tuple[int, ZfEvent]]

LOOP_VERIFY_REQUESTED = "loop.verify.requested"
LOOP_VERIFY_COMPLETED = "loop.verify.completed"


@dataclass(frozen=True)
class VerifyOutcome:
    status: str
    reason: str
    evidence_refs: list[str]
    missing_evidence: list[str]
    next_check: str


def build_loop_verifications(
    *,
    events: EventSlice,
    actions: list[dict[str, Any]],
    loops: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build explicit and deterministic verification rows."""

    records: dict[str, dict[str, Any]] = {}
    by_action = {str(item.get("action_id") or ""): item for item in actions}
    by_loop = {str(item.get("loop_id") or ""): item for item in loops}
    by_candidate = {str(item.get("candidate_id") or ""): item for item in candidates}
    event_by_id = {str(event.id or ""): event for _seq, event in events if event.id}

    for _seq, event in events:
        data = payload(event)
        if event.type == LOOP_VERIFY_REQUESTED:
            verification_id = str(data.get("verification_id") or event.id or "")
            if not verification_id:
                continue
            record = _ensure(records, verification_id)
            record.update({
                "verification_id": verification_id,
                "action_id": str(data.get("action_id") or ""),
                "source_action_id": str(data.get("source_action_id") or data.get("action_id") or ""),
                "loop_id": str(data.get("loop_id") or ""),
                "candidate_id": str(data.get("candidate_id") or ""),
                "status": "pending",
                "result": "pending",
                "request_event_id": event.id,
                "requested_at": event.ts,
                "updated_at": event.ts,
                "reason": str(data.get("reason") or ""),
                "next_check": str(data.get("next_check") or ""),
            })
            _extend(record, "event_ids", [event.id])
            _extend(record, "evidence_refs", _string_list(data.get("evidence_refs")))
            _extend(record, "missing_evidence", _string_list(data.get("missing_evidence")))
            continue
        if event.type == LOOP_VERIFY_COMPLETED:
            verification_id = str(data.get("verification_id") or "")
            if not verification_id:
                verification_id = f"lv-{_stable_id(data.get('source_loop_action_id'), event.id)}"
            record = _ensure(records, verification_id)
            status = str(data.get("status") or data.get("result") or "inconclusive")
            record.update({
                "verification_id": verification_id,
                "action_id": str(data.get("action_id") or data.get("source_loop_action_id") or record.get("action_id") or ""),
                "source_action_id": str(data.get("source_action_id") or data.get("action_id") or data.get("source_loop_action_id") or record.get("source_action_id") or ""),
                "loop_id": str(data.get("loop_id") or record.get("loop_id") or ""),
                "candidate_id": str(data.get("candidate_id") or record.get("candidate_id") or ""),
                "status": _normalize_status(status),
                "result": _normalize_status(status),
                "completed_event_id": event.id,
                "updated_at": event.ts,
                "reason": str(data.get("reason") or data.get("outcome") or ""),
                "terminal_event_id": str(data.get("terminal_event_id") or record.get("terminal_event_id") or ""),
                "terminal_event_type": str(data.get("terminal_event_type") or record.get("terminal_event_type") or ""),
                "next_check": str(data.get("next_check") or record.get("next_check") or ""),
            })
            _extend(record, "event_ids", [event.id])
            _extend(record, "evidence_refs", _string_list(data.get("evidence_refs") or data.get("artifact_refs")))
            _extend(record, "missing_evidence", _string_list(data.get("missing_evidence")))

    for action in actions:
        action_id = str(action.get("action_id") or "")
        if not action_id or not action.get("terminal_event_id"):
            continue
        verification_id = f"lv-{_stable_id(action_id, action.get('terminal_event_id'))}"
        existing = records.get(verification_id)
        if existing is not None and existing.get("completed_event_id"):
            continue
        candidate = by_candidate.get(str(action.get("candidate_id") or "")) or {}
        loop = by_loop.get(str(action.get("loop_id") or "")) or {}
        terminal = event_by_id.get(str(action.get("terminal_event_id") or ""))
        outcome = _evaluate(action, candidate, loop, terminal, events)
        record = _ensure(records, verification_id)
        record.update({
            "verification_id": verification_id,
            "action_id": action_id,
            "source_action_id": action_id,
            "loop_id": str(action.get("loop_id") or ""),
            "candidate_id": str(action.get("candidate_id") or ""),
            "status": outcome.status,
            "result": outcome.status,
            "mode": "derived",
            "request_event_id": "",
            "completed_event_id": "",
            "terminal_event_id": str(action.get("terminal_event_id") or ""),
            "terminal_event_type": str(action.get("terminal_event_type") or ""),
            "reason": outcome.reason,
            "missing_evidence": outcome.missing_evidence,
            "next_check": outcome.next_check,
            "task_ids": _string_list(action.get("task_ids")),
            "event_ids": dedupe(_string_list(action.get("event_ids")) + [str(action.get("terminal_event_id") or "")]),
            "evidence_refs": dedupe(_string_list(action.get("evidence_refs")) + outcome.evidence_refs),
            "requested_at": str(action.get("requested_at") or ""),
            "updated_at": str(action.get("updated_at") or ""),
        })

    _fill_from_actions(records, by_action)
    return sorted(records.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)


def attach_verifications(
    *,
    loops: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    verifications: list[dict[str, Any]],
) -> None:
    by_action: dict[str, list[dict[str, Any]]] = {}
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    by_loop: dict[str, list[dict[str, Any]]] = {}
    for row in verifications:
        if row.get("action_id"):
            by_action.setdefault(str(row["action_id"]), []).append(row)
        if row.get("candidate_id"):
            by_candidate.setdefault(str(row["candidate_id"]), []).append(row)
        if row.get("loop_id"):
            by_loop.setdefault(str(row["loop_id"]), []).append(row)

    for action in actions:
        rows = by_action.get(str(action.get("action_id") or ""), [])
        _attach_latest(action, rows)
    for candidate in candidates:
        rows = by_candidate.get(str(candidate.get("candidate_id") or ""), [])
        _attach_latest(candidate, rows)
    for loop in loops:
        rows = by_loop.get(str(loop.get("loop_id") or ""), [])
        if rows:
            _attach_latest(loop, rows)
            statuses = {str(row.get("status") or "") for row in rows}
            if "passed" in statuses and loop.get("status") in {"open", "verifying"}:
                loop["status"] = "recovered"
            elif "failed" in statuses and loop.get("status") == "recovered":
                loop["status"] = "verifying"


def _evaluate(
    action: dict[str, Any],
    candidate: dict[str, Any],
    loop: dict[str, Any],
    terminal: ZfEvent | None,
    events: EventSlice,
) -> VerifyOutcome:
    source_kind = str(candidate.get("source_kind") or action.get("source_kind") or "")
    action_status = str(action.get("status") or "")
    if action_status == "rejected" or (terminal is not None and terminal.type.endswith(".rejected")):
        return VerifyOutcome(
            status="failed",
            reason=_terminal_reason(terminal, action, "action rejected"),
            evidence_refs=[],
            missing_evidence=[],
            next_check="inspect_rejected_action",
        )
    if source_kind == "stuck_worker":
        refs = _recovery_refs(events, _string_list(action.get("task_ids")))
        if refs:
            return VerifyOutcome("passed", "worker liveness recovered after action", refs, [], "")
        return VerifyOutcome(
            "inconclusive",
            "repair applied but worker recovery not observed",
            [],
            ["worker.stuck.recovered"],
            "wait_for_worker_recovery",
        )
    if source_kind in {"gate_failure", "missing_evidence", "rework"}:
        refs = _gate_pass_refs(events, _string_list(action.get("task_ids")))
        if refs:
            return VerifyOutcome("passed", "gate or evidence check passed after action", refs, [], "")
        if terminal is not None and terminal.type in {"static_gate.failed", "verify.failed", "test.failed", "judge.failed"}:
            return VerifyOutcome(
                "failed",
                _terminal_reason(terminal, action, "gate or evidence check failed after action"),
                [],
                [],
                "inspect_failed_gate_evidence",
            )
        return VerifyOutcome(
            "inconclusive",
            "no passing gate or evidence check observed after action",
            [],
            ["static_gate.passed", "verify.passed", "test.passed", "judge.passed"],
            "wait_for_passing_gate_or_evidence",
        )
    if source_kind == "replan":
        decision = _terminal_value(terminal, "decision", "status")
        if decision in {"adopt", "accepted", "approved"}:
            return VerifyOutcome("passed", f"replan decision {decision}", [], [], "")
        if decision in {"reject", "rejected", "blocked"}:
            return VerifyOutcome("failed", f"replan decision {decision}", [], [], "inspect_replan_rejection")
        return VerifyOutcome(
            "inconclusive",
            "replan evaluation did not adopt or reject",
            [],
            ["decision:adopt|accepted|approved|reject|rejected|blocked"],
            "wait_for_replan_decision",
        )
    if source_kind == "autoresearch":
        if terminal is not None and terminal.type == "autoresearch.loop.failed":
            return VerifyOutcome(
                "failed",
                _terminal_reason(terminal, action, "autoresearch loop failed"),
                [],
                [],
                "inspect_autoresearch_failure",
            )
        refs = _terminal_refs(terminal)
        if refs:
            return VerifyOutcome("passed", "autoresearch completed with proposal artifact", refs, [], "")
        return VerifyOutcome(
            "inconclusive",
            "autoresearch completed without proposal artifact refs",
            [],
            ["artifact_ref", "proposal_ref", "candidate_path", "report_ref"],
            "wait_for_autoresearch_artifact",
        )
    if action_status in {"applied", "completed"}:
        return VerifyOutcome("passed", str(action.get("outcome") or "action completed"), [], [], "")
    return VerifyOutcome(
        "inconclusive",
        "no deterministic verification rule matched",
        [],
        ["deterministic_terminal_evidence"],
        "inspect_loop_events",
    )


def _recovery_refs(events: EventSlice, task_ids: list[str]) -> list[str]:
    tasks = set(task_ids)
    refs: list[str] = []
    for _seq, event in events:
        if event.type != "worker.stuck.recovered":
            continue
        data = payload(event)
        if not tasks or event.task_id in tasks or str(data.get("task_id") or "") in tasks:
            refs.append(str(event.id or event.type))
    return dedupe(refs)


def _gate_pass_refs(events: EventSlice, task_ids: list[str]) -> list[str]:
    tasks = set(task_ids)
    refs: list[str] = []
    for _seq, event in events:
        if not event.type.endswith(".passed") and event.type not in {
            "eval.evidence_sufficiency.completed",
            "eval.contract_completeness.completed",
        }:
            continue
        data = payload(event)
        status = str(data.get("status") or data.get("result") or "")
        if event.type.startswith("eval.") and status in {"failed", "blocked"}:
            continue
        if not tasks or event.task_id in tasks or str(data.get("task_id") or "") in tasks:
            refs.append(str(event.id or event.type))
    return dedupe(refs)


def _terminal_value(event: ZfEvent | None, *keys: str) -> str:
    if event is None:
        return ""
    data = payload(event)
    for key in keys:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def _terminal_refs(event: ZfEvent | None) -> list[str]:
    if event is None:
        return []
    data = payload(event)
    refs = []
    for key in ("artifact_ref", "proposal_ref", "candidate_path", "report_ref"):
        value = str(data.get(key) or "")
        if value:
            refs.append(value)
    refs.extend(_string_list(data.get("artifact_refs")))
    refs.extend(_string_list(data.get("proposal_refs")))
    return dedupe(refs)


def _terminal_reason(event: ZfEvent | None, action: dict[str, Any], fallback: str) -> str:
    if event is not None:
        data = payload(event)
        for key in ("reason", "error", "status", "decision", "outcome"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return str(action.get("reason") or action.get("outcome") or fallback)


def _fill_from_actions(records: dict[str, dict[str, Any]], by_action: dict[str, dict[str, Any]]) -> None:
    for record in records.values():
        action = by_action.get(str(record.get("action_id") or ""))
        if not action:
            continue
        if not record.get("source_action_id"):
            record["source_action_id"] = action.get("action_id") or ""
        for key in ("loop_id", "candidate_id"):
            if not record.get(key):
                record[key] = action.get(key) or ""
        _extend(record, "task_ids", _string_list(action.get("task_ids")))
        _extend(record, "evidence_refs", _string_list(action.get("evidence_refs")))


def _ensure(records: dict[str, dict[str, Any]], verification_id: str) -> dict[str, Any]:
    record = records.get(verification_id)
    if record is None:
        record = {
            "verification_id": verification_id,
            "action_id": "",
            "source_action_id": "",
            "loop_id": "",
            "candidate_id": "",
            "status": "pending",
            "result": "pending",
            "mode": "event",
            "request_event_id": "",
            "completed_event_id": "",
            "terminal_event_id": "",
            "terminal_event_type": "",
            "reason": "",
            "missing_evidence": [],
            "next_check": "",
            "task_ids": [],
            "event_ids": [],
            "evidence_refs": [],
            "requested_at": "",
            "updated_at": "",
        }
        records[verification_id] = record
    return record


def _attach_latest(target: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    latest = rows[0]
    target["verification_ids"] = [
        str(row.get("verification_id") or "") for row in rows if row.get("verification_id")
    ]
    target["latest_verification_id"] = latest.get("verification_id") or ""
    target["latest_verification_status"] = latest.get("status") or ""


def _extend(row: dict[str, Any], key: str, values: list[str]) -> None:
    row[key] = dedupe([*row.get(key, []), *values])


def _normalize_status(value: str) -> str:
    text = value.strip().lower()
    if text in {"pass", "passed", "success", "ok", "accepted", "adopt"}:
        return "passed"
    if text in {"fail", "failed", "reject", "rejected", "blocked"}:
        return "failed"
    return "inconclusive" if text not in {"pending", "running"} else text


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _stable_id(*parts: object) -> str:
    raw = ":".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
