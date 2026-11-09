"""Bound unrecoverable human escalations to one run terminal."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.run_scope import resolve_run_for_event


_LEGACY_UNRECOVERABLE_MARKERS = (
    "stage replan cap exhausted",
    "replan cap exhausted",
    "operator recovery is required",
    "no upstream failure route",
)
_UNRECOVERABLE_FAILURE_CLASSES = frozenset({
    "plan_admission_failed",
    "plan_artifact_package_rejected",
})
_RECOVERY_PROGRESS_EVENTS = frozenset({
    "human.resolved",
    "run.goal.completed",
    "run.goal.blocked",
    "run.goal.updated",
    "workflow.resume.applied",
    "task_map.ready",
    "task_map.amended",
})


def converge_unrecoverable_escalations(
    events: Iterable[ZfEvent],
    *,
    writer: EventWriter,
    task_store: TaskStore | None = None,
    request_autoresearch: bool = False,
) -> int:
    """Converge escalation to terminal, TaskStore, then bounded diagnosis."""

    rows = list(events)
    emitted = 0
    terminalized_runs: set[str] = set()
    by_id = {event.id: event for event in rows}
    from zf.runtime.reader_failure_supersession import reader_failure_was_replaced

    for index, escalation in enumerate(rows):
        if escalation.type != "human.escalate":
            continue
        payload = escalation.payload if isinstance(escalation.payload, dict) else {}
        policy = escalation_terminal_policy(payload)
        if not policy:
            continue
        run_id = resolve_run_for_event(rows, escalation)
        if not run_id:
            continue
        if run_id in terminalized_runs or _run_terminal_exists(
            rows[index + 1:],
            run_id=run_id,
        ):
            continue
        if _recovered_after(rows, index=index, run_id=run_id):
            continue
        source = by_id.get(str(payload.get("source_event_id") or ""))
        if source is not None and reader_failure_was_replaced(rows, source):
            continue
        reason = str(payload.get("reason") or "unrecoverable human escalation")
        source_ids = [
            escalation.id,
            str(payload.get("source_event_id") or ""),
            str(escalation.causation_id or ""),
        ]
        evidence_event_ids = list(dict.fromkeys(
            value for value in source_ids if value
        ))
        fingerprint = _fingerprint(
            run_id=run_id,
            failure_class=str(payload.get("failure_class") or ""),
            reason=reason,
        )
        derived_metadata = escalation_terminal_metadata(
            reason,
            source_event_id=str(payload.get("source_event_id") or ""),
        )
        failure_class = str(
            payload.get("failure_class")
            or derived_metadata.get("failure_class")
            or "operator_recovery_required"
        )
        terminal = writer.emit(
            "run.goal.blocked",
            actor="run-manager",
            task_id=str(escalation.task_id or "") or None,
            causation_id=escalation.id,
            correlation_id=run_id,
            payload={
                "schema_version": "run-goal.terminal.v1",
                "run_id": run_id,
                "workflow_run_id": run_id,
                "status": "blocked",
                "reason": "unrecoverable_human_escalation",
                "detail": reason,
                "failure_class": failure_class,
                "failure_scope": str(payload.get("failure_scope") or "run"),
                "blocker_fingerprint": fingerprint,
                "recovery_owner": str(policy["recovery_owner"]),
                "allowed_actions": list(policy["allowed_actions"]),
                "max_auto_attempts": int(policy["max_auto_attempts"]),
                "max_rescans": int(policy["max_rescans"]),
                "terminalization_condition": str(
                    policy["terminalization_condition"]
                ),
                "operator_required": True,
                "recoverable": False,
                "escalation_event_id": escalation.id,
                "evidence_event_ids": evidence_event_ids,
                "terminal_fallback": "operator_review_or_new_generation",
            },
        )
        if task_store is not None:
            from zf.runtime.workflow_task_lifecycle import (
                settle_workflow_managed_task_from_run_terminal,
            )

            settle_workflow_managed_task_from_run_terminal(
                task_store=task_store,
                event_writer=writer,
                terminal_event=terminal,
            )
        if request_autoresearch and failure_class == (
            "stage_replan_cap_exhausted"
        ):
            _request_stage_cap_autoresearch(
                writer,
                terminal=terminal,
                escalation=escalation,
            )
        emitted += 1
        terminalized_runs.add(run_id)
    return emitted


def converge_stage_replan_cap(
    event: ZfEvent,
    *,
    escalation: Any,
    writer: EventWriter,
    task_store: TaskStore,
) -> bool:
    """Publish a stage-cap escalation and immediately converge its run."""

    try:
        escalation.escalate(
            f"{event.type}: stage replan cap exhausted; "
            "plan/triage output keeps failing admission",
            task_id=str(event.task_id or "") or None,
            metadata={"source_event_id": event.id},
            correlation_id=event.correlation_id,
        )
        converge_unrecoverable_escalations(
            writer.event_log.read_all(),
            writer=writer,
            task_store=task_store,
            request_autoresearch=True,
        )
    except Exception:
        return False
    return True


def _request_stage_cap_autoresearch(
    writer: EventWriter,
    *,
    terminal: ZfEvent,
    escalation: ZfEvent,
) -> None:
    terminal_payload = (
        terminal.payload if isinstance(terminal.payload, dict) else {}
    )
    run_id = str(
        terminal_payload.get("run_id") or terminal.correlation_id or ""
    )
    request_id = "ar-stage-replan-" + hashlib.sha256(
        f"{run_id}|{escalation.id}".encode("utf-8")
    ).hexdigest()[:16]
    for event in writer.event_log.read_all():
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == "run.manager.autoresearch.requested"
            and str(payload.get("request_id") or "") == request_id
        ):
            return
    writer.emit(
        "run.manager.autoresearch.requested",
        actor="run-manager",
        task_id=str(terminal.task_id or escalation.task_id or "") or None,
        causation_id=terminal.id,
        correlation_id=request_id,
        payload={
            "schema_version": "run-manager.autoresearch-request.v2",
            "request_id": request_id,
            "operation_key": f"terminal-diagnosis:{terminal.id}",
            "recovery_case_id": request_id,
            "fingerprint": str(
                terminal_payload.get("blocker_fingerprint") or request_id
            ),
            "failure_class": "stage_replan_cap_exhausted",
            "failure_scope": "plan_admission",
            "owner_route": "run_manager",
            "action_policy": "needs_diagnosis",
            "intervention_class": "diagnose",
            "workflow_run_id": run_id,
            "run_id": run_id,
            "task_id": str(terminal.task_id or escalation.task_id or ""),
            "source_event_ids": [
                escalation.id,
                terminal.id,
            ],
            "source_ref": f"events.jsonl#{terminal.id}",
            "summary": (
                "Plan producer exhausted bounded replan attempts; diagnose the "
                "producer/runtime contract mismatch without mutating mainline"
            ),
            "recommended_actions": [
                "inspect_plan_candidate_preflight",
                "propose_producer_or_contract_fix",
            ],
            "expected_output": [
                "diagnosis_report",
                "reproduction_steps",
                "patch_or_resume_proposal",
            ],
            "apply_policy": "proposal_only",
            "resume_policy": "return_proposal_to_run_manager",
        },
    )


def escalation_terminal_policy(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return an explicit bounded terminal policy, or an empty mapping."""

    if payload.get("recoverable") is True:
        return {}
    if payload.get("operator_required") is False:
        return {}
    reason = str(payload.get("reason") or "").lower()
    failure_class = str(payload.get("failure_class") or "").lower()
    terminal_condition = str(
        payload.get("terminalization_condition") or ""
    ).strip()
    explicit_terminal = (
        payload.get("terminalize") is True
        or payload.get("recoverable") is False
        or terminal_condition in {"immediate", "auto_recovery_exhausted"}
    )
    legacy_terminal = (
        failure_class in _UNRECOVERABLE_FAILURE_CLASSES
        or any(marker in reason for marker in _LEGACY_UNRECOVERABLE_MARKERS)
    )
    if not explicit_terminal and not legacy_terminal:
        return {}
    allowed = _strings(payload.get("allowed_actions")) or [
        "operator_review",
        "start_new_generation",
    ]
    return {
        "recovery_owner": str(payload.get("recovery_owner") or "operator"),
        "allowed_actions": allowed,
        "max_auto_attempts": _non_negative_int(
            payload.get("max_auto_attempts"),
            default=0,
        ),
        "max_rescans": _non_negative_int(
            payload.get("max_rescans"),
            default=0,
        ),
        "terminalization_condition": (
            terminal_condition or "auto_recovery_exhausted"
        ),
    }


def escalation_terminal_metadata(
    reason: str,
    *,
    source_event_id: str = "",
) -> dict[str, Any]:
    lowered = str(reason or "").strip().lower()
    if not any(marker in lowered for marker in _LEGACY_UNRECOVERABLE_MARKERS):
        return {}
    if "replan cap exhausted" in lowered:
        failure_class, failure_scope = (
            "stage_replan_cap_exhausted",
            "plan_admission",
        )
    elif "no upstream failure route" in lowered:
        failure_class, failure_scope = (
            "no_upstream_failure_route",
            "recovery_route",
        )
    else:
        failure_class, failure_scope = "operator_recovery_required", "run"
    metadata: dict[str, Any] = {
        "failure_class": failure_class,
        "failure_scope": failure_scope,
        "recovery_owner": "operator",
        "allowed_actions": ["operator_review", "start_new_generation"],
        "max_auto_attempts": 0,
        "max_rescans": 0,
        "terminalization_condition": "auto_recovery_exhausted",
        "operator_required": True,
        "recoverable": False,
    }
    if source_event_id:
        metadata["source_event_id"] = source_event_id
    return metadata


def _run_terminal_exists(events: Iterable[ZfEvent], *, run_id: str) -> bool:
    entry = build_run_admission_projection(events).runs.get(run_id)
    return bool(entry is not None and entry.terminal)


def _recovered_after(
    events: list[ZfEvent],
    *,
    index: int,
    run_id: str,
) -> bool:
    for event in events[index + 1:]:
        if event.type not in _RECOVERY_PROGRESS_EVENTS:
            continue
        if event.type == "run.goal.updated":
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("status") or "").strip() not in {
                "active",
                "running",
            }:
                continue
        if resolve_run_for_event(events, event) == run_id:
            return True
    return False


def _fingerprint(*, run_id: str, failure_class: str, reason: str) -> str:
    normalized = re.sub(r"\d+", "#", reason.strip().lower())
    raw = "|".join((run_id, failure_class, normalized))
    return "escalation-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _strings(value: Any) -> list[str]:
    source = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    return list(dict.fromkeys(
        str(item).strip() for item in source if str(item).strip()
    ))


__all__ = [
    "converge_unrecoverable_escalations",
    "escalation_terminal_metadata",
    "escalation_terminal_policy",
]
