"""Structured clarification contract for External Issue Triage."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import hydrate_call_result_envelope
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_text


SCHEMA_VERSION = "external-issue-clarification.v1"
MAX_QUESTIONS = 5
MAX_ROUNDS = 3
QUESTION_TYPES = frozenset({"short_text", "long_text", "single_choice", "boolean"})
DISPOSITION_OUTCOMES = frozenset({"ready_to_fix", "unverified_candidate", "needs_info"})
NEEDS_INFO_REASON_CODES = frozenset({
    "missing_observable_symptom",
    "missing_expected_behavior",
    "no_safe_scope_or_source_binding",
    "evidence_unreadable_or_digest_mismatch",
    "contradictory_required_facts",
})
FORBIDDEN_NEEDS_INFO_REASON_CODES = frozenset({
    "user_product_environment_unavailable",
    "user_web_unavailable",
    "user_runtime_unavailable",
    "no_dynamic_playwright",
    "no_live_user_logs",
    "root_cause_not_proven",
})
_QUESTION_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_GAP_CHARS = 500
_REPORT_SCHEMA_VERSION = "external-issue-report.v1"


def _report_ref_descriptor(
    state_dir: Path,
    text: str,
    *,
    source_event_id: str = "",
) -> dict[str, Any]:
    """Persist a complete report before exposing a bounded protocol excerpt.

    Reader reports can arrive either as a declared Markdown artifact or as a
    legacy inline ``outputs.report`` string.  The latter must be promoted to an
    immutable sidecar so truncating ``verification_gaps`` never discards the
    evidence needed by Fix/Verifier.
    """

    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return write_sidecar_text(
        state_dir,
        f"artifacts/external-issues/reports/{digest}.md",
        text,
        kind="external_issue_report",
        schema_version=_REPORT_SCHEMA_VERSION,
        created_by="external-issue-triage-outcome",
        source_event_id=source_event_id,
        content_type="text/markdown",
        required=True,
        preview="Complete External Issue triage report",
    )


def _bounded_gap_excerpt(reason: str, *, report_ref: Mapping[str, Any] | None = None) -> str:
    """Return a contract-sized gap while pointing at the complete report."""

    suffix = " 完整报告见 report_ref。" if report_ref else ""
    text = " ".join(str(reason or "").split())
    if suffix and len(text) + len(suffix) <= _MAX_GAP_CHARS:
        return text + suffix
    if len(text) <= _MAX_GAP_CHARS and not suffix:
        return text
    marker = " …完整报告见 report_ref。" if report_ref else " …"
    keep = max(0, _MAX_GAP_CHARS - len(marker))
    return text[:keep].rstrip() + marker


def normalize_external_issue_disposition(
    value: object,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate Agent semantics while keeping semantic decisions Agent-owned.

    ``unverified_candidate`` is intentionally distinct from ``needs_info``:
    the former means the Issue is actionable with a bounded verification gap,
    while the latter means an indispensable user fact is absent.
    """

    if value in (None, "", {}):
        return None, []
    if not isinstance(value, Mapping):
        return None, [_issue("external_issue_disposition", "schema_invalid")]
    outcome = str(value.get("outcome") or "").strip().lower()
    reason = str(value.get("reason") or "").strip()
    raw_questions = value.get("questions")
    questions = raw_questions if isinstance(raw_questions, list) else []
    issues: list[dict[str, str]] = []
    if outcome not in DISPOSITION_OUTCOMES:
        issues.append(_issue("external_issue_disposition.outcome", "invalid_value"))
    if not reason or len(reason) > 1000:
        issues.append(_issue("external_issue_disposition.reason", "missing_or_too_long"))
    if not isinstance(raw_questions, list):
        issues.append(_issue("external_issue_disposition.questions", "schema_invalid"))
    reason_code = str(value.get("reason_code") or "").strip().lower()
    if (
        outcome == "needs_info"
        and reason_code not in FORBIDDEN_NEEDS_INFO_REASON_CODES
        and not 1 <= len(questions) <= MAX_QUESTIONS
    ):
        issues.append(_issue(
            "external_issue_disposition.questions", "question_count_invalid",
        ))
    if outcome in {"ready_to_fix", "unverified_candidate"} and questions:
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
    verification_gaps = value.get("verification_gaps")
    if verification_gaps is None:
        verification_gaps = []
    if not isinstance(verification_gaps, list) or any(
        not str(item).strip() or len(str(item)) > 500 for item in verification_gaps
    ) or len(verification_gaps) > 20:
        issues.append(_issue("external_issue_disposition.verification_gaps", "schema_invalid"))
    report_ref = value.get("report_ref")
    if report_ref is not None and (
        not isinstance(report_ref, Mapping)
        or not str(report_ref.get("ref") or "").strip()
        or not str(report_ref.get("sha256") or "").strip()
    ):
        issues.append(_issue("external_issue_disposition.report_ref", "schema_invalid"))
    confidence = str(value.get("confidence") or "").strip().lower()
    if confidence and confidence not in {"low", "medium", "high"}:
        issues.append(_issue("external_issue_disposition.confidence", "invalid_value"))
    verification_status = str(value.get("verification_status") or "").strip().lower()
    if verification_status and verification_status not in {"complete", "partial", "not_run"}:
        issues.append(_issue("external_issue_disposition.verification_status", "invalid_value"))
    if reason_code and reason_code not in (
        NEEDS_INFO_REASON_CODES | FORBIDDEN_NEEDS_INFO_REASON_CODES
    ):
        issues.append(_issue("external_issue_disposition.reason_code", "invalid_value"))
    if issues:
        return None, issues
    return {
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "reason": reason,
        "questions": normalized_questions,
        **({"reason_code": reason_code} if reason_code else {}),
        **({"confidence": confidence} if confidence else {}),
        **({"verification_status": verification_status} if verification_status else {}),
        **({"verification_gaps": [str(item).strip() for item in verification_gaps]} if verification_gaps else {}),
        **({"report_ref": dict(report_ref)} if isinstance(report_ref, Mapping) else {}),
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
            "external_issue.triage.ready_to_fix",
            "external_issue.triage.unverified_candidate",
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
    if disposition is None or disposition.get("outcome") not in {
        "ready_to_fix", "needs_info", "unverified_candidate",
    }:
        return None
    if (
        disposition.get("outcome") == "needs_info"
        and disposition.get("reason_code") in FORBIDDEN_NEEDS_INFO_REASON_CODES
    ):
        disposition = {
            **disposition,
            "outcome": "unverified_candidate",
            "questions": [],
            "verification_status": "partial",
            "verification_gaps": [str(disposition.get("reason") or "")],
        }
    invoke_payload = invoke.payload if isinstance(invoke.payload, Mapping) else {}
    source_key = str(invoke_payload.get("source_key") or "")
    source_revision = str(invoke_payload.get("source_revision") or "")
    if disposition.get("outcome") == "ready_to_fix":
        return ZfEvent(
            type="external_issue.triage.ready_to_fix",
            actor="kernel",
            task_id=cause.task_id or invoke.task_id,
            payload={
                "source_key": source_key,
                "source_revision": source_revision,
                "workflow_run_id": run_id,
                "verification_status": str(disposition.get("verification_status") or ""),
                "verification_gaps": list(disposition.get("verification_gaps") or []),
                **({"report_ref": dict(disposition["report_ref"])}
                   if isinstance(disposition.get("report_ref"), Mapping) else {}),
            },
            causation_id=cause.id,
            correlation_id=run_id,
        )
    if disposition.get("outcome") == "unverified_candidate":
        return ZfEvent(
            type="external_issue.triage.unverified_candidate",
            actor="kernel",
            task_id=cause.task_id or invoke.task_id,
            payload={
                "source_key": source_key,
                "source_revision": source_revision,
                "workflow_run_id": run_id,
                "reason": str(disposition.get("reason") or ""),
                "confidence": str(disposition.get("confidence") or ""),
                "verification_status": str(disposition.get("verification_status") or ""),
                "verification_gaps": list(disposition.get("verification_gaps") or []),
                **({"report_ref": dict(disposition["report_ref"])}
                   if isinstance(disposition.get("report_ref"), Mapping) else {}),
            },
            causation_id=cause.id,
            correlation_id=run_id,
        )
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
        normalized = _normalize_producer_disposition(state_dir, candidate)
        if normalized is not None:
            return normalized
        # Older workflow readers materialized the semantic report as Markdown
        # under ``outputs.report`` instead of preserving the structured
        # disposition field.  Do not lose a verified needs_info outcome at
        # the control boundary: recover only the explicit conclusion markers
        # emitted by the External Issue report contract.
        outputs = control.get("outputs")
        report_text = outputs.get("report") if isinstance(outputs, Mapping) else None
        if isinstance(report_text, str):
            report_ref = _report_ref_descriptor(state_dir, report_text)
            recovered = _disposition_from_report_markdown(
                report_text, report_ref=report_ref,
            )
            if recovered is not None:
                return recovered
        # Some workflow readers only retain the report in the immutable
        # ``output_artifacts`` list.  Hydrate the declared Markdown artifact
        # instead of dropping the verified semantic conclusion at the
        # control-result boundary.
        output_artifacts = control.get("output_artifacts")
        if isinstance(output_artifacts, list):
            for artifact in output_artifacts:
                if not isinstance(artifact, Mapping):
                    continue
                if str(artifact.get("kind") or "") != "report/markdown":
                    continue
                try:
                    payload = hydrate_sidecar_ref(state_dir, dict(artifact)).payload
                except (OSError, ValueError, TypeError):
                    continue
                if isinstance(payload, str):
                    recovered = _disposition_from_report_markdown(
                        payload, report_ref=artifact,
                    )
                    if recovered is not None:
                        return recovered
    return None


def _normalize_producer_disposition(
    state_dir: Path,
    candidate: object,
) -> dict[str, Any] | None:
    """Normalize a structured disposition without discarding oversized gaps.

    The raw control sidecar is already durable, but an explicit report ref is
    still attached to the normalized result so downstream Fix/Verifier code has
    a stable, auditable place to read the complete unbounded evidence from.
    """

    normalized, issues = normalize_external_issue_disposition(candidate)
    if normalized is not None and not issues:
        return normalized
    if not isinstance(candidate, Mapping):
        return None
    only_gap_overflow = bool(issues) and all(
        item.get("field") == "control_result.external_issue_disposition.verification_gaps"
        for item in issues
    )
    raw_gaps = candidate.get("verification_gaps")
    if not only_gap_overflow or not isinstance(raw_gaps, list):
        return None
    report_ref = write_immutable_json_sidecar(
        state_dir,
        dict(candidate),
        root="external-issues/reports/structured-dispositions",
        kind="external_issue_disposition_report",
        schema_version=SCHEMA_VERSION,
        created_by="external-issue-triage-outcome",
        required=True,
    )
    bounded = {
        **dict(candidate),
        "verification_gaps": [
            _bounded_gap_excerpt(str(item), report_ref=report_ref)
            for item in raw_gaps[:20]
            if str(item).strip()
        ],
        "report_ref": report_ref,
    }
    normalized, issues = normalize_external_issue_disposition(bounded)
    return normalized if normalized is not None and not issues else None


def _disposition_from_report_markdown(
    text: str,
    *,
    report_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Recover an explicit External Issue disposition from legacy Markdown."""

    verdict_match = re.search(r"`verdict`\s*:\s*`([^`]+)`", text, re.IGNORECASE)
    recommendation_match = re.search(
        r"`recommendation`\s*:\s*`([^`]+)`", text, re.IGNORECASE,
    )
    status_match = re.search(
        r"`(?:external_issue_triage_status|status)`\s*:\s*`([^`]+)`",
        text,
        re.IGNORECASE,
    )
    disposition_match = re.search(
        r"`?external_issue_disposition`?\s*:\s*`?([a-z_]+)`?",
        text,
        re.IGNORECASE,
    )
    prose_status_match = re.search(
        r"Triage\s+remains\s+\*\*([^*]+)\*\*",
        text,
        re.IGNORECASE,
    )
    values = {
        str(match.group(1)).strip().lower()
        for match in (
            verdict_match, recommendation_match, status_match, prose_status_match,
            disposition_match,
        )
        if match is not None
    }
    if not values:
        return None
    if "ready_to_fix" in values or "ready-to-fix" in values:
        outcome = "ready_to_fix"
    elif "unverified_candidate" in values or "unverified-candidate" in values:
        outcome = "unverified_candidate"
    elif {"needs_info", "needs-info", "needs_rework", "needs-rework"} & values:
        outcome = "needs_info"
    else:
        return None
    conclusion = re.search(
        r"##\s*结论\s*\n+(.+?)(?=\n##\s|\Z)", text, re.IGNORECASE | re.DOTALL,
    )
    reason = " ".join((conclusion.group(1) if conclusion else text).split())[:1000]
    # Missing a user's indispensable fact is a real clarification request.
    # Missing a live runtime, Playwright timing, or a source binding that the
    # canonical ZaoFu checkout can supply is only a verification gap and must
    # remain actionable as an unverified candidate.
    non_user_gap = re.search(
        r"运行时已停止|未能执行动态|没有可绑定.*source.?revision|"
        r"source.?revision.*缺失|target_ref.*不包含|动态.*未运行|"
        r"runtime\s+is\s+stopped|no\s+live\s+(?:browser\s+)?capture|"
        r"live\s+browser\s+capture.*skipped|endpoint[- ]level\s+timings?.*unavailable|"
        r"current\s+deployment.*unavailable",
        text,
        re.IGNORECASE,
    )
    if outcome == "needs_info" and non_user_gap:
        outcome = "unverified_candidate"
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "outcome": outcome,
        "reason": reason or "workflow report declared an External Issue disposition",
        "questions": [],
    }
    if outcome == "needs_info":
        payload["questions"] = [{
            "id": "missing_required_context",
            "type": "long_text",
            "question": "请补充仍缺失且无法由现有 Issue、artifact 和源码推导的信息。",
            "required": True,
        }]
    elif outcome == "unverified_candidate":
        payload.update({
            "verification_status": "partial",
            # Keep only a contract-sized projection here.  The complete
            # report remains available through the immutable report_ref.
            "verification_gaps": [
                _bounded_gap_excerpt(reason, report_ref=report_ref),
            ],
        })
    if report_ref is not None:
        payload["report_ref"] = dict(report_ref)
    normalized, issues = normalize_external_issue_disposition(payload)
    return normalized if normalized is not None and not issues else None


def _issue(field: str, code: str) -> dict[str, str]:
    return {"field": f"control_result.{field}", "code": code}


__all__ = [
    "MAX_ROUNDS",
    "SCHEMA_VERSION",
    "DISPOSITION_OUTCOMES",
    "external_issue_triage_outcome_event",
    "hydrate_external_issue_clarification",
    "normalize_external_issue_disposition",
]
