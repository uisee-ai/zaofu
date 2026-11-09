"""Recoverable Run Manager gate for contract-required human evidence."""

from __future__ import annotations

import hashlib
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore


_EXTERNAL_EVIDENCE_FAILURES = frozenset({
    "external_gate",
    "manual_evidence_required",
})


def pending_manual_evidence_gate_action(
    task_store: TaskStore | None,
    events: list[ZfEvent],
    failed_event: ZfEvent | None,
    triage_event: ZfEvent,
) -> tuple[bool, dict[str, Any] | None]:
    """Return whether this is a manual gate and its pending action, if any."""

    if task_store is None or failed_event is None:
        return False, None
    failed_payload = _payload(failed_event)
    triage_payload = _payload(triage_event)
    task_id = str(
        triage_event.task_id or triage_payload.get("task_id") or ""
    ).strip()
    if not task_id:
        return False, None
    try:
        task = task_store.get(task_id)
    except Exception:
        return False, None
    if task is None:
        return False, None
    if str(getattr(task, "status", "") or "").strip().lower() not in {
        "blocked",
        "in_progress",
    }:
        return False, None
    evidence_contract = getattr(task.contract, "evidence_contract", {}) or {}
    if not isinstance(evidence_contract, dict):
        return False, None
    required_refs = _evidence_refs(
        evidence_contract.get("required_manual_evidence")
    )
    if not required_refs:
        required_refs = _manual_evidence_refs_from_validation(task.contract)
    if not required_refs:
        return False, None
    failure_markers = {
        str(failed_payload.get(key) or "").strip().lower()
        for key in ("failure_class", "blocker_kind")
    }
    explicit_manual_failure = bool(
        failure_markers.intersection(_EXTERNAL_EVIDENCE_FAILURES)
    )
    structured_manual_failure = _structured_manual_evidence_failure(
        task.contract,
        failed_payload,
        triage_payload,
    )
    if not explicit_manual_failure and not structured_manual_failure:
        return False, None

    summary = str(
        failed_payload.get("reason")
        or failed_payload.get("summary")
        or "任务需要由外部参与者提供人工验收证据。"
    )
    workflow_run_id = str(
        failed_payload.get("workflow_run_id")
        or failed_event.correlation_id
        or triage_event.correlation_id
        or ""
    )
    request_id = _stable_id("manual-evidence", task_id, failed_event.id)
    checkpoint_id = _stable_id(
        "manual-evidence-gate",
        task_id,
        failed_event.id,
        triage_event.id,
    )
    if _action_completed(events, checkpoint_id) or _has_later_resolution(
        events, triage_event, task_id
    ):
        return True, None
    fingerprint = str(
        failed_payload.get("failure_fingerprint")
        or failed_payload.get("fingerprint")
        or failed_payload.get("failure_class")
        or failed_event.id
        or triage_event.id
    )
    return True, {
        "schema_version": "run-manager.pending-action.v1",
        "action": "orchestrator-triage-advice-apply",
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": "blocked_external_gate",
        "request_id": request_id,
        "task_id": task_id,
        "workflow_run_id": workflow_run_id,
        "recorded_event_id": triage_event.id,
        "recommended_action": "human",
        "guidance": (
            "按任务合约提供所列人工证据，再由 operator 从当前检查点重验；"
            "不得由 agent 伪造或改写人工结论。"
        ),
        "summary": summary,
        "fingerprint": fingerprint,
        "failure_class": "manual_evidence_required",
        "blocker_kind": "external_gate",
        "failure_count": 1,
        "failure_event_ids": [failed_event.id],
        "source_event_id": failed_event.id,
        "source_event_type": failed_event.type,
        "source_event_ids": [failed_event.id, triage_event.id],
        "owner_route": "human",
        "action_policy": "human_escalate",
        "intervention_class": "manual_review",
        "required_evidence_refs": required_refs,
        "resume_condition": "required_manual_evidence_present_and_reverified",
        "expected_downstream_events": ["human.escalate"],
        "verify_condition": "required_manual_evidence_present_and_reverified",
        "suggested_options": ["provide_required_evidence", "safe_halt"],
        "question": "请按任务合约提供独立人工验收证据，完成后由 operator 恢复重验。",
    }


def _evidence_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return list(dict.fromkeys(
            str(item) for item in value if str(item).strip()
        ))
    if isinstance(value, dict):
        for key in ("refs", "required_refs", "artifacts"):
            refs = _evidence_refs(value.get(key))
            if refs:
                return refs
    return []


def _structured_manual_evidence_failure(
    contract: Any,
    failed_payload: dict[str, Any],
    triage_payload: dict[str, Any],
) -> bool:
    """Recognize a failed contract-owned manual command without text guessing."""

    recommendation = str(
        triage_payload.get("recommended_action") or ""
    ).strip().lower()
    if recommendation != "request_evidence_reissue":
        return False

    validation = getattr(contract, "validation", {}) or {}
    commands = validation.get("commands") if isinstance(validation, dict) else None
    if not isinstance(commands, list):
        return False

    manual_command_ids: set[str] = set()
    manual_acceptance_ids: set[str] = set()
    for command in commands:
        if not isinstance(command, dict):
            continue
        tier = str(command.get("tier") or "").strip().lower()
        owner = str(command.get("owner") or "").strip().lower()
        if tier != "manual_evidence" and owner != "human":
            continue
        command_id = str(command.get("id") or "").strip()
        if command_id:
            manual_command_ids.add(command_id)
        manual_acceptance_ids.update(
            str(item).strip()
            for item in command.get("acceptance_ids") or []
            if str(item).strip()
        )
    if not manual_command_ids and not manual_acceptance_ids:
        return False

    self_check = failed_payload.get("impl_self_check")
    if not isinstance(self_check, dict):
        return False
    receipts = self_check.get("command_receipts")
    if isinstance(receipts, list) and receipts:
        failed_command_ids = {
            str(receipt.get("command_id") or "").strip()
            for receipt in receipts
            if isinstance(receipt, dict)
            and str(receipt.get("status") or "").strip().lower()
            in {"blocked", "failed"}
        }
        return bool(manual_command_ids.intersection(failed_command_ids))

    acceptance_results = self_check.get("acceptance_results")
    if isinstance(acceptance_results, list):
        blocked_acceptance_ids = {
            str(result.get("acceptance_id") or "").strip()
            for result in acceptance_results
            if isinstance(result, dict)
            and str(result.get("status") or "").strip().lower()
            == "blocked"
        }
        if manual_acceptance_ids.intersection(blocked_acceptance_ids):
            return True
    return False


def _manual_evidence_refs_from_validation(contract: Any) -> list[str]:
    validation = getattr(contract, "validation", {}) or {}
    commands = validation.get("commands") if isinstance(validation, dict) else None
    if not isinstance(commands, list):
        return []
    refs: list[str] = []
    for command in commands:
        if not isinstance(command, dict):
            continue
        tier = str(command.get("tier") or "").strip().lower()
        owner = str(command.get("owner") or "").strip().lower()
        if tier != "manual_evidence" and owner != "human":
            continue
        refs.extend(_evidence_refs(command.get("producer_paths")))
    return list(dict.fromkeys(refs))


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _action_completed(events: list[ZfEvent], checkpoint_id: str) -> bool:
    return any(
        event.type in {
            "run.manager.action.applied",
            "run.manager.action.blocked",
            "run.manager.action.failed",
        }
        and str(_payload(event).get("checkpoint_id") or "") == checkpoint_id
        for event in events
    )


def _has_later_resolution(
    events: list[ZfEvent],
    triage_event: ZfEvent,
    task_id: str,
) -> bool:
    seen_triage = False
    for event in events:
        if event.id == triage_event.id:
            seen_triage = True
            continue
        if not seen_triage or str(event.task_id or "") != task_id:
            continue
        if event.type in {
            "dev.build.done",
            "impl.child.completed",
            "verify.passed",
            "test.passed",
            "judge.passed",
            "task.done",
        }:
            return True
    return False


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return prefix + "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


__all__ = ["pending_manual_evidence_gate_action"]
