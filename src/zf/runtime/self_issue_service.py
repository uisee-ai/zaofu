"""Deterministic Self-Issue kernel service."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import shutil
import subprocess
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zf.core.config.schema import SelfIssueConfig
from zf.core.events import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.security.secret_provider import (
    LocalSecretProvider,
    SecretKey,
    SecretProvider,
    forge_credential_store_path,
    forge_credential_workspace_id,
)
from zf.core.self_issue.markdown import render_publication_markdown
from zf.core.self_issue.models import IssueDraft, PublicationIntent, stable_digest, utc_now
from issue_core.environment import detect_environment, detect_project_version
from issue_core.normalizer import IssueUnderstandingAgent
from issue_core.structure import structure_report
from issue_core.enrichment import DraftRevision, EvaluationRun, Resolution
from zf.core.self_issue.safe_export import (
    safe_export_obj as _safe_export_obj,
    safe_report_text as _safe_report_text,
)
from zf.core.self_issue.store import (
    AttachmentPreparationStore,
    IssueDraftStore,
    PublicationBatchStore,
    PublicationIntentStore,
    SelfIssueIntakeStore,
)
from zf.core.self_issue.enrichment_store import EnrichmentProjection, IssueEnrichmentStore
from zf.core.state.locks import locked_path
from zf.integrations.forge.base import ForgeProvider, ForgeResult, IssuePublishRequest
from zf.integrations.forge.gitlab import GitLabComProvider
from zf.integrations.forge.github import GitHubComProvider, GitHubDeviceOAuthClient
from zf.integrations.forge.github_auth import (
    GitHubCredentialAssessment,
    assess_github_credential,
)
from zf.integrations.forge.oauth import GitLabOAuthClient
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.sidecar_refs import write_sidecar_bytes
from zf.runtime.self_issue_attachments import SelfIssueAttachmentMixin
from zf.runtime.self_issue_browser_evidence import (
    BrowserCaptureResult,
    capture_self_issue_browser_evidence,
)
from zf.runtime.self_issue_evidence_activity import read_evidence_activity
from zf.runtime.self_issue_evidence_run import SelfIssueEvidenceRunMixin
from zf.runtime.self_issue_intake import (
    ALLOWED_ATTACHMENTS,
    MAX_ATTACHMENTS,
    MAX_ATTACHMENT_BYTES,
    MAX_TOTAL_ATTACHMENT_BYTES,
    SelfIssueIntakeMixin,
    _safe_filename,
    sanitize_attachment_for_disclosure,
)
from zf.runtime.self_issue_evidence import summarize_web_api_timing
from zf.runtime.self_issue_log_evidence import collect_log_evidence
from zf.runtime.self_issue_liveness import self_issue_runtime_status
from zf.runtime.self_issue_github_oauth import SelfIssueGitHubOAuthMixin
from zf.runtime.self_issue_oauth import SelfIssueOAuthMixin
from zf.runtime.self_issue_publication import SelfIssuePublicationMixin
from zf.runtime.self_issue_target import resolve_self_issue_target_root


DISCLOSURE_ALLOWLIST = frozenset({
    "bug_description", "reproduction_steps", "actual_behavior", "expected_behavior", "environment",
    "project_version", "zaofu_version", "additional_context", "attachment_context", "classification", "severity",
    "reproduction_status", "component", "impact_scope", "assessment_confidence",
    "analysis", "recommended_next_action",
    "source_code_revision",
    "evidence_refs", "published_attachments", "evidence_collection_status",
    "runtime_status", "assessment_status", "evidence_collection_mode",
    "evidence_limit_reason",
})
PUBLICATION_READY_EVIDENCE_STATUSES = frozenset({
    "completed", "interrupted", "failed",
})
CONFIRMATION_TTL = timedelta(minutes=10)
TOKEN_REFRESH_LEEWAY = timedelta(seconds=30)
STRUCTURE_RETRY_FIELDS = frozenset({
    "title", "summary", "bug_description", "reproduction_steps",
    "actual_behavior", "expected_behavior", "reported_expected_behavior",
    "environment", "project_version", "additional_context", "attachment_context",
})


class SelfIssueService(
    SelfIssueIntakeMixin,
    SelfIssueEvidenceRunMixin,
    SelfIssueAttachmentMixin,
    SelfIssueOAuthMixin,
    SelfIssueGitHubOAuthMixin,
    SelfIssuePublicationMixin,
):
    def __init__(
        self,
        state_dir: Path,
        writer: EventWriter,
        *,
        project_root: Path,
        forge_provider: ForgeProvider | None = None,
        secret_provider: SecretProvider | None = None,
        oauth_client: GitLabOAuthClient | None = None,
        forge_providers: dict[str, ForgeProvider] | None = None,
        github_oauth_client: GitHubDeviceOAuthClient | None = None,
        policy: SelfIssueConfig | None = None,
        issue_understander: IssueUnderstandingAgent | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        from zf.runtime.issue_settings import current_issue_policy
        self.policy = current_issue_policy(project_root, policy)
        self.project_root = resolve_self_issue_target_root(project_root, self.policy)
        self.writer = writer
        self.enrichment = IssueEnrichmentStore(
            self.state_dir / "self-issues" / "enrichment.json",
        )
        self.drafts = IssueDraftStore(
            self.state_dir / "self-issues" / "drafts.json",
            on_save=self.enrichment.sync_draft,
        )
        self.intakes = SelfIssueIntakeStore(self.state_dir / "self-issues" / "intakes.json")
        self.intents = PublicationIntentStore(
            self.state_dir / "self-issues" / "publication-intents.json",
        )
        self.batches = PublicationBatchStore(
            self.state_dir / "self-issues" / "publication-batches.json",
        )
        self.attachments = AttachmentPreparationStore(
            self.state_dir / "self-issues" / "attachment-preparations.json",
        )
        self.secrets = secret_provider or LocalSecretProvider(
            forge_credential_store_path(self.state_dir),
        )
        default_forge = forge_provider or GitLabComProvider()
        self.forges: dict[str, ForgeProvider] = {
            "gitlab": GitLabComProvider(),
            "github": GitHubComProvider(),
            **(forge_providers or {}),
        }
        self.forges[default_forge.name] = default_forge
        self.forge = default_forge
        self.oauth = oauth_client or GitLabOAuthClient()
        self.github_oauth = github_oauth_client or GitHubDeviceOAuthClient()
        self.issue_understander = issue_understander

    def capture(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        # The new `/issue <description>` path creates an editable Draft
        # immediately.  Keep the legacy empty/explicit questionnaire path for
        # API compatibility and for callers that intentionally request it.
        if bool(payload.get("direct_report")):
            return self.capture_direct_report(payload, causation_id=causation_id)
        return self.start_intake(payload, causation_id=causation_id)

    def capture_direct_report(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        """Create a provider-neutral Draft from one free-form user report.

        No project assessment, runtime inspection, or publication is started
        here.  The caller must later submit an explicit assessment decision.
        """
        with locked_path(self.state_dir / "self-issues" / "draft.lock"):
            description = str(payload.get("description") or "").strip()
            env = payload.get("environment")
            # A browser adapter may send an empty or partial object when the
            # platform API is unavailable.  Treat that as missing detection,
            # not as an authoritative environment, so CLI/host detection can
            # still fill the Draft deterministically.
            if not isinstance(env, dict) or not str(env.get("os") or "").strip() or not str(
                env.get("version") or ""
            ).strip():
                env = detect_environment()
            version = str(
                payload.get("project_version") or payload.get("zaofu_version") or ""
            ).strip()
            version_detection = {
                "value": version,
                "source": "user" if version else "unknown",
                "confidence": "high" if version else "low",
            }
            if not version:
                version_detection = detect_project_version(self.project_root)
                version = version_detection["value"]
            structured = structure_report(
                self.issue_understander,
                description,
                environment={str(key): str(value) for key, value in env.items()},
                project_version=version,
                explicit_title=str(payload.get("title") or ""),
                local_timestamp=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
            )
            proposal = dict(structured.proposal)
            target = self._target_binding(payload.get("target_binding") or {})
            subject_scope = str(payload.get("subject_scope") or "project").strip()[:120]
            supplied_reporter = payload.get("reporter_context")
            reporter = redact_obj(
                dict(supplied_reporter) if isinstance(supplied_reporter, dict) else {
                    "discovered_by": "user",
                    "reported_by": "user",
                    "collected_by": "kernel",
                    "role": "user",
                },
            )
            fingerprint = stable_digest({
                "subject_scope": subject_scope.lower(),
                "target_binding": target,
                "description": description.lower()[:2000],
            })
            existing = self.drafts.find_fingerprint(fingerprint)
            if existing is not None and not existing.published_issue_ref:
                updated = replace(
                    existing,
                    revision=existing.revision + 1,
                    title=proposal["title"],
                    summary=proposal["summary"],
                    bug_description=proposal["bug_description"],
                    reproduction_steps=proposal["reproduction_steps"],
                    actual_behavior=proposal["actual_behavior"],
                    expected_behavior=proposal["expected_behavior"],
                    reported_expected_behavior=proposal["expected_behavior"],
                    environment=proposal["environment"],
                    project_version=proposal.get("project_version", ""),
                    zaofu_version=proposal.get("project_version", ""),
                    additional_context=proposal["additional_context"],
                    attachment_context=proposal["attachment_context"],
                    reporter_context=reporter,
                    source_code_revision=self._git_head(),
                    updated_at=utc_now(),
                )
                self.drafts.save(updated)
                return {"ok": True, "status": "draft_ready", "draft": self._draft_view(updated)}
            draft_id = f"sid-{uuid.uuid4().hex[:12]}"
            raw_report_ref = write_sidecar_json(
                self.state_dir,
                f"artifacts/self-issues/{draft_id}/raw-report.json",
                {
                    "schema_version": "issue-raw-report.v1",
                    "draft_id": draft_id,
                    "description": description,
                },
                kind="self_issue_raw_report",
                schema_version="issue-raw-report.v1",
                created_by="user",
                access_scope={"external_disclosure": False},
                required=True,
                preview="Local-only original Issue report",
            )
            draft = IssueDraft(
                draft_id=draft_id,
                subject_scope=subject_scope,
                target_binding=target,
                incident_fingerprint=fingerprint,
                title=proposal["title"],
                summary=proposal["summary"],
                bug_description=proposal["bug_description"],
                reproduction_steps=proposal["reproduction_steps"],
                actual_behavior=proposal["actual_behavior"],
                expected_behavior=proposal["expected_behavior"],
                reported_expected_behavior=proposal["expected_behavior"],
                environment=proposal["environment"],
                project_version=proposal.get("project_version", ""),
                zaofu_version=proposal.get("project_version", ""),
                source_code_revision=self._git_head(),
                additional_context=proposal["additional_context"],
                attachment_context=proposal["attachment_context"],
                analysis={
                    "understanding": proposal["understanding"],
                    "project_version_detection": version_detection,
                },
                evidence_refs=[raw_report_ref],
                disclosure_fields=[
                    "bug_description", "reproduction_steps", "actual_behavior",
                    "expected_behavior", "environment", "project_version",
                    "additional_context", "attachment_context", "classification",
                    "severity", "reproduction_status", "component", "impact_scope",
                    "assessment_confidence", "analysis", "recommended_next_action",
                    "published_attachments",
                ],
                reporter_context=reporter,
            )
            draft.validate()
            self.drafts.save(draft)
            self._seed_field_metadata(draft, source="structure", user_locked=False)
            event = self.writer.emit(
                "self_issue.draft.created", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "draft_id": draft.draft_id,
                    "revision": draft.revision,
                    "source": "free_form_report",
                    "assessment": "not_started",
                    "understanding_status": structured.understanding_status,
                    "understanding_reason": structured.reason_code,
                },
            )
            return {
                "ok": True, "status": "draft_ready", "draft": self._draft_view(draft),
                "event_id": event.id, "start_evidence": False,
            }

    def update(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "update.lock"):
            draft = self._required_draft(payload)
            locked = self._evaluation_lock_response(draft, action="draft_update")
            if locked is not None:
                return locked
            if draft.publication_state in {
                "publishing", "published", "partially_published", "outcome_unknown",
            }:
                return {
                    "ok": False,
                    "status": "published_immutable",
                    "reason": "Published Issue content is immutable; create a new Self-Issue report.",
                    "draft": self._draft_view(draft),
                }
            expected = int(payload.get("revision") or 0)
            if expected != draft.revision:
                return {"ok": False, "status": "revision_conflict", "reason": "draft revision changed"}
            allowed = {
                "title", "summary", "classification", "severity", "reproduction_status",
                "analysis", "suggested_fix", "disclosure_fields", "target_binding",
                "bug_description", "reproduction_steps", "expected_behavior",
                "environment", "project_version", "zaofu_version", "additional_context", "component",
                "actual_behavior",
                "attachment_context", "markdown_body",
                "impact_scope", "assessment_confidence", "recommended_next_action",
            }
            updates = {key: redact_obj(payload[key]) for key in allowed if key in payload}
            if "expected_behavior" in updates:
                updates["reported_expected_behavior"] = updates["expected_behavior"]
            updates["target_binding"] = self._target_binding(
                updates.get("target_binding", draft.target_binding),
            )
            changed_fields = tuple(sorted(
                key for key, value in updates.items()
                if getattr(draft, key) != value
            ))
            if not changed_fields:
                return {
                    "ok": True,
                    "status": "draft_unchanged",
                    "draft": self._draft_view(draft),
                }
            updated = replace(
                draft,
                **updates,
                revision=draft.revision + 1,
                publication_state="draft",
                updated_at=utc_now(),
            )
            updated.validate()
            self.intents.invalidate_unpublished(
                draft.draft_id, reason="Draft content changed",
            )
            self.batches.invalidate_unpublished(
                draft.draft_id, reason="Draft content changed",
            )
            self.attachments.invalidate_unprepared(
                draft.draft_id, reason="Draft content changed",
            )
            self.drafts.save(updated)
            self._lock_user_fields(updated, changed_fields=changed_fields)
            event = self.writer.emit(
                "self_issue.draft.updated", actor="kernel", causation_id=causation_id or None,
                payload={"draft_id": updated.draft_id, "revision": updated.revision},
            )
            return {"ok": True, "status": "draft_updated", "draft": self._draft_view(updated), "event_id": event.id}

    def retry_understanding(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "draft.lock"):
            draft = self._required_draft(payload)
            locked = self._evaluation_lock_response(draft, action="understanding_retry")
            if locked is not None:
                return locked
            expected = int(payload.get("revision") or 0)
            if expected != draft.revision:
                return {"ok": False, "status": "revision_conflict", "reason": "draft revision changed"}
            understanding = draft.analysis.get("understanding", {})
            if not isinstance(understanding, dict) or understanding.get("status") != "fallback":
                return {
                    "ok": True, "status": "understanding_not_fallback",
                    "draft": self._draft_view(draft),
                }
            if not self._understanding_retry_allowed(draft):
                return {
                    "ok": False,
                    "status": "understanding_retry_requires_unchanged_draft",
                    "reason": (
                        "A structured Draft field changed after fallback; use evaluation "
                        "to avoid overwriting user edits."
                    ),
                    "draft": self._draft_view(draft),
                }
            raw_ref = next((
                item for item in draft.evidence_refs
                if str(item.get("kind") or "") == "self_issue_raw_report"
            ), None)
            if raw_ref is None:
                raise ValueError("raw Issue report is unavailable")
            raw = hydrate_sidecar_ref(
                self.state_dir,
                raw_ref,
                purpose="self_issue_structure_retry",
                actor="kernel",
                max_bytes=2 * 1024 * 1024,
            ).payload
            description = str(raw.get("description") or "") if isinstance(raw, dict) else ""
            structured = structure_report(
                self.issue_understander,
                description,
                environment=draft.environment,
                project_version=draft.project_version or draft.zaofu_version,
                local_timestamp=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
            )
            if structured.understanding_status != "completed":
                return {
                    "ok": False,
                    "status": "understanding_unavailable",
                    "reason": structured.reason_code,
                    "draft": self._draft_view(draft),
                }
            proposal = dict(structured.proposal)
            analysis = dict(draft.analysis)
            analysis["understanding"] = proposal["understanding"]
            updated = replace(
                draft,
                revision=draft.revision + 1,
                title=proposal["title"],
                summary=proposal["summary"],
                bug_description=proposal["bug_description"],
                reproduction_steps=proposal["reproduction_steps"],
                actual_behavior=proposal["actual_behavior"],
                expected_behavior=proposal["expected_behavior"],
                reported_expected_behavior=proposal["expected_behavior"],
                attachment_context=proposal["attachment_context"],
                additional_context=proposal["additional_context"],
                environment=proposal["environment"],
                project_version=proposal.get("project_version", "") or draft.project_version,
                zaofu_version=proposal.get("project_version", "") or draft.zaofu_version,
                analysis=analysis,
                updated_at=utc_now(),
            )
            updated.validate()
            self.drafts.save(updated)
            event = self.writer.emit(
                "self_issue.understanding.completed",
                actor="kernel",
                causation_id=causation_id or None,
                payload={"draft_id": updated.draft_id, "revision": updated.revision},
            )
            return {
                "ok": True,
                "status": "understanding_completed",
                "draft": self._draft_view(updated),
                "event_id": event.id,
            }

    def dismiss_suggestion(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "suggestion.lock"):
            draft = self._required_draft(payload)
            locked = self._evaluation_lock_response(draft, action="suggestion_dismiss")
            if locked is not None:
                return locked
            projection = self.enrichment.get(draft.draft_id)
            if projection is None:
                raise ValueError("Issue Enrichment projection is unavailable")
            updated, dismissal = self.enrichment.dismiss_suggestion(
                draft_id=draft.draft_id,
                suggestion_id=str(payload.get("suggestion_id") or ""),
                actor="reporter",
                expected_draft_revision=int(payload.get("revision") or 0),
                expected_draft_digest=str(payload.get("draft_digest") or ""),
                dismissed_at=utc_now(),
            )
            event = self.writer.emit(
                "self_issue.suggestion.dismissed",
                actor="user",
                causation_id=causation_id or None,
                payload={
                    "draft_id": draft.draft_id,
                    "suggestion_id": dismissal.suggestion_id,
                    "revision": dismissal.revision,
                    "status": "dismissed",
                },
            )
            return {
                "ok": True,
                "status": "suggestion_dismissed",
                "draft": self._draft_view(draft),
                "dismissal": dismissal.to_dict(),
                "event_id": event.id,
            }

    def resolve_suggestion(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "suggestion.lock"):
            draft = self._required_draft(payload)
            locked = self._evaluation_lock_response(draft, action="suggestion_resolve")
            if locked is not None:
                return locked
            projection = self.enrichment.get(draft.draft_id)
            if projection is None:
                raise ValueError("Issue Enrichment projection is unavailable")
            if (
                int(payload.get("revision") or 0) != draft.revision
                or str(payload.get("draft_digest") or "") != projection.draft_digest
            ):
                return {
                    "ok": False, "status": "suggestion_stale",
                    "reason": "Draft changed before the suggestion was resolved",
                    "draft": self._draft_view(draft),
                }
            suggestion_id = str(payload.get("suggestion_id") or "")
            suggestion = next((
                item for item in projection.suggestions
                if item.suggestion_id == suggestion_id
            ), None)
            if suggestion is None or suggestion.status != "pending":
                return {
                    "ok": False, "status": "suggestion_not_pending",
                    "draft": self._draft_view(draft),
                }
            if (
                suggestion.based_on_revision != draft.revision
                or suggestion.based_on_digest != projection.draft_digest
            ):
                return {
                    "ok": False, "status": "suggestion_stale",
                    "reason": "Suggestion targets an earlier Draft revision",
                    "draft": self._draft_view(draft),
                }
            action = str(payload.get("resolution") or "accepted")
            if action not in {"accepted", "merged"}:
                raise ValueError("suggestion resolution must be accepted or merged")
            field_path = suggestion.field_path
            if not hasattr(draft, field_path) or field_path in {
                "draft_id", "revision", "publication_state", "published_issue_ref",
            }:
                raise ValueError("Suggestion targets an unsupported Draft field")
            value = (
                redact_obj(payload.get("value"))
                if action == "merged" else suggestion.proposed_value
            )
            updated = replace(
                draft,
                **{field_path: value},
                revision=draft.revision + 1,
                publication_state="draft",
                updated_at=utc_now(),
            )
            if field_path == "expected_behavior":
                updated = replace(updated, reported_expected_behavior=str(value or ""))
            updated.validate()
            self.drafts.save(updated)
            current = self.enrichment.get(updated.draft_id)
            if current is None:
                raise ValueError("Issue Enrichment projection is unavailable")
            resolution = Resolution(
                resolution_id=f"resolution-{uuid.uuid4().hex[:12]}",
                suggestion_or_conflict_id=suggestion_id,
                action=action,
                resolved_by="reporter",
                based_on_revision=draft.revision,
                resulting_revision=updated.revision,
                created_at=utc_now(),
            )
            metadata = dict(current.field_metadata)
            metadata[field_path] = {
                "source": "reporter", "user_locked": True, "confidence": "high",
                "evidence_refs": list(suggestion.evidence_refs),
                "last_changed_revision": updated.revision,
            }
            revised_suggestions = tuple(
                replace(item, status=action) if item.suggestion_id == suggestion_id
                else replace(item, status="stale") if item.status == "pending"
                else item
                for item in current.suggestions
            )
            revision = DraftRevision(
                draft_id=updated.draft_id,
                revision=updated.revision,
                parent_revision=updated.revision - 1,
                actor="reporter",
                changed_fields=(field_path,),
                change_reason=f"suggestion_{action}",
                content_digest=current.draft_digest,
                created_at=utc_now(),
                resolution_id=resolution.resolution_id,
            )
            self.enrichment.save(
                replace(
                    current,
                    field_metadata=metadata,
                    suggestions=revised_suggestions,
                    resolutions=(*current.resolutions, resolution),
                    revisions=(*current.revisions, revision),
                ),
                expected_draft_revision=current.draft_revision,
                expected_draft_digest=current.draft_digest,
            )
            event = self.writer.emit(
                "self_issue.suggestion.resolved", actor="user",
                causation_id=causation_id or None,
                payload={
                    "draft_id": updated.draft_id, "suggestion_id": suggestion_id,
                    "resolution_id": resolution.resolution_id, "action": action,
                    "revision": updated.revision,
                },
            )
            return {
                "ok": True, "status": f"suggestion_{action}",
                "draft": self._draft_view(updated), "event_id": event.id,
            }

    def resolve_conflict(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "suggestion.lock"):
            draft = self._required_draft(payload)
            locked = self._evaluation_lock_response(draft, action="conflict_resolve")
            if locked is not None:
                return locked
            projection = self.enrichment.get(draft.draft_id)
            if projection is None:
                raise ValueError("Issue Enrichment projection is unavailable")
            if (
                int(payload.get("revision") or 0) != draft.revision
                or str(payload.get("draft_digest") or "") != projection.draft_digest
            ):
                return {"ok": False, "status": "conflict_stale", "draft": self._draft_view(draft)}
            conflict_id = str(payload.get("conflict_id") or "")
            conflict = next((
                item for item in projection.conflicts if item.conflict_id == conflict_id
            ), None)
            if conflict is None or conflict.status != "unresolved":
                return {"ok": False, "status": "conflict_not_pending", "draft": self._draft_view(draft)}
            action = str(payload.get("resolution") or "needs_reinvestigation")
            if action == "needs_reinvestigation":
                updated_projection = replace(
                    projection,
                    conflicts=tuple(
                        replace(item, status=action) if item.conflict_id == conflict_id else item
                        for item in projection.conflicts
                    ),
                )
                self.enrichment.save(
                    updated_projection,
                    expected_draft_revision=projection.draft_revision,
                    expected_draft_digest=projection.draft_digest,
                )
                self.writer.emit(
                    "self_issue.conflict.reinvestigation_requested", actor="user",
                    causation_id=causation_id or None,
                    payload={"draft_id": draft.draft_id, "conflict_id": conflict_id},
                )
                return {"ok": True, "status": action, "draft": self._draft_view(draft)}
            if action not in {"resolved_left", "resolved_right", "resolved_both"}:
                raise ValueError("unsupported Conflict resolution")
            claim_by_id = {item.claim_id: item for item in projection.claims}
            left = claim_by_id.get(conflict.left_claim_ref)
            right = claim_by_id.get(conflict.right_claim_ref)
            if left is None or right is None:
                raise ValueError("Conflict Claim is unavailable")
            value = {
                "resolved_left": left.value,
                "resolved_right": right.value,
                "resolved_both": payload.get("value"),
            }[action]
            if action == "resolved_both" and value is None:
                value = f"{left.value}\n{right.value}"
            if not hasattr(draft, conflict.field_path):
                raise ValueError("Conflict targets an unsupported Draft field")
            updated = replace(
                draft,
                **{conflict.field_path: redact_obj(value)},
                revision=draft.revision + 1,
                publication_state="draft",
                updated_at=utc_now(),
            )
            updated.validate()
            self.drafts.save(updated)
            current = self.enrichment.get(updated.draft_id)
            if current is None:
                raise ValueError("Issue Enrichment projection is unavailable")
            resolution = Resolution(
                resolution_id=f"resolution-{uuid.uuid4().hex[:12]}",
                suggestion_or_conflict_id=conflict_id,
                action=action,
                resolved_by="reporter",
                based_on_revision=draft.revision,
                resulting_revision=updated.revision,
                created_at=utc_now(),
            )
            metadata = dict(current.field_metadata)
            metadata[conflict.field_path] = {
                "source": "reporter", "user_locked": True, "confidence": "high",
                "evidence_refs": list({*left.dependency_refs, *right.dependency_refs}),
                "last_changed_revision": updated.revision,
            }
            self.enrichment.save(
                replace(
                    current,
                    field_metadata=metadata,
                    conflicts=tuple(
                        replace(item, status=action) if item.conflict_id == conflict_id
                        else item
                        for item in current.conflicts
                    ),
                    resolutions=(*current.resolutions, resolution),
                ),
                expected_draft_revision=current.draft_revision,
                expected_draft_digest=current.draft_digest,
            )
            self.writer.emit(
                "self_issue.conflict.resolved", actor="user",
                causation_id=causation_id or None,
                payload={
                    "draft_id": updated.draft_id, "conflict_id": conflict_id,
                    "resolution_id": resolution.resolution_id, "action": action,
                    "revision": updated.revision,
                },
            )
            return {"ok": True, "status": action, "draft": self._draft_view(updated)}

    def add_draft_attachment(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        """Add a user-disclosed binary/text attachment to a direct-report Draft.

        The public ``/issue`` path bypasses the legacy questionnaire, so it
        needs its own controlled attachment writer.  The bytes are sanitized
        and stored as an immutable sidecar; the Draft only keeps the verified
        descriptor and digest.  Publication still requires the normal
        provider-specific disclosure/preparation gate.
        """
        with locked_path(self.state_dir / "self-issues" / "draft.lock"):
            draft = self._required_draft(payload)
            locked = self._evaluation_lock_response(draft, action="attachment_add")
            if locked is not None:
                return locked
            expected_revision = int(payload.get("revision") or 0)
            if expected_revision and expected_revision != draft.revision:
                return {
                    "ok": False,
                    "status": "revision_conflict",
                    "reason": "draft revision changed",
                    "draft": self._draft_view(draft),
                }
            if draft.publication_state in {
                "publishing", "published", "partially_published", "outcome_unknown",
            }:
                raise ValueError("published Draft is immutable")
            if len(draft.attachment_refs) >= MAX_ATTACHMENTS:
                raise ValueError(f"at most {MAX_ATTACHMENTS} attachments are allowed")
            filename = _safe_filename(payload.get("filename"))
            suffix = Path(filename).suffix.lower()
            expected_type = ALLOWED_ATTACHMENTS.get(suffix)
            supplied_type = str(payload.get("content_type") or "").split(";", 1)[0].lower()
            if not expected_type or supplied_type != expected_type:
                raise ValueError("attachment type is unsupported or does not match its extension")
            try:
                content = base64.b64decode(
                    str(payload.get("content_base64") or ""), validate=True,
                )
            except (ValueError, binascii.Error) as exc:
                raise ValueError("attachment is not valid base64") from exc
            if not content or len(content) > MAX_ATTACHMENT_BYTES:
                raise ValueError("attachment must be non-empty and no larger than 20 MB")
            total = sum(int(item.get("byte_count") or 0) for item in draft.attachment_refs)
            if total + len(content) > MAX_TOTAL_ATTACHMENT_BYTES:
                raise ValueError("attachment total exceeds 100 MB")
            sanitized, redaction_applied = sanitize_attachment_for_disclosure(
                content, suffix=suffix, content_type=expected_type,
            )
            video_confirmed = bool(payload.get("video_disclosure_confirmed"))
            if expected_type.startswith("video/") and not video_confirmed:
                raise ValueError("video attachment requires explicit public-disclosure confirmation")
            attachment_id = f"att-{uuid.uuid4().hex[:12]}"
            descriptor = write_sidecar_bytes(
                self.state_dir,
                Path("artifacts") / "self-issues" / draft.draft_id / "attachments"
                / f"{attachment_id}-{filename}",
                sanitized,
                kind="self_issue_user_attachment",
                schema_version="self-issue-attachment.v1",
                created_by=str(draft.reporter_context.get("reported_by") or "user"),
                source_event_id=causation_id,
                access_scope={"external_disclosure": False},
                retention={"class": "user_controlled"},
                required=False,
                content_type=expected_type,
                preview=filename,
            )
            descriptor.update({
                "attachment_id": attachment_id,
                "filename": filename,
                "source": "user",
                "description": str(payload.get("description") or f"User-provided attachment ({filename}).")[:500],
                "purpose": str(payload.get("purpose") or "Provide user-supplied context for Issue assessment.")[:500],
                "redaction_applied": redaction_applied,
                "video_disclosure_confirmed": video_confirmed,
            })
            updated = replace(
                draft,
                revision=draft.revision + 1,
                attachment_refs=[*draft.attachment_refs, descriptor],
                publication_state="draft",
                updated_at=utc_now(),
            )
            updated.validate()
            self.intents.invalidate_unpublished(
                draft.draft_id, reason="Draft attachment changed",
            )
            self.batches.invalidate_unpublished(
                draft.draft_id, reason="Draft attachment changed",
            )
            self.attachments.invalidate_unprepared(
                draft.draft_id, reason="Draft attachment changed",
            )
            self.drafts.save(updated)
            event = self.writer.emit(
                "self_issue.draft.attachment_added", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "draft_id": updated.draft_id,
                    "revision": updated.revision,
                    "attachment_id": attachment_id,
                    "sha256": descriptor["sha256"],
                    "byte_count": descriptor["byte_count"],
                    "content_type": expected_type,
                },
            )
            return {
                "ok": True,
                "status": "draft_attachment_added",
                "draft": self._draft_view(updated),
                "event_id": event.id,
            }

    def remove_draft_attachment(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        """Remove one un-published Draft attachment through the kernel."""
        with locked_path(self.state_dir / "self-issues" / "draft.lock"):
            draft = self._required_draft(payload)
            locked = self._evaluation_lock_response(draft, action="attachment_remove")
            if locked is not None:
                return locked
            expected_revision = int(payload.get("revision") or 0)
            if expected_revision and expected_revision != draft.revision:
                return {
                    "ok": False,
                    "status": "revision_conflict",
                    "reason": "draft revision changed",
                    "draft": self._draft_view(draft),
                }
            if draft.publication_state in {
                "publishing", "published", "partially_published", "outcome_unknown",
            }:
                raise ValueError("published Draft is immutable")
            attachment_id = str(payload.get("attachment_id") or "").strip()
            target = next((
                item for item in draft.attachment_refs
                if str(item.get("attachment_id") or "") == attachment_id
            ), None)
            if target is None:
                raise ValueError("attachment not found")
            self._safe_attachment_path(target).unlink(missing_ok=True)
            updated = replace(
                draft,
                revision=draft.revision + 1,
                attachment_refs=[item for item in draft.attachment_refs if item is not target],
                publication_state="draft",
                updated_at=utc_now(),
            )
            updated.validate()
            self.intents.invalidate_unpublished(
                draft.draft_id, reason="Draft attachment changed",
            )
            self.batches.invalidate_unpublished(
                draft.draft_id, reason="Draft attachment changed",
            )
            self.attachments.invalidate_unprepared(
                draft.draft_id, reason="Draft attachment changed",
            )
            self.drafts.save(updated)
            event = self.writer.emit(
                "self_issue.draft.attachment_removed", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "draft_id": updated.draft_id,
                    "revision": updated.revision,
                    "attachment_id": attachment_id,
                },
            )
            return {
                "ok": True,
                "status": "draft_attachment_removed",
                "draft": self._draft_view(updated),
                "event_id": event.id,
            }

    def _safe_attachment_path(self, descriptor: dict[str, Any]) -> Path:
        relative = Path(str(descriptor.get("ref") or ""))
        root = self.state_dir.resolve()
        candidate = (root / relative).resolve()
        if relative.is_absolute() or not candidate.is_relative_to(root):
            raise ValueError("attachment ref leaves the configured state dir")
        return candidate

    def get(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        draft_id = str(payload.get("draft_id") or "").strip()
        draft = self.drafts.get(draft_id) if draft_id else self.drafts.latest()
        intake = self.intakes.latest()
        if draft is None:
            return {
                "ok": True,
                "status": "intake_collecting" if intake else "draft_absent",
                "draft": None,
                "intake": self._intake_view(intake) if intake else None,
            }
        return {
            "ok": True,
            "status": "draft_ready",
            "draft": self._draft_view(draft),
            "intake": self._intake_view(intake) if intake else None,
        }

    def dismiss(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        draft_id = str(payload.get("draft_id") or "").strip()
        draft = self.drafts.get(draft_id)
        if draft is None:
            return {"ok": True, "status": "draft_absent", "draft_id": draft_id}
        permanent = bool(payload.get("permanent"))
        if permanent and str(payload.get("confirmation") or "") != draft.draft_id:
            raise ValueError("permanent deletion requires the exact Draft id as confirmation")
        locked = self._evaluation_lock_response(draft, action="draft_dismiss")
        if locked is not None:
            return locked
        if self.intents.locked_for_draft(draft.draft_id):
            raise ValueError("cannot dismiss a Draft while publication outcome recovery is locked")
        invalidated = self.intents.invalidate_unpublished(
            draft.draft_id, reason="draft dismissed by user",
        )
        running = draft.assessment_status == "running"
        run_id = draft.evidence_run_id
        recover_until = ""
        trash_root = self.state_dir / "artifacts" / "self-issues-trash" / draft.draft_id
        artifact_root = self.state_dir / "artifacts" / "self-issues" / draft.draft_id
        projection = self.enrichment.get(draft.draft_id)
        if not permanent:
            recover_until = (
                datetime.now(timezone.utc) + timedelta(days=7)
            ).isoformat()
            write_sidecar_json(
                self.state_dir,
                f"artifacts/self-issues-trash/{draft.draft_id}/recovery.json",
                {
                    "schema_version": "self-issue-recovery.v1",
                    "draft": draft.to_dict(),
                    "enrichment": projection.to_dict() if projection is not None else None,
                    "recover_until": recover_until,
                },
                kind="self_issue_recovery",
                schema_version="self-issue-recovery.v1",
                created_by="kernel",
                retention={"class": "recoverable", "expires_at": recover_until},
                access_scope={"external_disclosure": False},
                required=True,
                preview="Recoverable deleted Draft",
            )
            if artifact_root.is_dir():
                trash_root.mkdir(parents=True, exist_ok=True)
                shutil.move(str(artifact_root), str(trash_root / "original-artifacts"))
        self.drafts.delete(draft.draft_id)
        self.enrichment.delete(draft.draft_id)
        self.intents.delete_for_draft(draft.draft_id)
        self.batches.delete_for_draft(draft.draft_id)
        self.attachments.delete_for_draft(draft.draft_id)
        if artifact_root.is_dir():
            shutil.rmtree(artifact_root)
        activity_path = (
            self.state_dir / "self-issues" / "evidence-activity" / f"{draft.draft_id}.json"
        )
        activity_path.unlink(missing_ok=True)
        event = self.writer.emit(
            "self_issue.draft.dismissed",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "revision": draft.revision,
                "invalidated_intent_ids": invalidated,
                "recoverable": not permanent,
                "recover_until": recover_until,
            },
        )
        return {
            "ok": True,
            "status": "draft_dismissed",
            "draft_id": draft.draft_id,
            "event_id": event.id,
            "recoverable": not permanent,
            "recover_until": recover_until,
            "cancel_evidence": running,
            "run_id": (
                run_id if running else ""
            ),
            "thread_id": (
                f"self-issue-assessment:{draft.draft_id}:{run_id}"
                if running else ""
            ),
        }

    def restore_dismissed(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        draft_id = str(payload.get("draft_id") or "").strip()
        recovery_path = (
            self.state_dir / "artifacts" / "self-issues-trash" / draft_id / "recovery.json"
        )
        if not recovery_path.is_file():
            return {"ok": False, "status": "recovery_not_found", "draft_id": draft_id}
        value = json.loads(recovery_path.read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(str(value.get("recover_until") or ""))
        if datetime.now(timezone.utc) >= expires:
            return {"ok": False, "status": "recovery_expired", "draft_id": draft_id}
        draft = IssueDraft.from_dict(dict(value["draft"]))
        self.drafts.save(draft)
        enrichment = value.get("enrichment")
        if isinstance(enrichment, dict):
            self.enrichment.save(EnrichmentProjection.from_dict(enrichment))
        trash_root = recovery_path.parent
        original = trash_root / "original-artifacts"
        target = self.state_dir / "artifacts" / "self-issues" / draft_id
        if original.is_dir() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(original), str(target))
        shutil.rmtree(trash_root)
        event = self.writer.emit(
            "self_issue.draft.restored", actor="kernel",
            causation_id=causation_id or None,
            payload={"draft_id": draft_id, "revision": draft.revision},
        )
        return {
            "ok": True, "status": "draft_restored",
            "draft": self._draft_view(draft), "event_id": event.id,
        }

    def privacy_erase(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        draft = self._required_draft(payload)
        if str(payload.get("confirmation") or "") != draft.draft_id:
            raise ValueError("privacy erasure requires the exact Draft id as confirmation")
        active = self._active_evaluation_run(draft.draft_id)
        if active is not None:
            self._finish_evaluation_run(
                draft.draft_id, run_id=active.run_id, status="cancelled",
                reason="privacy_erasure", causation_id=causation_id,
            )
        remote_ref = dict(draft.published_issue_ref)
        self.drafts.delete(draft.draft_id)
        self.enrichment.delete(draft.draft_id)
        self.intents.delete_for_draft(draft.draft_id)
        self.batches.delete_for_draft(draft.draft_id)
        self.attachments.delete_for_draft(draft.draft_id)
        for root in (
            self.state_dir / "artifacts" / "self-issues" / draft.draft_id,
            self.state_dir / "artifacts" / "self-issues-trash" / draft.draft_id,
        ):
            if root.is_dir():
                shutil.rmtree(root)
        event = self.writer.emit(
            "self_issue.privacy_erased", actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "status": "purged",
                "remote_cleanup_required": bool(remote_ref),
            },
        )
        return {
            "ok": True, "status": "privacy_erased", "draft_id": draft.draft_id,
            "remote_cleanup_required": bool(remote_ref),
            "remote_issue_ref": remote_ref,
            "event_id": event.id,
        }

    def _draft_view(self, draft: IssueDraft) -> dict[str, Any]:
        value = draft.to_dict()
        enrichment = self.enrichment.get(draft.draft_id)
        if enrichment is not None:
            enrichment_view = enrichment.to_dict()
            for run in enrichment_view["evaluation_runs"]:
                run.pop("commit_capability", None)
            enrichment_view["evaluation_locked"] = any(
                run.get("status") == "running"
                for run in enrichment_view["evaluation_runs"]
            )
        else:
            enrichment_view = {
                "schema_version": "issue-enrichment-projection.v1",
                "draft_id": draft.draft_id,
                "draft_revision": draft.revision,
                "draft_digest": self.enrichment.draft_digest(draft),
                "migration_status": "legacy_v2_unmigrated",
                "field_metadata": {},
                "revisions": [],
                "claims": [],
                "evidences": [],
                "gaps": [],
                "suggestions": [],
                "conflicts": [],
                "resolutions": [],
                "evaluation_runs": [],
                "dismissals": [],
                "evaluation_locked": False,
            }
        value["enrichment"] = enrichment_view
        value["understanding_retry_allowed"] = self._understanding_retry_allowed(draft)
        value["runtime_status"] = self_issue_runtime_status(self.state_dir)
        value["evidence_activity"] = read_evidence_activity(
            self.state_dir, draft.draft_id,
        )
        latest_attachment = max(
            self.attachments.for_draft(draft.draft_id),
            key=lambda item: item.updated_at,
            default=None,
        )
        value["attachment_preparation"] = (
            {
                "preparation_id": latest_attachment.preparation_id,
                "status": latest_attachment.status,
                "manifest_digest": latest_attachment.manifest_digest,
                "confirmation_id": latest_attachment.confirmation_id,
                "failure_reason": latest_attachment.failure_reason,
            }
            if latest_attachment else None
        )
        targets = self._configured_targets()
        if self.policy.enabled and self.policy.target_locked and targets:
            configured = targets.get(str(draft.target_binding.get("provider") or ""))
            primary = configured or targets.get("gitlab") or next(iter(targets.values()))
            value["target_binding"] = dict(primary)
        value["target_policy"] = {
            "locked": bool(self.policy.enabled and self.policy.target_locked),
            "targets": self._configured_target_policy(),
            "allowed_modes": [
                mode for mode in ("gitlab", "github", "both")
                if self._mode_supported(mode)
            ],
            "default_mode": self.policy.default_publication_mode,
        }
        batch = self.batches.latest_for_draft(draft.draft_id)
        if batch is not None:
            batch_view = self._batch_response(batch)
            value["publication_batch"] = batch_view
            value.update({
                "batch_id": batch.batch_id,
                "publication_mode": batch.publication_mode,
                "intent_ids": dict(batch.intent_ids),
                "payload_digest": batch.payload_digest,
                "previews": dict(batch_view.get("previews") or {}),
                "providers": dict(batch_view.get("providers") or {}),
                "issues": dict(batch_view.get("issues") or {}),
                "confirmation_id": batch.confirmation_id,
            })
            value["published_issue_refs"] = self._published_refs(batch)
        intent = self._latest_publication_intent(draft.draft_id)
        if intent is not None and (
            intent.draft_revision == draft.revision or intent.status == "published"
        ):
            value["intent_id"] = intent.intent_id
            value["payload_digest"] = intent.payload_digest
            value["preview"] = dict(intent.payload)
        return value

    def _latest_publication_intent(
        self, draft_id: str, *, status: str = "",
    ) -> PublicationIntent | None:
        candidates = [
            item for item in self.intents.for_draft(draft_id)
            if not status or item.status == status
        ]
        return max(candidates, key=lambda item: item.updated_at, default=None)

    def _target_binding(self, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            raise ValueError("target_binding must be provider-neutral")
        if not set(value) <= {"provider", "project"}:
            raise ValueError("target_binding must be provider-neutral")
        supplied = {
            str(key): str(item).strip()
            for key, item in value.items()
        }
        if not (self.policy.enabled and self.policy.target_locked):
            return supplied
        targets = self._configured_targets()
        required = targets.get("gitlab") or next(iter(targets.values()), {
            "provider": self.policy.provider,
            "project": self.policy.target_project,
        })
        if not supplied:
            return required
        selected = targets.get(supplied.get("provider", ""))
        if selected is not None and supplied.get("project") == selected.get("project"):
            return dict(selected)
        for key, expected in required.items():
            actual = supplied.get(key, "")
            if actual and actual != expected:
                raise ValueError(
                    f"Self-Issue target is locked to {required['provider']}:"
                    f"{required['project']}"
                )
        return required

    def _validate_policy_target(self, value: object) -> None:
        normalized = self._target_binding(value)
        if self.policy.enabled and self.policy.target_locked and normalized != value:
            raise ValueError(
                f"Self-Issue target is locked to {self.policy.provider}:"
                f"{self.policy.target_project}"
            )

    def _validate_publication_target(
        self, draft: IssueDraft, intent: PublicationIntent,
    ) -> None:
        provider = str(intent.target_binding.get("provider") or "")
        if intent.target_binding != self._target_for_provider(provider, draft=draft):
            raise ValueError("publication target no longer matches the locked policy")

    def _configured_targets(self) -> dict[str, dict[str, str]]:
        if self.policy.targets:
            return {
                name: {"provider": name, "project": target.project}
                for name, target in self.policy.targets.items()
            }
        if self.policy.target_project:
            return {
                self.policy.provider: {
                    "provider": self.policy.provider,
                    "project": self.policy.target_project,
                },
            }
        return {}

    def _configured_target_policy(self) -> dict[str, dict[str, str]]:
        targets = self._configured_targets()
        projected: dict[str, dict[str, str]] = {}
        for name, binding in targets.items():
            target = self.policy.targets.get(name)
            projected[name] = {
                **binding,
                "authorization_domain": str(
                    getattr(target, "authorization_domain", "") or f"{name}.com"
                ),
                "auth_mode": str(getattr(target, "auth_mode", "") or ""),
                "oauth_scope": str(getattr(target, "oauth_scope", "") or ""),
            }
        return projected

    def _target_for_provider(
        self, provider: str, *, draft: IssueDraft | None = None,
    ) -> dict[str, str]:
        target = self._configured_targets().get(provider)
        if (
            draft is not None
            and not (self.policy.enabled and self.policy.target_locked)
            and str(draft.target_binding.get("provider") or "") == provider
        ):
            target = dict(draft.target_binding)
        if target is None or not target.get("project"):
            raise ValueError(f"Self-Issue provider target is not configured: {provider}")
        return dict(target)

    def _target_config(self, provider: str) -> Any:
        target = self.policy.targets.get(provider)
        if target is not None:
            return target
        if provider == self.policy.provider:
            return self.policy
        raise ValueError(f"Self-Issue provider target is not configured: {provider}")

    def _publication_selection(
        self, payload: dict[str, Any], *, draft: IssueDraft | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        mode = str(
            payload.get("publication_mode") or self.policy.default_publication_mode or "gitlab"
        ).strip().lower()
        providers = {
            "gitlab": ("gitlab",),
            "github": ("github",),
            "both": ("gitlab", "github"),
        }.get(mode)
        configured = set(self._configured_targets())
        if (
            not configured
            and draft is not None
            and not (self.policy.enabled and self.policy.target_locked)
        ):
            configured.add(str(draft.target_binding.get("provider") or ""))
        if providers is None or not all(name in configured for name in providers):
            raise ValueError("publication_mode is not enabled by the locked Self-Issue policy")
        return mode, providers

    def _mode_supported(self, mode: str) -> bool:
        required = {
            "gitlab": {"gitlab"}, "github": {"github"}, "both": {"gitlab", "github"},
        }[mode]
        return required <= set(self._configured_targets())

    def _forge_for(self, provider: str) -> ForgeProvider:
        forge = self.forges.get(provider)
        if forge is None:
            raise ValueError(f"Forge provider is not configured: {provider}")
        return forge

    def _permission_scope(self, provider: str) -> str:
        if provider == "gitlab":
            return "api"
        target = self._target_config("github")
        if str(getattr(target, "auth_mode", "") or "device_flow") == "oauth_device_flow":
            return str(getattr(target, "oauth_scope", "") or "public_repo")
        return "issues:write"

    def _permission_snapshot(self, provider: str) -> dict[str, str]:
        target = self._target_config(provider)
        return {
            "scope": self._permission_scope(provider),
            "provider": provider,
            "authorization_domain": str(
                getattr(target, "authorization_domain", "") or f"{provider}.com"
            ),
            "auth_mode": str(getattr(target, "auth_mode", "") or ""),
        }

    def _github_credential_assessment(
        self, draft: IssueDraft, secret: dict[str, str],
    ) -> GitHubCredentialAssessment:
        target = self._target_config("github")
        require_media = any(
            str(item.get("content_type") or "").lower().startswith(("image/", "video/"))
            for item in draft.attachment_refs
            if isinstance(item, dict)
        )
        return assess_github_credential(
            secret,
            auth_mode=str(getattr(target, "auth_mode", "") or "device_flow"),
            required_scope=self._permission_scope("github"),
            require_media=require_media,
        )

    def _github_publication_preflight(
        self, project: str, secret: dict[str, str],
    ) -> ForgeResult:
        forge = self._forge_for("github")
        preflight_method = getattr(forge, "check_issue_publication_access", None)
        if preflight_method is None:
            return ForgeResult(
                status="publish_failed",
                reason="github_publication_preflight_unavailable",
            )
        return preflight_method(
            project,
            access_token=str(secret["access_token"]),
        )

    def _publication_lock(self, name: str):
        return locked_path(self.state_dir / "self-issues" / name)

    @property
    def _publication_ready_evidence_statuses(self) -> frozenset[str]:
        return PUBLICATION_READY_EVIDENCE_STATUSES

    @property
    def _confirmation_ttl(self) -> timedelta:
        return CONFIRMATION_TTL

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return _parse_time(value)

    def _collect_local_evidence(
        self,
        draft_id: str,
        *,
        revision: int,
        run_id: str,
        reporter_context: dict[str, Any],
        runtime_status: str,
    ) -> dict[str, Any]:
        recent_events = self.writer.event_log.read_all()[-100:]
        relevant_events = [
            event for event in recent_events
            if any(token in event.type.lower() for token in (
                "fail", "error", "reject", "timeout", "diagnostic", "trace",
            ))
        ][-20:]
        event_refs = [
            {
                "event_id": event.id,
                "type": event.type,
                "ts": event.ts,
                "task_id": event.task_id or "",
                "trace_ref": event.correlation_id or "",
            }
            for event in relevant_events
        ]
        code_locations: set[str] = set()
        for event in relevant_events:
            payload_text = json.dumps(redact_obj(event.payload), ensure_ascii=False, default=str)
            for match in _CODE_LOCATION_RE.finditer(payload_text):
                relative = match.group("path")
                candidate = (self.project_root / relative).resolve()
                if candidate.is_relative_to(self.project_root.resolve()) and candidate.is_file():
                    line = match.group("line") or ""
                    code_locations.add(f"{relative}:{line}" if line else relative)
        log_evidence = collect_log_evidence(self.state_dir)
        capture_hint = reporter_context.get("browser_capture")
        capture_requested = (
            isinstance(capture_hint, dict) and bool(capture_hint.get("requested"))
        )
        browser_result = BrowserCaptureResult(
            "deferred" if runtime_status == "live" and capture_requested else "not_available",
            (
                "Browser capture is deferred until the Orchestrator classifies a safe Web/UI target."
                if runtime_status == "live" and capture_requested
                else "Dynamic Playwright capture was not attempted because the project runtime is stopped."
            ),
        )
        screenshot_refs = (
            [dict(browser_result.screenshot_ref)]
            if browser_result.screenshot_ref is not None else []
        )
        captured_paths = {str(item.get("ref") or "") for item in screenshot_refs}
        artifact_root = self.state_dir / "artifacts"
        if artifact_root.is_dir():
            for path in sorted(artifact_root.rglob("*"), reverse=True):
                if (
                    len(screenshot_refs) >= 20
                    or not path.is_file()
                    or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}
                    or path.stat().st_size > 20 * 1024 * 1024
                ):
                    continue
                relative = str(path.relative_to(self.state_dir))
                if relative in captured_paths:
                    continue
                screenshot_refs.append({
                    "ref": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "byte_count": path.stat().st_size,
                    "capture_source": (
                        "playwright" if "playwright" in relative.lower()
                        or "test-results" in relative.lower() else "local_artifact"
                    ),
                })
        evidence = {
            "git": {"head": self._git_head(), "branch": self._git("branch", "--show-current"),
                    "dirty_paths": self._git("status", "--short").splitlines()[:100]},
            "config": {"present": (self.project_root / "zf.yaml").is_file()},
            "event_log": {
                "present": (self.state_dir / "events.jsonl").is_file(),
                "recent_failure_refs": event_refs,
            },
            **log_evidence,
            "screenshot_refs": screenshot_refs,
            "browser_capture": {
                "status": browser_result.status,
                "reason": browser_result.reason,
                "capture_kind": (
                    str(browser_result.screenshot_ref.get("capture_kind") or "")
                    if browser_result.screenshot_ref else ""
                ),
            },
            "web_api_timing": summarize_web_api_timing(
                self.state_dir / "logs" / "web-api-timing.jsonl",
            ),
            "code_locations": sorted(code_locations)[:50],
            "collection_mode": "read_only",
            "runtime_status": runtime_status,
        }
        return write_sidecar_json(
            self.state_dir,
            f"artifacts/self-issues/{draft_id}/evidence-r{revision}.json",
            evidence,
            kind="self_issue_evidence", schema_version="self-issue-evidence.v1",
            created_by="kernel", access_scope={"external_disclosure": False}, required=True,
            preview="Local-only redacted evidence summary",
        )

    def _capture_assessed_browser_evidence(
        self,
        *,
        draft: IssueDraft,
        report: dict[str, Any],
        mechanical_evidence: dict[str, Any],
    ) -> tuple[dict[str, Any], BrowserCaptureResult]:
        """Run one passive capture only after semantic Web/UI target approval."""

        from zf.runtime.issue_settings import current_issue_policy
        capture_policy = current_issue_policy(self.project_root, self.policy)
        evidence = dict(mechanical_evidence)
        hint = draft.reporter_context.get("browser_capture")
        hint = hint if isinstance(hint, dict) else {}
        component = str(report.get("component") or "").lower()
        web_target_approved = (
            report.get("classification") == "web/ui"
            or any(token in component for token in ("web", "ui", "kanban", "browser"))
        )
        if draft.runtime_status != "live":
            result = BrowserCaptureResult(
                "not_available",
                "Dynamic Playwright capture was not attempted because the project runtime is stopped.",
            )
        elif not bool(hint.get("requested")):
            result = BrowserCaptureResult(
                "not_requested", "The incident reporter did not request a browser capture.",
            )
        elif not web_target_approved:
            result = BrowserCaptureResult(
                "not_requested",
                "The Orchestrator did not classify this incident as a safe Web/UI capture target.",
            )
        else:
            result = capture_self_issue_browser_evidence(
                state_dir=self.state_dir,
                draft_id=draft.draft_id,
                run_id=draft.evidence_run_id,
                reporter_context=draft.reporter_context,
                enabled=bool(capture_policy.browser_capture_enabled),
                denied_urls=capture_policy.browser_capture_denied_urls,
                configured_base_url=str(self.policy.browser_capture_base_url or ""),
            )
        evidence["browser_capture"] = {
            "status": result.status,
            "reason": result.reason,
        }
        if result.screenshot_ref is not None:
            refs = list(evidence.get("screenshot_refs") or [])
            refs.append(dict(result.screenshot_ref))
            evidence["screenshot_refs"] = refs
        return evidence, result

    def _publication_payload(
        self, draft: IssueDraft, *, marker: str, provider: str = "gitlab",
    ) -> tuple[dict[str, Any], str, str]:
        fields = set(draft.disclosure_fields)
        if not fields <= DISCLOSURE_ALLOWLIST:
            raise ValueError("disclosure contains unknown or non-exportable fields")
        # Kernel-local sidecar refs never enter the provider payload. Evidence
        # selected for external disclosure must first become a confirmed provider
        # upload. GitHub has no supported Issue attachment upload API, so its
        # immutable payload contains an explicit omission notice instead.
        fields.discard("evidence_refs")
        public_analysis = {
            key: draft.analysis.get(key)
            for key in (
                "external_issue_disposition",
                "investigation_gaps",
                "downstream_verification_tasks",
                "suggested_fix_directions",
                "root_cause_status",
                "risks",
                "evidence_summary",
                "claims",
                "hypotheses",
                "conflicts",
                "version_context",
                "assessment_note",
                "bounded_fix_target",
                "acceptance",
                "verification_summary",
                "scope", "suggested_components", "severity_status", "classification_status",
                "attachment_warnings", "root_cause", "test_assessment", "executed_reproduction",
                "assessment_confidence_reason", "classification_reason", "severity_reason",
            )
            if draft.analysis.get(key) not in (None, "", [], {}, "Not provided.")
        }
        values = {
            "bug_description": draft.bug_description,
            "reproduction_steps": draft.reproduction_steps,
            "actual_behavior": draft.actual_behavior,
            "expected_behavior": draft.expected_behavior,
            "environment": draft.environment,
            "project_version": draft.project_version or draft.zaofu_version,
            "zaofu_version": draft.zaofu_version,
            "source_code_revision": draft.source_code_revision or self._git_head(),
            "additional_context": draft.additional_context,
            "attachment_context": draft.attachment_context,
            "classification": draft.classification,
            "severity": draft.severity,
            "reproduction_status": draft.reproduction_status,
            "component": draft.component,
            "impact_scope": draft.impact_scope,
            "assessment_confidence": draft.assessment_confidence,
            "analysis": public_analysis,
            "recommended_next_action": draft.recommended_next_action,
            "evidence_collection_status": draft.evidence_status,
            "published_attachments": [{
                "filename": str(item.get("filename") or "attachment"),
                "markdown": str(item.get("markdown") or ""),
                "url": str(item.get("url") or ""),
                "sha256": str(item.get("sha256") or ""),
                "content_type": str(item.get("content_type") or ""),
                "kind": str(item.get("kind") or "self_issue_user_attachment"),
                "source": str(item.get("source") or ("user" if str(item.get("kind") or "").startswith("self_issue_user_") else "agent")),
                "capture_source": str(item.get("capture_source") or "user"),
                "description": str(item.get("description") or ""),
                "purpose": str(item.get("purpose") or ""),
            } for item in draft.published_attachments],
            "runtime_status": draft.runtime_status,
            "assessment_status": draft.assessment_status,
            "evidence_collection_mode": draft.evidence_collection_mode,
            "evidence_limit_reason": draft.evidence_limit_reason,
        }
        # The questionnaire's ``zaofu_version`` is the human-readable package
        # version.  The exact source commit is captured when the intake is
        # promoted, so publishing later cannot silently bind the Issue to a
        # different checkout.  Legacy drafts fall back to the current head.
        source_code_revision = draft.source_code_revision or self._git_head()
        if source_code_revision and source_code_revision != "unknown":
            values["source_code_revision"] = source_code_revision
        fields.update({
            "assessment_confidence", "evidence_collection_status", "impact_scope",
            "runtime_status", "assessment_status", "evidence_collection_mode",
            "evidence_limit_reason",
        })
        if "source_code_revision" in values:
            fields.add("source_code_revision")
        if draft.reporter_context.get("source") == "portable_issue_session":
            # The typed legacy store defaults are not semantic findings.
            # Only explicit report facts may be presented as such.
            facts = draft.analysis.get("report_facts")
            if isinstance(facts, dict):
                for key in ("severity", "reproduction_status", "assessment_confidence", "evidence_collection_mode"):
                    # Keep the field visible without inventing an assessment.
                    if key == "severity" and str(facts.get(key) or "").lower() in {"", "needs_assessment"}:
                        values[key] = "待评估（本轮报告未提供，不使用默认 P2）"
                    else:
                        values[key] = facts.get(key) or ""
                if draft.classification == "unknown":
                    values["classification"] = "needs_triage (not classified)"
                values["source_code_revision"] = draft.source_code_revision
        disclosed = {key: values[key] for key in sorted(fields)}
        if provider == "github":
            disclosed.pop("published_attachments", None)
            if draft.attachment_refs:
                disclosed["binary_attachments_omitted"] = True
        redacted = _safe_export_obj(redact_obj(disclosed))
        body = render_publication_markdown(redacted, marker=marker)
        if draft.markdown_body is not None:
            body = str(redact_obj(draft.markdown_body))
            redacted = {**redacted, "markdown_body": body, "title": draft.title}
            if marker:
                body = body.rstrip() + f"\n\n<!-- zf-self-issue:{marker} -->\n"
        payload = {
            "title": _safe_report_text(draft.title),
            "body": body,
            "labels": ([draft.classification] if draft.classification != "unknown" else []) + [draft.severity.lower()],
        }
        if isinstance(draft.analysis.get("report_facts"), dict):
            reported_severity = draft.analysis["report_facts"].get("severity")
            if reported_severity not in {"P0", "P1", "P2", "P3"}:
                payload["labels"] = [draft.classification] if draft.classification != "unknown" else []
        return payload, stable_digest(redacted), stable_digest(sorted(fields))

    def _required_draft(self, payload: dict[str, Any]) -> IssueDraft:
        draft = self.drafts.get(str(payload.get("draft_id") or ""))
        if draft is None:
            raise ValueError("draft not found")
        return draft

    def _understanding_retry_allowed(self, draft: IssueDraft) -> bool:
        understanding = draft.analysis.get("understanding", {})
        if not isinstance(understanding, dict) or understanding.get("status") != "fallback":
            return False
        if draft.revision == 1:
            return True
        projection = self.enrichment.get(draft.draft_id)
        if projection is None or not projection.field_metadata:
            return False
        return not any(
            bool(projection.field_metadata.get(field_path, {}).get("user_locked"))
            for field_path in STRUCTURE_RETRY_FIELDS
        )

    def _seed_field_metadata(
        self, draft: IssueDraft, *, source: str, user_locked: bool,
    ) -> None:
        projection = self.enrichment.get(draft.draft_id)
        if projection is None:
            projection = self.enrichment.sync_draft(draft)
        metadata = dict(projection.field_metadata)
        for field_path in (
            "title", "summary", "bug_description", "reproduction_steps",
            "actual_behavior", "expected_behavior", "reported_expected_behavior",
            "environment", "project_version", "additional_context",
            "attachment_context", "target_binding",
        ):
            field_locked = user_locked and field_path != "actual_behavior"
            metadata[field_path] = {
                "source": source,
                "user_locked": field_locked,
                "confidence": "high" if field_locked else "medium",
                "evidence_refs": [],
                "last_changed_revision": draft.revision,
            }
        self.enrichment.save(
            replace(projection, field_metadata=metadata),
            expected_draft_revision=projection.draft_revision,
            expected_draft_digest=projection.draft_digest,
        )

    def _lock_user_fields(
        self, draft: IssueDraft, *, changed_fields: tuple[str, ...],
    ) -> None:
        projection = self.enrichment.get(draft.draft_id)
        if projection is None:
            projection = self.enrichment.sync_draft(draft)
        metadata = dict(projection.field_metadata)
        governed = set(metadata) | {
            "title", "summary", "bug_description", "reproduction_steps",
            "actual_behavior", "expected_behavior", "reported_expected_behavior",
            "environment", "project_version", "additional_context",
            "attachment_context", "target_binding",
        }
        locked_fields = set(changed_fields) & governed
        if "expected_behavior" in locked_fields:
            locked_fields.add("reported_expected_behavior")
        for field_path in locked_fields:
            previous = dict(metadata.get(field_path) or {})
            metadata[field_path] = {
                "source": "reporter",
                "user_locked": True,
                "confidence": "high",
                "evidence_refs": list(previous.get("evidence_refs") or ()),
                "last_changed_revision": draft.revision,
            }
        revision = DraftRevision(
            draft_id=draft.draft_id,
            revision=draft.revision,
            parent_revision=draft.revision - 1,
            actor="reporter",
            changed_fields=tuple(sorted(locked_fields or set(changed_fields))),
            change_reason="user_edit",
            content_digest=projection.draft_digest,
            created_at=utc_now(),
        )
        claims = tuple(
            replace(
                claim,
                verification_status="contradicted",
                invalidated_at_revision=draft.revision,
            )
            if claim.field_path in locked_fields
            and claim.invalidated_at_revision is None
            else claim
            for claim in projection.claims
        )
        suggestions = tuple(
            replace(suggestion, status="stale")
            if suggestion.status == "pending"
            and (
                suggestion.field_path in locked_fields
                or suggestion.based_on_digest != projection.draft_digest
            )
            else suggestion
            for suggestion in projection.suggestions
        )
        conflicts = tuple(
            replace(conflict, status="needs_reinvestigation")
            if conflict.status == "unresolved" and conflict.field_path in locked_fields
            else conflict
            for conflict in projection.conflicts
        )
        self.enrichment.save(
            replace(
                projection,
                field_metadata=metadata,
                revisions=(*projection.revisions, revision),
                claims=claims,
                suggestions=suggestions,
                conflicts=conflicts,
            ),
            expected_draft_revision=projection.draft_revision,
            expected_draft_digest=projection.draft_digest,
        )

    def _active_evaluation_run(self, draft_id: str) -> EvaluationRun | None:
        projection = self.enrichment.get(draft_id)
        if projection is None:
            return None
        return next((
            run for run in reversed(projection.evaluation_runs)
            if run.status == "running"
        ), None)

    def _evaluation_lock_response(
        self, draft: IssueDraft, *, action: str,
    ) -> dict[str, Any] | None:
        run = self._active_evaluation_run(draft.draft_id)
        if run is None:
            return None
        return {
            "ok": False,
            "status": "evaluation_locked",
            "reason": f"Draft is read-only while evaluation {run.run_id} is active",
            "blocked_action": action,
            "evaluation": {
                "run_id": run.run_id,
                "draft_revision": run.draft_revision,
                "draft_digest": run.draft_digest,
                "status": run.status,
                "started_at": run.started_at,
            },
            "draft": self._draft_view(draft),
        }

    def _start_evaluation_run(self, draft: IssueDraft, *, run_id: str) -> EvaluationRun:
        projection = self.enrichment.get(draft.draft_id)
        if projection is None:
            projection = self.enrichment.sync_draft(draft)
        active = self._active_evaluation_run(draft.draft_id)
        if active is not None:
            if active.run_id == run_id:
                return active
            raise ValueError("another Evaluation Run already locks this Draft")
        run = EvaluationRun.start(
            run_id=run_id,
            draft_id=draft.draft_id,
            draft_revision=draft.revision,
            draft_digest=projection.draft_digest,
            commit_capability=secrets.token_urlsafe(32),
            started_at=utc_now(),
        )
        self.enrichment.save(
            replace(projection, evaluation_runs=(*projection.evaluation_runs, run)),
            expected_draft_revision=projection.draft_revision,
            expected_draft_digest=projection.draft_digest,
        )
        self.writer.emit(
            "self_issue.evaluation.requested",
            actor="kernel",
            payload={
                "draft_id": draft.draft_id,
                "run_id": run_id,
                "revision": draft.revision,
                "draft_digest": projection.draft_digest,
            },
        )
        return run

    def _finish_evaluation_run(
        self,
        draft_id: str,
        *,
        run_id: str,
        status: str,
        reason: str = "",
        causation_id: str = "",
    ) -> EvaluationRun | None:
        projection = self.enrichment.get(draft_id)
        if projection is None:
            return None
        target = next((
            run for run in projection.evaluation_runs if run.run_id == run_id
        ), None)
        if target is None or target.status != "running":
            return target
        finished = target.finish(status=status, completed_at=utc_now(), reason=reason)
        self.enrichment.save(
            replace(
                projection,
                evaluation_runs=tuple(
                    finished if run.run_id == run_id else run
                    for run in projection.evaluation_runs
                ),
            ),
            expected_draft_revision=projection.draft_revision,
            expected_draft_digest=projection.draft_digest,
        )
        self.writer.emit(
            f"self_issue.evaluation.{status}",
            actor="kernel",
            causation_id=causation_id or None,
            payload={"draft_id": draft_id, "run_id": run_id, "status": status},
        )
        return finished

    def _required_intent(self, payload: dict[str, Any]) -> PublicationIntent:
        intent = self.intents.get(str(payload.get("intent_id") or ""))
        if intent is None:
            raise ValueError("publication intent not found")
        return intent

    def _secret_key(
        self, payload: dict[str, Any], draft: IssueDraft, *, provider: str = "",
    ) -> SecretKey:
        provider = provider or str(draft.target_binding.get("provider") or "gitlab")
        target = self._target_config(provider)
        requested_domain = str(payload.get("authorization_domain") or "").strip()
        configured_domain = str(
            getattr(target, "authorization_domain", "")
            or ("gitlab.com" if provider == "gitlab" else "github.com")
        )
        if (
            self.policy.enabled
            and self.policy.target_locked
            and requested_domain
            and requested_domain != configured_domain
        ):
            raise ValueError("Self-Issue authorization domain is fixed by zf.yaml")
        return SecretKey(
            user_id=str(payload.get("user_id") or "local-user"),
            workspace_id=str(
                payload.get("workspace_id")
                or forge_credential_workspace_id(self.project_root)
            ),
            provider=provider,
            authorization_domain=(
                configured_domain
                if self.policy.enabled and self.policy.target_locked
                else requested_domain or configured_domain
            ),
        )

    def _provider_secret(
        self, key: SecretKey, *, causation_id: str,
    ) -> dict[str, str] | None:
        with locked_path(forge_credential_store_path(self.state_dir).with_name("forge-refresh.lock")):
            secret = self.secrets.reveal(key)
            if not secret or not self._token_expired(secret):
                return secret
            refresh_token = str(secret.get("refresh_token") or "")
            client_id = str(secret.get("client_id") or "")
            redirect_uri = str(secret.get("redirect_uri") or "")
            if not refresh_token or not client_id:
                return None
            try:
                if key.provider == "github":
                    refreshed = self.github_oauth.refresh(
                        refresh_token=refresh_token,
                        client_id=client_id,
                    )
                else:
                    if not redirect_uri:
                        return None
                    refreshed = self.oauth.refresh(
                        refresh_token=refresh_token,
                        client_id=client_id,
                        redirect_uri=redirect_uri,
                    )
            except (OSError, ValueError):
                return None
            if not str(refreshed.get("refresh_token") or ""):
                return None
            refreshed["client_id"] = client_id
            if redirect_uri:
                refreshed["redirect_uri"] = redirect_uri
            if not str(refreshed.get("scope") or ""):
                refreshed["scope"] = str(secret.get("scope") or "")
            for metadata_key in ("auth_mode", "token_kind", "token_source"):
                if not str(refreshed.get(metadata_key) or "") and str(
                    secret.get(metadata_key) or ""
                ):
                    refreshed[metadata_key] = str(secret[metadata_key])
            self.secrets.put(key, refreshed)
            self.writer.emit(
                "self_issue.oauth.refreshed", actor="kernel",
                causation_id=causation_id or None,
                payload={"credential_subject": key.subject,
                         "authorization_domain": key.authorization_domain},
            )
            return refreshed

    @staticmethod
    def _token_expired(secret: dict[str, str]) -> bool:
        expires_at = str(secret.get("expires_at") or "")
        if not expires_at:
            return False
        try:
            expiry = _parse_time(expires_at)
        except ValueError:
            return True
        return datetime.now(timezone.utc) + TOKEN_REFRESH_LEEWAY >= expiry

    def _git_head(self) -> str:
        return self._git("rev-parse", "HEAD") or "unknown"

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.project_root, capture_output=True, text=True,
            check=False, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _severity_rank(value: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(value, 9)


_CODE_LOCATION_RE = re.compile(
    r"(?P<path>(?:src|tests|web)/[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx))"
    r"(?::(?P<line>[1-9][0-9]*))?"
)
