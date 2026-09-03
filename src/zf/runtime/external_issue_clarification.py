"""Structured clarification contract for External Issue Triage."""

from __future__ import annotations

import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import hydrate_call_result_envelope
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


SCHEMA_VERSION = "external-issue-clarification.v1"
MAX_QUESTIONS = 5
MAX_ROUNDS = 3
QUESTION_TYPES = frozenset({"short_text", "long_text", "single_choice", "boolean"})
_QUESTION_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def normalize_external_issue_disposition(
    value: object,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate Agent semantics while leaving question content Agent-owned."""

    if value in (None, "", {}):
        return None, []
    if not isinstance(value, Mapping):
        return None, [_issue("external_issue_disposition", "schema_invalid")]
    outcome = str(value.get("outcome") or "").strip().lower()
    reason = str(value.get("reason") or "").strip()
    raw_questions = value.get("questions")
    questions = raw_questions if isinstance(raw_questions, list) else []
    issues: list[dict[str, str]] = []
    if outcome not in {"ready_to_fix", "needs_info"}:
        issues.append(_issue("external_issue_disposition.outcome", "invalid_value"))
    if not reason or len(reason) > 1000:
        issues.append(_issue("external_issue_disposition.reason", "missing_or_too_long"))
    if not isinstance(raw_questions, list):
        issues.append(_issue("external_issue_disposition.questions", "schema_invalid"))
    if outcome == "needs_info" and not 1 <= len(questions) <= MAX_QUESTIONS:
        issues.append(_issue(
            "external_issue_disposition.questions", "question_count_invalid",
        ))
    if outcome == "ready_to_fix" and questions:
        issues.append(_issue(
            "external_issue_disposition.questions", "questions_not_allowed",
        ))

    normalized_questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(questions[:MAX_QUESTIONS]):
        field = f"external_issue_disposition.questions[{index}]"
        if not isinstance(raw, Mapping):
            issues.append(_issue(field, "schema_invalid"))
            continue
        question_id = str(raw.get("id") or "").strip()
        question_type = str(raw.get("type") or "long_text").strip().lower()
        question = str(raw.get("question") or "").strip()
        options = [
            str(item).strip() for item in raw.get("options") or []
            if str(item).strip()
        ] if isinstance(raw.get("options"), list) else []
        if not _QUESTION_ID.fullmatch(question_id) or question_id in seen:
            issues.append(_issue(f"{field}.id", "invalid_or_duplicate"))
        seen.add(question_id)
        if question_type not in QUESTION_TYPES:
            issues.append(_issue(f"{field}.type", "invalid_value"))
        if not question or len(question) > 500:
            issues.append(_issue(f"{field}.question", "missing_or_too_long"))
        if question_type == "single_choice" and not 2 <= len(options) <= 10:
            issues.append(_issue(f"{field}.options", "option_count_invalid"))
        if question_type != "single_choice" and options:
            issues.append(_issue(f"{field}.options", "options_not_allowed"))
        normalized_questions.append({
            "id": question_id,
            "type": question_type,
            "question": question,
            "required": bool(raw.get("required", True)),
            **({"options": options} if options else {}),
        })
    if issues:
        return None, issues
    return {
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "reason": reason,
        "questions": normalized_questions,
    }, []


def external_issue_triage_outcome_event(
    state_dir: Path,
    events: Iterable[ZfEvent],
    *,
    cause: ZfEvent,
) -> ZfEvent | None:
    """Project an independently verified Agent disposition into Issue truth."""

    if cause.type != "artifact.delivery.verified":
        return None
    rows = list(events)
    payload = cause.payload if isinstance(cause.payload, Mapping) else {}
    run_id = str(payload.get("workflow_run_id") or cause.correlation_id or "")
    invoke = next((
        event for event in reversed(rows)
        if event.type == "workflow.invoke.requested"
        and str((event.payload or {}).get("workflow_run_id") or event.correlation_id or "") == run_id
        and str((event.payload or {}).get("source") or "") == "external_issue"
    ), None)
    if invoke is None:
        return None
    if any(
        event.type in {
            "external_issue.triage.needs_info",
            "external_issue.triage.needs_info_exhausted",
        }
        and str((event.payload or {}).get("workflow_run_id") or "") == run_id
        for event in rows
    ):
        return None
    artifact_result = payload.get("artifact_delivery_result")
    artifact_result = artifact_result if isinstance(artifact_result, Mapping) else {}
    disposition = _producer_disposition(
        Path(state_dir), artifact_result.get("input_result_refs") or [],
    )
    if disposition is None or disposition.get("outcome") != "needs_info":
        return None
    invoke_payload = invoke.payload if isinstance(invoke.payload, Mapping) else {}
    source_key = str(invoke_payload.get("source_key") or "")
    source_revision = str(invoke_payload.get("source_revision") or "")
    round_number = 1 + sum(
        event.type == "external_issue.triage.needs_info"
        and str((event.payload or {}).get("source_key") or "") == source_key
        for event in rows
    )
    exhausted = round_number >= MAX_ROUNDS
    body = {
        **disposition,
        "source_key": source_key,
        "source_revision": source_revision,
        "workflow_run_id": run_id,
        "task_id": str(cause.task_id or invoke.task_id or ""),
        "round": round_number,
        "max_rounds": MAX_ROUNDS,
        "status": "needs_info_exhausted" if exhausted else "needs_info",
    }
    descriptor = write_immutable_json_sidecar(
        Path(state_dir),
        body,
        root="external-issues/clarifications",
        kind="external_issue_clarification",
        schema_version=SCHEMA_VERSION,
        created_by="external-issue-triage-outcome",
        source_event_id=cause.id,
    )
    return ZfEvent(
        type=(
            "external_issue.triage.needs_info_exhausted"
            if exhausted else "external_issue.triage.needs_info"
        ),
        actor="kernel",
        task_id=cause.task_id or invoke.task_id,
        payload={
            "source_key": source_key,
            "source_revision": source_revision,
            "workflow_run_id": run_id,
            "round": round_number,
            "max_rounds": MAX_ROUNDS,
            "reason": str(disposition.get("reason") or ""),
            "clarification_ref": descriptor,
        },
        causation_id=cause.id,
        correlation_id=run_id,
    )


def hydrate_external_issue_clarification(
    state_dir: Path, descriptor: object,
) -> dict[str, Any] | None:
    if not isinstance(descriptor, Mapping):
        return None
    try:
        payload = hydrate_sidecar_ref(Path(state_dir), dict(descriptor)).payload
    except (OSError, ValueError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _producer_disposition(
    state_dir: Path, input_result_refs: object,
) -> dict[str, Any] | None:
    if not isinstance(input_result_refs, list):
        return None
    for ref in input_result_refs:
        try:
            descriptor = (
                dict(ref) if isinstance(ref, Mapping) else {
                    "ref": str(ref),
                    "sha256": PurePosixPath(str(ref)).stem,
                }
            )
            envelope = hydrate_call_result_envelope(state_dir, descriptor)
            descriptor = envelope.get("control_result")
            if not isinstance(descriptor, Mapping):
                continue
            control = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(control, Mapping):
            continue
        candidate = control.get("external_issue_disposition")
        # Provider adapters may retain the worker report under ``report``
        # while the canonical control sidecar keeps top-level fields.  Accept
        # both layouts; validation still happens through the same contract.
        if candidate is None and isinstance(control.get("report"), Mapping):
            candidate = control["report"].get("external_issue_disposition")
        normalized, issues = normalize_external_issue_disposition(candidate)
        if normalized is not None and not issues:
            return normalized
    return None


def _issue(field: str, code: str) -> dict[str, str]:
    return {"field": f"control_result.{field}", "code": code}


__all__ = [
    "MAX_ROUNDS",
    "SCHEMA_VERSION",
    "external_issue_triage_outcome_event",
    "hydrate_external_issue_clarification",
    "normalize_external_issue_disposition",
]
