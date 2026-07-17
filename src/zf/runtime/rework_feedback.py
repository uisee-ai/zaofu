"""Lossless, digest-verified feedback artifacts for bounded rework."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.verification_result import failed_acceptance_ids


SCHEMA_VERSION = "rework-feedback.v1"
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


class ReworkFeedbackError(ValueError):
    """Feedback is missing, stale, or digest-invalid."""


def write_rework_feedback(
    state_dir: Path,
    *,
    task_id: str,
    failure_fingerprint: str,
    source_event: ZfEvent,
    source_attempt: int,
    verification_result: Mapping[str, Any] | None = None,
    allowed_paths: list[str] | None = None,
    required_actions: list[str] | None = None,
    summary: str = "",
) -> dict[str, Any]:
    result = dict(verification_result or {})
    event_payload = source_event.payload if isinstance(source_event.payload, dict) else {}
    findings = result.get("findings")
    if not isinstance(findings, list):
        findings = event_payload.get("findings")
    if not isinstance(findings, list):
        report = event_payload.get("report") if isinstance(event_payload.get("report"), dict) else {}
        findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    findings = list(findings)
    commands = result.get("reproduction_commands")
    if not isinstance(commands, list):
        commands = event_payload.get("reproduction_commands")
    if not isinstance(commands, list):
        commands = event_payload.get("verification_commands")
    commands = [str(item).strip() for item in commands or [] if str(item).strip()]
    evidence_refs = result.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        evidence_refs = event_payload.get("evidence_refs")
    evidence_refs = [str(item).strip() for item in evidence_refs or [] if str(item).strip()]
    requirement_results = [
        dict(item) if isinstance(item, Mapping) else item
        for item in result.get("requirement_results") or []
    ]
    finding_acceptance_ids: dict[str, str] = {}
    for item in requirement_results:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "") not in {"failed", "blocked"}:
            continue
        acceptance_id = str(item.get("acceptance_id") or "").strip()
        nested_findings = list(item.get("findings") or [])
        for finding in nested_findings:
            finding_acceptance_ids[_finding_marker(finding)] = acceptance_id
        findings.extend(nested_findings)
        commands.extend(str(value).strip() for value in item.get("reproduction_commands") or [])
        evidence_refs.extend(str(value).strip() for value in item.get("evidence_refs") or [])
    findings = _dedupe_values(findings)
    normalized_findings = [
        _normalize_finding(
            finding,
            task_id=task_id,
            failure_fingerprint=failure_fingerprint,
            acceptance_id=finding_acceptance_ids.get(_finding_marker(finding), ""),
        )
        for finding in findings
    ]
    by_marker = {
        _finding_marker(finding): finding
        for finding in normalized_findings
    }
    for item in requirement_results:
        if not isinstance(item, dict):
            continue
        item["findings"] = [
            by_marker.get(
                _finding_marker(finding),
                _normalize_finding(
                    finding,
                    task_id=task_id,
                    failure_fingerprint=failure_fingerprint,
                    acceptance_id=str(item.get("acceptance_id") or ""),
                ),
            )
            for finding in item.get("findings") or []
        ]
    commands = _dedupe_strings(commands)
    evidence_refs = _dedupe_strings(evidence_refs)
    feedback_id = "feedback-" + hashlib.sha256("\0".join((
        str(task_id or "").strip(),
        str(failure_fingerprint or "").strip(),
        str(source_event.id or ""),
    )).encode("utf-8")).hexdigest()[:20]
    body = {
        "schema_version": SCHEMA_VERSION,
        "feedback_id": feedback_id,
        "task_id": str(task_id or "").strip(),
        "failure_fingerprint": str(failure_fingerprint or "").strip(),
        "source_event_id": str(source_event.id or ""),
        "source_event_type": str(source_event.type or ""),
        "source_attempt": int(source_attempt or 0),
        "summary": str(
            summary
            or result.get("summary")
            or event_payload.get("reason")
            or event_payload.get("summary")
            or source_event.type
        ).strip(),
        "failed_acceptance_ids": failed_acceptance_ids(result),
        "requirement_results": requirement_results,
        "findings": normalized_findings,
        "reproduction_commands": commands,
        "evidence_refs": evidence_refs,
        "allowed_paths": [str(item) for item in allowed_paths or [] if str(item).strip()],
        "required_actions": [str(item) for item in required_actions or [] if str(item).strip()],
    }
    _validate_feedback(body)
    stable = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    suffix = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]
    ref = (
        "artifacts/rework-feedback/"
        f"{_segment(task_id)}/{_segment(source_event.id)}-{suffix}.json"
    )
    descriptor = write_sidecar_json(
        state_dir,
        ref,
        body,
        kind="rework_feedback",
        schema_version=SCHEMA_VERSION,
        created_by="orchestrator",
        source_event_id=source_event.id,
        required=True,
        preview=body["summary"][:240],
    )
    return {
        **descriptor,
        "feedback_id": feedback_id,
        "finding_ids": [
            str(item.get("finding_id") or "")
            for item in normalized_findings
            if str(item.get("finding_id") or "")
        ],
    }


def hydrate_rework_feedback(
    state_dir: Path,
    descriptor: Mapping[str, Any],
    *,
    expected_task_id: str = "",
    expected_fingerprint: str = "",
) -> dict[str, Any]:
    try:
        hydrated = hydrate_sidecar_ref(
            state_dir,
            dict(descriptor),
            purpose="rework_dispatch",
            actor="orchestrator",
        )
    except Exception as exc:
        raise ReworkFeedbackError(str(exc)) from exc
    if not isinstance(hydrated.payload, dict):
        raise ReworkFeedbackError("rework feedback is not an object")
    body = dict(hydrated.payload)
    _validate_feedback(body)
    if expected_task_id and str(body.get("task_id") or "") != expected_task_id:
        raise ReworkFeedbackError("rework feedback task_id mismatch")
    if expected_fingerprint and str(body.get("failure_fingerprint") or "") != expected_fingerprint:
        raise ReworkFeedbackError("rework feedback fingerprint mismatch")
    return body


def descriptor_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    ref = str(
        payload.get("rework_feedback_ref")
        or payload.get("feedback_artifact_ref")
        or ""
    ).strip()
    digest = str(
        payload.get("rework_feedback_digest")
        or payload.get("feedback_artifact_digest")
        or ""
    ).strip()
    if not ref or not digest:
        raise ReworkFeedbackError("rework feedback ref/digest missing")
    return {
        "ref": ref,
        "sha256": digest,
        "kind": "rework_feedback",
        "schema_version": SCHEMA_VERSION,
        "content_type": "application/json",
        "required": True,
    }


def feedback_payload_fields(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "rework_feedback_ref": str(descriptor.get("ref") or ""),
        "rework_feedback_digest": str(descriptor.get("sha256") or ""),
    }
    feedback_id = str(descriptor.get("feedback_id") or "").strip()
    if feedback_id:
        fields["feedback_id"] = feedback_id
    finding_ids = [
        str(item).strip()
        for item in descriptor.get("finding_ids") or []
        if str(item).strip()
    ]
    if finding_ids:
        fields["finding_ids"] = finding_ids
    return fields


def feedback_briefing_lines(body: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    summary = str(body.get("summary") or "").strip()
    if summary:
        lines.append(summary)
    for item in body.get("requirement_results") or []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "") not in {"failed", "blocked"}:
            continue
        acceptance_id = str(item.get("acceptance_id") or "unknown-ac")
        for finding in item.get("findings") or []:
            lines.append(f"{acceptance_id}: {_finding_text(finding)}")
        for command in item.get("reproduction_commands") or []:
            lines.append(f"{acceptance_id} reproduce: {command}")
    for finding in body.get("findings") or []:
        lines.append(_finding_text(finding))
    for action in body.get("required_actions") or []:
        lines.append(f"required: {action}")
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = str(line or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _validate_feedback(body: Mapping[str, Any]) -> None:
    if str(body.get("schema_version") or "") != SCHEMA_VERSION:
        raise ReworkFeedbackError("unsupported rework feedback schema")
    required = (
        "task_id",
        "failure_fingerprint",
        "source_event_id",
        "source_event_type",
        "summary",
    )
    missing = [key for key in required if not str(body.get(key) or "").strip()]
    if missing:
        raise ReworkFeedbackError("rework feedback missing: " + ", ".join(missing))


def _finding_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(
            value.get("message")
            or value.get("summary")
            or value.get("reason")
            or value.get("title")
            or json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        ).strip()
    return str(value or "").strip()


def _finding_marker(value: Any) -> str:
    if isinstance(value, Mapping):
        semantic = {
            str(key): item
            for key, item in value.items()
            if str(key) not in {"finding_id", "event_id", "ts", "timestamp"}
        }
        return json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    return str(value or "").strip()


def _normalize_finding(
    value: Any,
    *,
    task_id: str,
    failure_fingerprint: str,
    acceptance_id: str = "",
) -> dict[str, Any]:
    record = dict(value) if isinstance(value, Mapping) else {"message": str(value or "")}
    if acceptance_id:
        record.setdefault("acceptance_id", acceptance_id)
    finding_id = str(record.get("finding_id") or "").strip()
    if not finding_id:
        stable = "\0".join((
            str(task_id or "").strip(),
            str(failure_fingerprint or "").strip(),
            str(record.get("acceptance_id") or "").strip(),
            _finding_marker(record),
        ))
        finding_id = "finding-" + hashlib.sha256(
            stable.encode("utf-8"),
        ).hexdigest()[:20]
    record["finding_id"] = finding_id
    return record


def _dedupe_strings(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _dedupe_values(values: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            out.append(value)
    return out


def _segment(value: Any) -> str:
    return _SAFE_SEGMENT.sub("-", str(value or "").strip()).strip("-._") or "unknown"


__all__ = [
    "ReworkFeedbackError",
    "SCHEMA_VERSION",
    "descriptor_from_payload",
    "feedback_briefing_lines",
    "feedback_payload_fields",
    "hydrate_rework_feedback",
    "write_rework_feedback",
]
