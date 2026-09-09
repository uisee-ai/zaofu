"""Kernel-owned pre-Draft intake and local attachment lifecycle."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
import uuid
import zlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zf.core.security.redaction import redact_obj, redact_text
from zf.core.self_issue.intake import (
    QUESTION_SCHEMA_VERSION,
    default_intake_answers,
    first_missing_required,
    intake_questions,
    normalize_intake_answers,
)
from zf.core.self_issue.models import IssueDraft, SelfIssueIntake, stable_digest, utc_now
from zf.core.state.locks import locked_path
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json


MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 50 * 1024 * 1024
ALLOWED_ATTACHMENTS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".json": "application/json",
}
_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f\x7f/\\]+")
_VOLATILE_SIGNAL_TOKEN = re.compile(
    r"(?i)(?:\b[0-9a-f]{8,}\b|\b\d+(?:\.\d+)?\b|/tmp/[^\s]+)",
)
_ACTIVE_AUTO_INTAKE_LIMIT = 10


class SelfIssueIntakeMixin:
    """Transitions before a canonical Issue Draft exists."""

    def start_intake(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "intake.lock"):
            target = self._target_binding(payload.get("target_binding") or {})
            seed = str(payload.get("title") or payload.get("description") or "").strip()[:240]
            intake = SelfIssueIntake(
                intake_id=f"sii-{uuid.uuid4().hex[:12]}",
                subject_scope=str(payload.get("subject_scope") or "zaofu"),
                target_binding=target,
                reporter_context=_reporter_context(payload),
                reporter_evidence_refs=self._validated_reporter_evidence_refs(
                    payload.get("evidence_refs") or [],
                ),
            )
            intake.answers_ref = self._write_intake_answers(
                intake, default_intake_answers(title_seed=seed),
            )
            self.intakes.save(intake)
            event = self.writer.emit(
                "self_issue.intake.started",
                actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "intake_id": intake.intake_id,
                    "question_schema_version": QUESTION_SCHEMA_VERSION,
                    "reporter_context": redact_obj(intake.reporter_context),
                    "reporter_evidence_count": len(intake.reporter_evidence_refs),
                },
            )
            return {
                "ok": True,
                "status": "intake_collecting",
                "intake": self._intake_view(intake),
                "event_id": event.id,
            }

    def system_detect(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        """Create/update a local-only Intake from one verified strong signal."""
        from zf.runtime.self_issue_auto_trigger import safe_signal_snapshot

        with locked_path(self.state_dir / "self-issues" / "intake.lock"):
            event_id = str(payload.get("event_id") or causation_id or "").strip()
            event_type = str(payload.get("event_type") or "").strip()
            reporter_kind = str(payload.get("reporter_kind") or "").strip()
            if not event_id or reporter_kind not in {"kernel", "worker"}:
                raise ValueError("system detection requires a verified event and reporter kind")
            if reporter_kind == "worker" and event_type != "worker.self_issue.detected":
                raise ValueError("worker system detection requires worker.self_issue.detected")
            title = str(payload.get("title") or "ZaoFu detected an internal incident").strip()[:240]
            summary = str(payload.get("summary") or "").strip()[:1000]
            classification = str(payload.get("classification") or "unknown")
            severity = str(payload.get("severity") or "P2")
            subject_scope = "zaofu"
            fingerprint = stable_digest({
                "authorization_domain": str(
                    self.policy.authorization_domain or "gitlab.com"
                ).lower(),
                "subject_scope": subject_scope,
                "component": classification,
                "failure_category": event_type,
                "error_signature": _VOLATILE_SIGNAL_TOKEN.sub(
                    "<variable>", summary.lower(),
                )[:400],
                "baseline_version": self._git_head(),
            })
            if self._auto_intake_was_dismissed(fingerprint):
                event = self.writer.emit(
                    "self_issue.intake.auto_suppressed", actor="kernel",
                    causation_id=causation_id or None,
                    payload={
                        "incident_fingerprint": fingerprint,
                        "source_event_id": event_id,
                        "reason": "dismissed_fingerprint_cooldown",
                    },
                )
                return {
                    "ok": True, "status": "auto_intake_suppressed",
                    "reason": "matching automatic Intake was dismissed within 24 hours",
                    "incident_fingerprint": fingerprint,
                    "event_id": event.id,
                }
            existing = self.intakes.find_fingerprint(fingerprint)
            reporter_refs = self._validated_reporter_evidence_refs(
                payload.get("evidence_refs") or [],
            )
            if reporter_kind == "worker" and (
                not reporter_refs
                or any(
                    not str(item.get("source_event_id") or "")
                    or str(item.get("created_by") or "")
                    != str(payload.get("actor") or "")
                    for item in reporter_refs
                )
            ):
                raise ValueError(
                    "worker detection requires actor-owned evidence refs with source_event_id"
                )
            target = existing.target_binding if existing else self._target_binding({})
            intake_id = existing.intake_id if existing else f"sii-{uuid.uuid4().hex[:12]}"
            signal_ref = write_sidecar_json(
                self.state_dir,
                (
                    f"artifacts/self-issue-intakes/{intake_id}/signals/"
                    f"{stable_digest(event_id)[:16]}.json"
                ),
                {
                    "schema_version": "self-issue-detected-signal.v1",
                    "signal": safe_signal_snapshot(payload),
                },
                kind="self_issue_detected_signal",
                schema_version="self-issue-detected-signal.v1",
                created_by="kernel",
                source_event_id=event_id,
                access_scope={"external_disclosure": False},
                required=True,
                preview="Local-only detected incident signal",
            )
            refs = [*reporter_refs, signal_ref]
            now = datetime.now(timezone.utc)
            if existing is not None:
                previous_severity = str(existing.detection.get("severity") or "P3")
                escalated = _severity_rank(severity) < _severity_rank(previous_severity)
                due = escalated or (
                    now - _parse_timestamp(existing.last_notified_at or existing.created_at)
                    >= timedelta(hours=6)
                )
                updated = replace(
                    existing,
                    revision=existing.revision + 1,
                    reporter_evidence_refs=_merge_refs(
                        existing.reporter_evidence_refs, refs, limit=20,
                    ),
                    detection={
                        **existing.detection,
                        "severity": severity if escalated else previous_severity,
                        "classification": classification,
                        "latest_event_id": event_id,
                        "latest_event_type": event_type,
                    },
                    occurrence_count=existing.occurrence_count + 1,
                    notification_due=due,
                    last_notified_at=utc_now() if due else existing.last_notified_at,
                    updated_at=utc_now(),
                )
                self.intakes.save(updated)
                event = self.writer.emit(
                    "self_issue.intake.auto_updated", actor="kernel",
                    causation_id=causation_id or None,
                    payload={
                        "intake_id": updated.intake_id,
                        "incident_fingerprint": fingerprint,
                        "occurrence_count": updated.occurrence_count,
                        "notification_due": due,
                        "severity_escalated": escalated,
                        "source_event_id": event_id,
                    },
                )
                return {
                    "ok": True, "status": "auto_intake_updated",
                    "intake": self._intake_view(updated), "event_id": event.id,
                }
            active_auto = [
                item for item in self.intakes.list()
                if item.origin == "system_detected"
                and item.status in {"collecting", "awaiting_user_review", "submitted"}
            ]
            if len(active_auto) >= _ACTIVE_AUTO_INTAKE_LIMIT:
                event = self.writer.emit(
                    "self_issue.intake.auto_suppressed", actor="kernel",
                    causation_id=causation_id or None,
                    payload={
                        "incident_fingerprint": fingerprint,
                        "source_event_id": event_id,
                        "reason": "active_candidate_limit",
                    },
                )
                return {
                    "ok": True, "status": "auto_intake_suppressed",
                    "reason": "active automatic Intake limit reached",
                    "incident_fingerprint": fingerprint,
                    "event_id": event.id,
                }
            reporter_context = _reporter_context({
                "actor": str(payload.get("actor") or reporter_kind),
                "task_id": str(payload.get("task_id") or ""),
                "reporter_context": {
                    "discovered_by": reporter_kind,
                    "reported_by": reporter_kind if reporter_kind == "worker" else "kernel",
                    "collected_by": reporter_kind if reporter_kind == "worker" else "kernel",
                    "role": reporter_kind,
                    "reporter_fallback": (
                        "orchestrator" if reporter_kind not in {"worker", "kernel"} else ""
                    ),
                    "source_event_id": event_id,
                    "browser_capture": {
                        "requested": bool(payload.get("browser_capture_requested")),
                        "target": "kanban_board",
                    },
                },
            })
            intake = SelfIssueIntake(
                intake_id=intake_id,
                subject_scope=subject_scope,
                target_binding=target,
                origin="system_detected",
                incident_fingerprint=fingerprint,
                detection={
                    "severity": severity,
                    "classification": classification,
                    "latest_event_id": event_id,
                    "latest_event_type": event_type,
                    "reporter_kind": reporter_kind,
                },
                status="awaiting_user_review",
                reporter_context=reporter_context,
                reporter_evidence_refs=refs,
                last_notified_at=utc_now(),
            )
            answers = default_intake_answers(title_seed=title)
            answers["bug_description"] = summary
            intake.answers_ref = self._write_intake_answers(intake, answers)
            self.intakes.save(intake)
            event = self.writer.emit(
                "self_issue.intake.auto_detected", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "intake_id": intake.intake_id,
                    "incident_fingerprint": fingerprint,
                    "source_event_id": event_id,
                    "source_event_type": event_type,
                    "reporter_kind": reporter_kind,
                    "notification_due": True,
                },
            )
            return {
                "ok": True, "status": "auto_intake_created",
                "intake": self._intake_view(intake), "event_id": event.id,
            }

    def get_intake(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        intake_id = str(payload.get("intake_id") or "").strip()
        intake = self.intakes.get(intake_id) if intake_id else self.intakes.latest()
        if intake is None or intake.status not in {
            "collecting", "awaiting_user_review", "submitted",
        }:
            return {"ok": True, "status": "intake_absent", "intake": None}
        return {"ok": True, "status": "intake_collecting", "intake": self._intake_view(intake)}

    def save_intake(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "intake.lock"):
            intake = self.intakes.get(str(payload.get("intake_id") or ""))
            if intake is None:
                raise ValueError("intake not found")
            if intake.status == "promoted":
                return self._promoted_intake_result(intake)
            if intake.status not in {"collecting", "awaiting_user_review", "submitted"}:
                raise ValueError("intake is not collecting")
            expected = int(payload.get("revision") or 0)
            if expected and expected != intake.revision:
                return {
                    "ok": False,
                    "status": "revision_conflict",
                    "reason": "intake revision changed",
                    "intake": self._intake_view(intake),
                }
            answers = normalize_intake_answers(payload.get("answers") or {}, complete=False)
            step = int(payload.get("current_step") or 0)
            updated = replace(
                intake,
                revision=intake.revision + 1,
                current_step=max(0, min(step, 7)),
                updated_at=utc_now(),
            )
            updated.answers_ref = self._write_intake_answers(updated, answers)
            self.intakes.save(updated)
            return {"ok": True, "status": "intake_saved", "intake": self._intake_view(updated)}

    def add_intake_attachment(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "intake.lock"):
            intake = self._required_intake(payload)
            if len(intake.attachment_refs) >= MAX_ATTACHMENTS:
                raise ValueError(f"at most {MAX_ATTACHMENTS} attachments are allowed")
            filename = _safe_filename(payload.get("filename"))
            suffix = Path(filename).suffix.lower()
            expected_type = ALLOWED_ATTACHMENTS.get(suffix)
            supplied_type = str(payload.get("content_type") or "").split(";", 1)[0].lower()
            if not expected_type or supplied_type != expected_type:
                raise ValueError("attachment type is unsupported or does not match its extension")
            try:
                content = base64.b64decode(str(payload.get("content_base64") or ""), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("attachment is not valid base64") from exc
            if not content or len(content) > MAX_ATTACHMENT_BYTES:
                raise ValueError("attachment must be non-empty and no larger than 20 MB")
            total = sum(int(item.get("byte_count") or 0) for item in intake.attachment_refs)
            if total + len(content) > MAX_TOTAL_ATTACHMENT_BYTES:
                raise ValueError("attachment total exceeds 50 MB")
            sanitized, redaction_applied = sanitize_attachment_for_disclosure(
                content, suffix=suffix, content_type=expected_type,
            )
            attachment_id = f"att-{uuid.uuid4().hex[:12]}"
            relative = (
                Path("artifacts") / "self-issue-intakes" / intake.intake_id
                / "attachments" / f"{attachment_id}-{filename}"
            )
            path = self.state_dir / relative
            _atomic_write_bytes(path, sanitized)
            digest = hashlib.sha256(sanitized).hexdigest()
            descriptor = {
                "ref_schema_version": "sidecar-ref.v1",
                "kind": "self_issue_user_attachment",
                "ref": relative.as_posix(),
                "sha256": digest,
                "byte_count": len(sanitized),
                "content_type": expected_type,
                "schema_version": "self-issue-attachment.v1",
                "encoding": "binary",
                "created_by": str(intake.reporter_context.get("reported_by") or "user"),
                "source_event_id": str(payload.get("source_event_id") or ""),
                "access_scope": {"external_disclosure": False},
                "retention": {"class": "user_controlled"},
                "required": False,
                "preview": filename,
                "attachment_id": attachment_id,
                "filename": filename,
                "redaction_applied": redaction_applied,
                "video_disclosure_confirmed": bool(payload.get("video_disclosure_confirmed")),
            }
            if expected_type.startswith("video/") and not descriptor["video_disclosure_confirmed"]:
                path.unlink(missing_ok=True)
                raise ValueError("video attachment requires explicit public-disclosure confirmation")
            updated = replace(
                intake,
                revision=intake.revision + 1,
                attachment_refs=[*intake.attachment_refs, descriptor],
                updated_at=utc_now(),
            )
            self.intakes.save(updated)
            self.writer.emit(
                "self_issue.intake.attachment_added", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "intake_id": intake.intake_id,
                    "attachment_id": attachment_id,
                    "sha256": digest,
                    "byte_count": len(sanitized),
                    "content_type": expected_type,
                },
            )
            return {"ok": True, "status": "attachment_added", "intake": self._intake_view(updated)}

    def remove_intake_attachment(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "intake.lock"):
            intake = self._required_intake(payload)
            attachment_id = str(payload.get("attachment_id") or "")
            target = next((
                item for item in intake.attachment_refs
                if str(item.get("attachment_id") or "") == attachment_id
            ), None)
            if target is None:
                raise ValueError("attachment not found")
            _safe_local_ref_path(self.state_dir, target).unlink(missing_ok=True)
            updated = replace(
                intake,
                revision=intake.revision + 1,
                attachment_refs=[item for item in intake.attachment_refs if item is not target],
                updated_at=utc_now(),
            )
            self.intakes.save(updated)
            return {"ok": True, "status": "attachment_removed", "intake": self._intake_view(updated)}

    def submit_intake(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "intake.lock"):
            intake = self.intakes.get(str(payload.get("intake_id") or ""))
            if intake is None:
                raise ValueError("intake not found")
            if intake.status == "promoted":
                return self._promoted_intake_result(intake)
            if intake.status not in {"collecting", "awaiting_user_review", "submitted"}:
                raise ValueError("intake cannot be submitted")
            answers = normalize_intake_answers(
                payload.get("answers") or self._intake_answers(intake), complete=False,
            )
            missing = first_missing_required(answers)
            if missing:
                return {
                    "ok": False,
                    "status": "intake_incomplete",
                    "missing_question_id": missing,
                    "reason": "This question can not be empty",
                    "intake": self._intake_view(intake, answers=answers),
                }
            if intake.attachment_refs and payload.get("attachment_disclosure_confirmed") is not True:
                return {
                    "ok": False,
                    "status": "attachment_disclosure_required",
                    "missing_question_id": "attachments_context",
                    "reason": "Confirm attachment visibility before submitting this report.",
                    "intake": self._intake_view(intake, answers=answers),
                }
            answers = normalize_intake_answers(answers, complete=True)
            intake.answers_ref = self._write_intake_answers(intake, answers)
            draft = self._promote_intake(intake, answers=answers)
            intake.status = "promoted"
            intake.promoted_draft_id = draft.draft_id
            intake.updated_at = utc_now()
            self.intakes.save(intake)
            event = self.writer.emit(
                "self_issue.intake.promoted", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "intake_id": intake.intake_id,
                    "draft_id": draft.draft_id,
                    "revision": draft.revision,
                    "attachment_count": len(draft.attachment_refs),
                },
            )
            return {
                "ok": True,
                "status": "draft_collecting_evidence",
                "draft": self._draft_view(draft),
                "event_id": event.id,
                "start_evidence": True,
            }

    def dismiss_intake(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "intake.lock"):
            intake = self.intakes.get(str(payload.get("intake_id") or ""))
            if intake is None:
                return {"ok": True, "status": "intake_absent", "intake_id": ""}
            if intake.status == "promoted":
                return self._promoted_intake_result(intake)
            if intake.status not in {"collecting", "awaiting_user_review", "submitted"}:
                raise ValueError("intake cannot be cancelled")
            for descriptor in intake.attachment_refs:
                _safe_local_ref_path(self.state_dir, descriptor).unlink(missing_ok=True)
            self.intakes.delete(intake.intake_id)
            artifact_root = (
                self.state_dir / "artifacts" / "self-issue-intakes" / intake.intake_id
            )
            if artifact_root.is_dir():
                shutil.rmtree(artifact_root)
            self.writer.emit(
                "self_issue.intake.cancelled", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "intake_id": intake.intake_id,
                    "origin": intake.origin,
                    "incident_fingerprint": intake.incident_fingerprint,
                },
            )
            return {"ok": True, "status": "intake_cancelled", "intake_id": intake.intake_id}

    def _promoted_intake_result(self, intake: SelfIssueIntake) -> dict[str, Any]:
        draft = self.drafts.get(intake.promoted_draft_id)
        if draft is None:
            raise ValueError("promoted Intake Draft is missing")
        return {
            "ok": True,
            "status": "intake_already_submitted",
            "intake_id": intake.intake_id,
            "draft": self._draft_view(draft),
        }

    def _promote_intake(
        self, intake: SelfIssueIntake, *, answers: dict[str, Any],
    ) -> IssueDraft:
        environment = answers.get("environment") or {}
        fingerprint = stable_digest({
            "authorization_domain": str(self.policy.authorization_domain or "gitlab.com").lower(),
            "reporter_subject": str(intake.reporter_context.get("reporter_subject") or "local"),
            "subject_scope": intake.subject_scope.lower(),
            "component": "unknown",
            "failure_category": "unknown",
            "error_signature": str(answers["bug_description"]).lower()[:300],
            "baseline_version": str(answers["zaofu_version"]).lower(),
        })
        duplicate = self.drafts.find_fingerprint(fingerprint)
        if (
            duplicate is not None
            and not duplicate.published_issue_ref
            and datetime.now(timezone.utc) - _parse_timestamp(duplicate.updated_at)
            <= timedelta(hours=24)
        ):
            updated = replace(
                duplicate,
                revision=duplicate.revision + 1,
                title=str(answers["title"]),
                summary=str(answers["bug_description"]),
                bug_description=str(answers["bug_description"]),
                reproduction_steps=str(answers["reproduction_steps"]),
                expected_behavior=str(answers["expected_behavior"]),
                environment={
                    "os": str(environment.get("os") or ""),
                    "version": str(environment.get("version") or ""),
                },
                zaofu_version=str(answers["zaofu_version"]),
                source_code_revision=self._git_head(),
                additional_context=str(answers["additional_context"]),
                attachment_context=str(answers["attachments_context"]),
                attachment_refs=[*duplicate.attachment_refs, *intake.attachment_refs],
                evidence_refs=[*duplicate.evidence_refs, *intake.reporter_evidence_refs],
                reporter_context=dict(intake.reporter_context),
                evidence_status="pending",
                runtime_status="unknown",
                evidence_collection_mode="pending",
                evidence_limit_reason="",
                evidence_run_id="",
                evidence_input_ref={},
                evidence_checkpoint_ref={},
                evidence_error="",
                assessment_status="not_started",
                assessment_claim_id="",
                assessment_claimed_at="",
                assessment_owner_pid=0,
                occurrence_count=duplicate.occurrence_count + 1,
                notification_due=(
                    datetime.now(timezone.utc) - _parse_timestamp(
                        duplicate.last_notified_at or duplicate.created_at
                    ) >= timedelta(hours=6)
                ),
                updated_at=utc_now(),
            )
            self.intents.invalidate_unpublished(
                duplicate.draft_id, reason="matching incident occurred again",
            )
            self.attachments.invalidate_unprepared(
                duplicate.draft_id, reason="matching incident occurred again",
            )
            self.drafts.save(updated)
            return updated
        analysis: dict[str, Any] = {}
        draft = IssueDraft(
            draft_id=f"sid-{uuid.uuid4().hex[:12]}",
            subject_scope=intake.subject_scope,
            target_binding=dict(intake.target_binding),
            incident_fingerprint=fingerprint,
            title=str(answers["title"]),
            summary=str(answers["bug_description"]),
            bug_description=str(answers["bug_description"]),
            reproduction_steps=str(answers["reproduction_steps"]),
            expected_behavior=str(answers["expected_behavior"]),
            environment={
                "os": str(environment.get("os") or ""),
                "version": str(environment.get("version") or ""),
            },
            zaofu_version=str(answers["zaofu_version"]),
            source_code_revision=self._git_head(),
            additional_context=str(answers["additional_context"]),
            attachment_context=str(answers["attachments_context"]),
            attachment_refs=list(intake.attachment_refs),
            evidence_refs=list(intake.reporter_evidence_refs),
            analysis=analysis,
            suggested_fix="Pending Orchestrator assessment.",
            recommended_next_action="Pending Orchestrator assessment.",
            reporter_context=dict(intake.reporter_context),
            evidence_status="pending",
            disclosure_fields=[
                "bug_description", "reproduction_steps", "expected_behavior",
                "environment", "zaofu_version", "additional_context",
                "attachment_context",
                "classification", "severity", "reproduction_status", "component",
                "impact_scope", "assessment_confidence", "analysis",
                "recommended_next_action", "evidence_refs",
                "published_attachments",
            ],
            last_notified_at=utc_now(),
        )
        self.drafts.save(draft)
        return draft

    def _validated_reporter_evidence_refs(
        self, value: object,
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list) or len(value) > 20:
            raise ValueError("reporter evidence_refs must be a list of at most 20 refs")
        accepted: list[dict[str, Any]] = []
        for raw in value:
            if not isinstance(raw, dict):
                raise ValueError("reporter evidence_refs must contain ref descriptors")
            relative = Path(str(raw.get("ref") or ""))
            root = self.state_dir.resolve()
            path = (root / relative).resolve()
            digest = str(raw.get("sha256") or "")
            if (
                relative.is_absolute()
                or not path.is_relative_to(root)
                or not path.is_file()
                or not digest
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("reporter evidence ref is missing, outside state, or changed")
            accepted.append({
                key: redact_obj(item)
                for key, item in raw.items()
                if key in {
                    "ref_schema_version", "kind", "ref", "sha256", "byte_count",
                    "content_type", "schema_version", "encoding", "created_by",
                    "source_event_id", "access_scope", "retention", "required", "preview",
                }
            })
        return accepted

    def _required_intake(self, payload: dict[str, Any]) -> SelfIssueIntake:
        intake = self.intakes.get(str(payload.get("intake_id") or ""))
        if intake is None or intake.status not in {"collecting", "awaiting_user_review"}:
            raise ValueError("collecting intake not found")
        return intake

    def _auto_intake_was_dismissed(self, fingerprint: str) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        try:
            events = self.writer.event_log.read_all()[-2000:]
        except Exception:
            return False
        return any(
            event.type == "self_issue.intake.cancelled"
            and isinstance(event.payload, dict)
            and event.payload.get("incident_fingerprint") == fingerprint
            and _parse_timestamp(event.ts) >= cutoff
            for event in events
        )

    def _write_intake_answers(
        self, intake: SelfIssueIntake, answers: dict[str, Any],
    ) -> dict[str, Any]:
        return write_sidecar_json(
            self.state_dir,
            f"artifacts/self-issue-intakes/{intake.intake_id}/answers.json",
            {
                "schema_version": QUESTION_SCHEMA_VERSION,
                "intake_id": intake.intake_id,
                "answers": answers,
                "updated_at": utc_now(),
            },
            kind="self_issue_intake_answers",
            schema_version=QUESTION_SCHEMA_VERSION,
            created_by="kernel",
            access_scope={"external_disclosure": False},
            required=True,
            preview="Local-only Self-Issue intake answers",
        )

    def _intake_answers(self, intake: SelfIssueIntake) -> dict[str, Any]:
        if not intake.answers_ref:
            return default_intake_answers()
        hydrated = hydrate_sidecar_ref(
            self.state_dir, intake.answers_ref,
            purpose="self_issue_intake_answers", actor="kernel",
        )
        body = hydrated.payload
        if not isinstance(body, dict) or body.get("intake_id") != intake.intake_id:
            raise ValueError("intake answer sidecar does not match the Intake")
        return normalize_intake_answers(body.get("answers") or {}, complete=False)

    def _intake_view(
        self, intake: SelfIssueIntake, *, answers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = intake.to_dict()
        value.pop("answers_ref", None)
        value["questions"] = intake_questions()
        value["answers"] = answers if answers is not None else self._intake_answers(intake)
        value["attachments"] = [
            {
                key: item.get(key)
                for key in (
                    "attachment_id", "filename", "byte_count", "content_type",
                    "sha256", "redaction_applied", "video_disclosure_confirmed",
                )
            }
            for item in intake.attachment_refs
        ]
        value["limits"] = {
            "max_files": MAX_ATTACHMENTS,
            "max_file_bytes": MAX_ATTACHMENT_BYTES,
            "max_total_bytes": MAX_TOTAL_ATTACHMENT_BYTES,
            "accepted_extensions": sorted(ALLOWED_ATTACHMENTS),
        }
        targets = self._configured_targets()
        value["target_policy"] = {
            "locked": bool(self.policy.enabled and self.policy.target_locked),
            "targets": {name: dict(target) for name, target in targets.items()},
            "allowed_modes": [
                mode for mode in ("gitlab", "github", "both")
                if self._mode_supported(mode)
            ],
            "default_mode": self.policy.default_publication_mode,
        }
        return value


def _reporter_context(payload: dict[str, Any]) -> dict[str, Any]:
    supplied = payload.get("reporter_context")
    supplied = dict(supplied) if isinstance(supplied, dict) else {}
    discovered_by = str(supplied.get("discovered_by") or payload.get("actor") or "user")
    identity = str(payload.get("user_id") or supplied.get("session_id") or "local-user")
    browser_capture = supplied.get("browser_capture")
    browser_capture = browser_capture if isinstance(browser_capture, dict) else {}
    return redact_obj({
        "discovered_by": discovered_by,
        "reported_by": str(supplied.get("reported_by") or discovered_by),
        "collected_by": str(supplied.get("collected_by") or "kernel"),
        "assessed_by": "orchestrator",
        "reporter_fallback": (
            "orchestrator"
            if (
                str(supplied.get("reporter_fallback") or "") == "orchestrator"
                or discovered_by not in {"worker", "kernel"}
            ) else ""
        ),
        "role": str(supplied.get("role") or "user"),
        "task_id": str(supplied.get("task_id") or payload.get("task_id") or ""),
        "session_id": str(supplied.get("session_id") or ""),
        "source_event_id": str(supplied.get("source_event_id") or ""),
        "browser_capture": {
            "requested": bool(browser_capture.get("requested")),
            "target": (
                "kanban_board"
                if str(browser_capture.get("target") or "") == "kanban_board" else ""
            ),
            "base_url": str(browser_capture.get("base_url") or "")[:500],
            "project_id": str(browser_capture.get("project_id") or "")[:200],
        },
        "reporter_subject": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
    })


def _severity_rank(value: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(value, 3)


def _merge_refs(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]], *, limit: int,
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in [*existing, *incoming]:
        merged[(str(item.get("ref") or ""), str(item.get("sha256") or ""))] = item
    return list(merged.values())[-limit:]


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_filename(value: object) -> str:
    # Keep Unicode names (for example ``现场截图.png``) while rejecting path
    # syntax and controls. ASCII-only rewriting used to erase the extension.
    name = unicodedata.normalize("NFC", str(value or "attachment"))
    name = _UNSAFE_FILENAME.sub("-", name).strip().strip(".")
    if not name or name in {".", ".."}:
        raise ValueError("attachment filename is invalid")
    if len(name) <= 120:
        return name
    suffix = Path(name).suffix[:20]
    if not suffix:
        return name[:120]
    return f"{name[:-len(suffix)][:120 - len(suffix)]}{suffix}"


def sanitize_attachment_for_disclosure(
    content: bytes, *, suffix: str, content_type: str,
) -> tuple[bytes, bool]:
    if content_type in {"text/plain", "application/json"}:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("text attachment must be valid UTF-8") from exc
        if content_type == "application/json":
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError("JSON attachment is invalid") from exc
        redacted = redact_text(text)
        return redacted.encode("utf-8"), redacted != text
    if not _matches_magic(content, suffix):
        raise ValueError("attachment content does not match its declared type")
    if suffix == ".png":
        return _strip_png_metadata(content), True
    if suffix in {".jpg", ".jpeg"}:
        return _strip_jpeg_metadata(content), True
    return content, False


def _matches_magic(content: bytes, suffix: str) -> bool:
    checks = {
        ".png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": content.startswith(b"\xff\xd8\xff"),
        ".jpeg": content.startswith(b"\xff\xd8\xff"),
        ".mp4": len(content) >= 12 and b"ftyp" in content[4:12],
        ".webm": content.startswith(b"\x1aE\xdf\xa3"),
    }
    return bool(checks.get(suffix))


def _strip_png_metadata(content: bytes) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    if not content.startswith(signature):
        raise ValueError("PNG attachment is invalid")
    offset = len(signature)
    output = bytearray(signature)
    allowed = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}
    saw_ihdr = saw_idat = saw_iend = False
    while offset + 12 <= len(content):
        size = int.from_bytes(content[offset:offset + 4], "big")
        end = offset + 12 + size
        if size > MAX_ATTACHMENT_BYTES or end > len(content):
            raise ValueError("PNG attachment has an invalid chunk")
        kind = content[offset + 4:offset + 8]
        data = content[offset + 8:offset + 8 + size]
        expected_crc = int.from_bytes(content[offset + 8 + size:end], "big")
        if zlib.crc32(kind + data) & 0xFFFFFFFF != expected_crc:
            raise ValueError("PNG attachment checksum is invalid")
        if kind in allowed:
            output.extend(content[offset:end])
        saw_ihdr = saw_ihdr or kind == b"IHDR"
        saw_idat = saw_idat or kind == b"IDAT"
        saw_iend = saw_iend or kind == b"IEND"
        offset = end
        if kind == b"IEND":
            break
    if not (saw_ihdr and saw_idat and saw_iend) or offset != len(content):
        raise ValueError("PNG attachment structure is invalid")
    return bytes(output)


def _strip_jpeg_metadata(content: bytes) -> bytes:
    if not content.startswith(b"\xff\xd8"):
        raise ValueError("JPEG attachment is invalid")
    output = bytearray(content[:2])
    offset = 2
    while offset < len(content):
        if content[offset] != 0xFF:
            raise ValueError("JPEG attachment structure is invalid")
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            raise ValueError("JPEG attachment is truncated")
        marker = content[offset]
        offset += 1
        if marker == 0xD9:
            output.extend(b"\xff\xd9")
            return bytes(output)
        if marker == 0xDA:
            if offset + 2 > len(content):
                raise ValueError("JPEG scan is truncated")
            size = int.from_bytes(content[offset:offset + 2], "big")
            if size < 2 or offset + size > len(content):
                raise ValueError("JPEG scan header is invalid")
            output.extend(b"\xff\xda")
            output.extend(content[offset:])
            return bytes(output)
        if marker in range(0xD0, 0xD8) or marker == 0x01:
            output.extend(bytes((0xFF, marker)))
            continue
        if offset + 2 > len(content):
            raise ValueError("JPEG segment is truncated")
        size = int.from_bytes(content[offset:offset + 2], "big")
        end = offset + size
        if size < 2 or end > len(content):
            raise ValueError("JPEG segment is invalid")
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
            output.extend(bytes((0xFF, marker)))
            output.extend(content[offset:end])
        offset = end
    raise ValueError("JPEG attachment has no image scan")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _safe_local_ref_path(state_dir: Path, descriptor: dict[str, Any]) -> Path:
    relative = Path(str(descriptor.get("ref") or ""))
    root = Path(state_dir).resolve()
    candidate = (root / relative).resolve()
    if relative.is_absolute() or not candidate.is_relative_to(root):
        raise ValueError("attachment ref leaves the configured state dir")
    return candidate
