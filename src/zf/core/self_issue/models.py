"""Provider-neutral Self-Issue records owned by the deterministic kernel."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


CLASSIFICATIONS = frozenset({
    "bug",
    "runtime", "kernel/state", "provider/integration", "web/ui",
    "configuration", "security", "performance", "test/regression", "unknown",
})
SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
REPRODUCTION_STATUSES = frozenset({"reproduced", "observed", "unverified"})
PUBLICATION_STATES = frozenset({
    "draft", "previewed", "confirmed", "publishing", "published",
    "partially_published", "publish_failed", "outcome_unknown", "invalidated",
})
PUBLICATION_BATCH_STATES = frozenset({
    "previewed", "confirmed", "publishing", "partially_published", "published",
    "publish_failed", "outcome_unknown", "invalidated",
})
PUBLICATION_MODES = frozenset({"gitlab", "github", "both"})
TARGET_BINDING_FIELDS = frozenset({"provider", "project"})
INTAKE_STATUSES = frozenset({
    "collecting", "awaiting_user_review", "submitted", "promoted", "cancelled",
})
INTAKE_ORIGINS = frozenset({"manual", "system_detected"})
RUNTIME_STATUSES = frozenset({"live", "stopped", "unknown"})
EVIDENCE_STATUSES = frozenset({
    "pending", "collecting_static", "waiting_for_runtime", "collecting_live",
    "interrupted", "completed", "failed",
})
ASSESSMENT_STATUSES = frozenset({
    "not_started", "waiting_for_evidence", "waiting_for_runtime", "running",
    "completed", "skipped", "failed",
})
EVIDENCE_COLLECTION_MODES = frozenset({"pending", "static_only", "limited", "full"})
ATTACHMENT_PREPARATION_STATES = frozenset({
    "previewed", "confirmed", "preparing", "prepared", "failed",
    "outcome_unknown", "invalidated",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class SelfIssueIntake:
    intake_id: str
    subject_scope: str
    target_binding: dict[str, str]
    origin: str = "manual"
    incident_fingerprint: str = ""
    detection: dict[str, Any] = field(default_factory=dict)
    question_schema_version: str = "self-issue-intake.v1"
    revision: int = 1
    status: str = "collecting"
    current_step: int = 0
    answers_ref: dict[str, Any] = field(default_factory=dict)
    attachment_refs: list[dict[str, Any]] = field(default_factory=list)
    reporter_evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    reporter_context: dict[str, Any] = field(default_factory=dict)
    promoted_draft_id: str = ""
    occurrence_count: int = 1
    notification_due: bool = True
    last_notified_at: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if not self.intake_id or not self.subject_scope:
            raise ValueError("intake_id and subject_scope are required")
        if self.status not in INTAKE_STATUSES:
            raise ValueError("unsupported intake status")
        if self.origin not in INTAKE_ORIGINS:
            raise ValueError("unsupported intake origin")
        if self.origin == "system_detected" and not self.incident_fingerprint:
            raise ValueError("system-detected intake requires an incident fingerprint")
        if not isinstance(self.detection, dict):
            raise ValueError("intake detection must be an object")
        if self.occurrence_count < 1:
            raise ValueError("intake occurrence_count must be positive")
        if self.current_step < 0 or self.current_step > 7:
            raise ValueError("intake current_step must be between 0 and 7")
        if (
            not isinstance(self.target_binding, dict)
            or not set(self.target_binding) <= TARGET_BINDING_FIELDS
            or not all(isinstance(value, str) for value in self.target_binding.values())
        ):
            raise ValueError("target_binding must use provider-neutral provider/project fields")
        if not isinstance(self.answers_ref, dict) or (
            self.answers_ref and not str(self.answers_ref.get("ref") or "")
        ):
            raise ValueError("answers_ref must be an empty object or sidecar ref")
        if not all(
            isinstance(item, dict) and str(item.get("ref") or "")
            for item in self.attachment_refs
        ):
            raise ValueError("attachment_refs must contain ref descriptors")
        if not all(
            isinstance(item, dict) and str(item.get("ref") or "")
            for item in self.reporter_evidence_refs
        ):
            raise ValueError("reporter_evidence_refs must contain ref descriptors")
        if not isinstance(self.reporter_context, dict):
            raise ValueError("reporter_context must be an object")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SelfIssueIntake":
        intake = cls(**value)
        intake.validate()
        return intake


@dataclass
class IssueDraft:
    draft_id: str
    subject_scope: str
    target_binding: dict[str, str]
    incident_fingerprint: str
    title: str
    summary: str
    schema_version: str = "issue-draft.v2"
    revision: int = 1
    classification: str = "unknown"
    severity: str = "P2"
    reproduction_status: str = "unverified"
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    disclosure_fields: list[str] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    suggested_fix: str = ""
    # Explicit user-edited publication Markdown; None uses the report renderer.
    markdown_body: str | None = None
    bug_description: str = ""
    reproduction_steps: str = ""
    actual_behavior: str = ""
    expected_behavior: str = ""
    reported_expected_behavior: str = ""
    canonical_expected_behavior: str = ""
    environment: dict[str, str] = field(default_factory=dict)
    # Provider-neutral project version. ``zaofu_version`` remains a legacy
    # wire alias so existing Drafts can be replayed without migration loss.
    project_version: str = ""
    zaofu_version: str = ""
    # Exact canonical checkout captured when the intake is promoted.  Keep
    # this separate from ``zaofu_version``: the latter is the user-facing
    # package version entered in the questionnaire (for example, 0.0.7),
    # while this commit binds the published Issue to the source that was
    # actually reported.
    source_code_revision: str = ""
    additional_context: str = ""
    attachment_context: str = ""
    attachment_refs: list[dict[str, Any]] = field(default_factory=list)
    published_attachments: list[dict[str, Any]] = field(default_factory=list)
    component: str = "unknown"
    impact_scope: str = "unknown"
    assessment_confidence: str = "low"
    recommended_next_action: str = ""
    reporter_context: dict[str, Any] = field(default_factory=dict)
    runtime_status: str = "unknown"
    evidence_status: str = "pending"
    evidence_collection_mode: str = "pending"
    evidence_limit_reason: str = ""
    evidence_run_id: str = ""
    evidence_base_revision: int = 0
    evidence_input_ref: dict[str, Any] = field(default_factory=dict)
    investigation_plan_ref: dict[str, Any] = field(default_factory=dict)
    evidence_result_ref: dict[str, Any] = field(default_factory=dict)
    evidence_checkpoint_ref: dict[str, Any] = field(default_factory=dict)
    evidence_error: str = ""
    assessment_status: str = "not_started"
    assessment_claim_id: str = ""
    assessment_claimed_at: str = ""
    assessment_owner_pid: int = 0
    publication_state: str = "draft"
    published_issue_ref: dict[str, str] = field(default_factory=dict)
    occurrence_count: int = 1
    notification_due: bool = True
    last_notified_at: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if not self.draft_id or not self.subject_scope or not self.incident_fingerprint:
            raise ValueError("draft_id, subject_scope, and incident_fingerprint are required")
        if self.schema_version != "issue-draft.v2":
            raise ValueError("unsupported Issue Draft schema_version")
        if self.classification not in CLASSIFICATIONS:
            raise ValueError("unsupported classification")
        if self.severity not in SEVERITIES:
            raise ValueError("unsupported severity")
        if self.reproduction_status not in REPRODUCTION_STATUSES:
            raise ValueError("unsupported reproduction_status")
        if self.publication_state not in PUBLICATION_STATES:
            raise ValueError("unsupported publication_state")
        if self.evidence_status not in EVIDENCE_STATUSES:
            raise ValueError("unsupported evidence_status")
        if self.runtime_status not in RUNTIME_STATUSES:
            raise ValueError("unsupported runtime_status")
        if self.assessment_status not in ASSESSMENT_STATUSES:
            raise ValueError("unsupported assessment_status")
        if self.evidence_collection_mode not in EVIDENCE_COLLECTION_MODES:
            raise ValueError("unsupported evidence_collection_mode")
        if self.assessment_owner_pid < 0:
            raise ValueError("assessment_owner_pid must not be negative")
        for name, ref in (
            ("evidence_input_ref", self.evidence_input_ref),
            ("investigation_plan_ref", self.investigation_plan_ref),
            ("evidence_result_ref", self.evidence_result_ref),
            ("evidence_checkpoint_ref", self.evidence_checkpoint_ref),
        ):
            if not isinstance(ref, dict) or (ref and not str(ref.get("ref") or "")):
                raise ValueError(f"{name} must be an empty object or sidecar ref")
        if (
            not isinstance(self.target_binding, dict)
            or not set(self.target_binding) <= TARGET_BINDING_FIELDS
        ):
            raise ValueError("target_binding must use provider-neutral provider/project fields")
        if not all(isinstance(value, str) for value in self.target_binding.values()):
            raise ValueError("target_binding values must be strings")
        if not all(isinstance(item, dict) and str(item.get("ref") or "") for item in self.evidence_refs):
            raise ValueError("evidence_refs must contain ref descriptors")
        if not isinstance(self.analysis, dict):
            raise ValueError("analysis must be an object")
        if self.markdown_body is not None and not isinstance(self.markdown_body, str):
            raise ValueError("markdown_body must be text or null")
        if not isinstance(self.environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.environment.items()
        ):
            raise ValueError("environment must be a string map")
        if self.assessment_confidence not in {"low", "medium", "high"}:
            raise ValueError("assessment_confidence must be low, medium, or high")
        if not isinstance(self.reporter_context, dict):
            raise ValueError("reporter_context must be an object")
        for name, refs in (
            ("attachment_refs", self.attachment_refs),
            ("published_attachments", self.published_attachments),
        ):
            if not isinstance(refs, list) or not all(isinstance(item, dict) for item in refs):
                raise ValueError(f"{name} must be an object list")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "IssueDraft":
        migrated = dict(value)
        migrated.setdefault(
            "reported_expected_behavior",
            str(migrated.get("expected_behavior") or ""),
        )
        legacy_evidence_status = str(migrated.get("evidence_status") or "")
        if legacy_evidence_status == "running":
            migrated["evidence_status"] = "collecting_live"
            migrated.setdefault("assessment_status", "running")
        elif legacy_evidence_status == "conflict":
            migrated["evidence_status"] = "failed"
            migrated.setdefault("assessment_status", "failed")
        draft = cls(**migrated)
        draft.validate()
        return draft


@dataclass
class PublicationIntent:
    intent_id: str
    draft_id: str
    draft_revision: int
    payload: dict[str, Any]
    payload_digest: str
    redaction_digest: str
    disclosure_digest: str
    target_binding: dict[str, str]
    credential_subject: str
    permission_snapshot: dict[str, Any]
    marker: str
    publication_snapshot_ref: dict[str, Any] = field(default_factory=dict)
    handoff_ref: dict[str, Any] = field(default_factory=dict)
    batch_id: str = ""
    status: str = "previewed"
    confirmation_id: str = ""
    confirmation_expires_at: str = ""
    confirmed_at: str = ""
    published_issue_ref: dict[str, str] = field(default_factory=dict)
    evidence_comment_status: str = "not_required"
    evidence_comment_id: str = ""
    evidence_comment_url: str = ""
    evidence_comment_digest: str = ""
    evidence_comment_attachments: list[dict[str, Any]] = field(default_factory=list)
    evidence_comment_failure_reason: str = ""
    outcome_evidence_refs: list[str] = field(default_factory=list)
    failure_reason: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if self.status not in PUBLICATION_STATES - {"draft"}:
            raise ValueError("unsupported publication intent status")
        if stable_digest(self.payload) != self.payload_digest:
            raise ValueError("publication payload digest mismatch")
        if not isinstance(self.target_binding, dict) or not set(
            self.target_binding
        ) <= TARGET_BINDING_FIELDS:
            raise ValueError("publication target must be provider-neutral")
        for name, ref in (
            ("publication_snapshot_ref", self.publication_snapshot_ref),
            ("handoff_ref", self.handoff_ref),
        ):
            if not isinstance(ref, dict) or (ref and not str(ref.get("ref") or "")):
                raise ValueError(f"{name} must be an empty object or sidecar ref")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PublicationIntent":
        intent = cls(**value)
        intent.validate()
        return intent


@dataclass
class PublicationBatch:
    batch_id: str
    draft_id: str
    draft_revision: int
    publication_mode: str
    selected_providers: list[str]
    intent_ids: dict[str, str]
    payload_digest: str
    status: str = "previewed"
    confirmation_id: str = ""
    confirmation_expires_at: str = ""
    confirmed_at: str = ""
    failure_reason: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if not self.batch_id or not self.draft_id:
            raise ValueError("batch_id and draft_id are required")
        if self.publication_mode not in PUBLICATION_MODES:
            raise ValueError("unsupported publication mode")
        if self.status not in PUBLICATION_BATCH_STATES:
            raise ValueError("unsupported publication batch status")
        if (
            not self.selected_providers
            or len(self.selected_providers) != len(set(self.selected_providers))
            or any(provider not in {"gitlab", "github"} for provider in self.selected_providers)
        ):
            raise ValueError("publication batch providers are invalid")
        if set(self.intent_ids) != set(self.selected_providers) or not all(
            isinstance(value, str) and value for value in self.intent_ids.values()
        ):
            raise ValueError("publication batch intent map is incomplete")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PublicationBatch":
        batch = cls(**value)
        batch.validate()
        return batch


@dataclass
class AttachmentPreparationIntent:
    preparation_id: str
    draft_id: str
    draft_revision: int
    target_binding: dict[str, str]
    credential_subject: str
    attachment_manifest: list[dict[str, Any]]
    manifest_digest: str
    status: str = "previewed"
    confirmation_id: str = ""
    confirmation_expires_at: str = ""
    confirmed_at: str = ""
    prepared_attachments: list[dict[str, Any]] = field(default_factory=list)
    outcome_evidence_refs: list[str] = field(default_factory=list)
    failure_reason: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if not self.preparation_id or not self.draft_id:
            raise ValueError("preparation_id and draft_id are required")
        if self.status not in ATTACHMENT_PREPARATION_STATES:
            raise ValueError("unsupported attachment preparation status")
        if stable_digest(self.attachment_manifest) != self.manifest_digest:
            raise ValueError("attachment manifest digest mismatch")
        if not isinstance(self.target_binding, dict) or not set(
            self.target_binding
        ) <= TARGET_BINDING_FIELDS:
            raise ValueError("attachment target must be provider-neutral")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AttachmentPreparationIntent":
        intent = cls(**value)
        intent.validate()
        return intent
