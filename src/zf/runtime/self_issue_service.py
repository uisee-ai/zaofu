"""Deterministic Self-Issue kernel service."""

from __future__ import annotations

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
from zf.core.state.locks import locked_path
from zf.integrations.forge.base import ForgeProvider, ForgeResult, IssuePublishRequest
from zf.integrations.forge.gitlab import GitLabComProvider
from zf.integrations.forge.github import GitHubComProvider, GitHubDeviceOAuthClient
from zf.integrations.forge.oauth import GitLabOAuthClient
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.self_issue_attachments import SelfIssueAttachmentMixin
from zf.runtime.self_issue_browser_evidence import (
    BrowserCaptureResult,
    capture_self_issue_browser_evidence,
)
from zf.runtime.self_issue_evidence_activity import read_evidence_activity
from zf.runtime.self_issue_evidence_run import SelfIssueEvidenceRunMixin
from zf.runtime.self_issue_intake import SelfIssueIntakeMixin
from zf.runtime.self_issue_evidence import summarize_web_api_timing
from zf.runtime.self_issue_log_evidence import collect_log_evidence
from zf.runtime.self_issue_liveness import self_issue_runtime_status
from zf.runtime.self_issue_github_oauth import SelfIssueGitHubOAuthMixin
from zf.runtime.self_issue_oauth import SelfIssueOAuthMixin
from zf.runtime.self_issue_publication import SelfIssuePublicationMixin
from zf.runtime.self_issue_target import resolve_self_issue_target_root


DISCLOSURE_ALLOWLIST = frozenset({
    "bug_description", "reproduction_steps", "expected_behavior", "environment",
    "zaofu_version", "additional_context", "attachment_context", "classification", "severity",
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
    ) -> None:
        self.state_dir = Path(state_dir)
        self.policy = policy or SelfIssueConfig()
        self.project_root = resolve_self_issue_target_root(project_root, self.policy)
        self.writer = writer
        self.drafts = IssueDraftStore(self.state_dir / "self-issues" / "drafts.json")
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

    def capture(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        return self.start_intake(payload, causation_id=causation_id)

    def update(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        with locked_path(self.state_dir / "self-issues" / "update.lock"):
            draft = self._required_draft(payload)
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
                "environment", "zaofu_version", "additional_context", "component",
                "attachment_context",
                "impact_scope", "assessment_confidence", "recommended_next_action",
            }
            updates = {key: redact_obj(payload[key]) for key in allowed if key in payload}
            updates["target_binding"] = self._target_binding(
                updates.get("target_binding", draft.target_binding),
            )
            if all(getattr(draft, key) == value for key, value in updates.items()):
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
            event = self.writer.emit(
                "self_issue.draft.updated", actor="kernel", causation_id=causation_id or None,
                payload={"draft_id": updated.draft_id, "revision": updated.revision},
            )
            return {"ok": True, "status": "draft_updated", "draft": self._draft_view(updated), "event_id": event.id}

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
        if self.intents.locked_for_draft(draft.draft_id):
            raise ValueError("cannot dismiss a Draft while publication outcome recovery is locked")
        invalidated = self.intents.invalidate_unpublished(
            draft.draft_id, reason="draft dismissed by user",
        )
        running = draft.assessment_status == "running"
        run_id = draft.evidence_run_id
        self.drafts.delete(draft.draft_id)
        self.intents.delete_for_draft(draft.draft_id)
        self.batches.delete_for_draft(draft.draft_id)
        self.attachments.delete_for_draft(draft.draft_id)
        artifact_root = self.state_dir / "artifacts" / "self-issues" / draft.draft_id
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
            },
        )
        return {
            "ok": True,
            "status": "draft_dismissed",
            "draft_id": draft.draft_id,
            "event_id": event.id,
            "cancel_evidence": running,
            "run_id": (
                run_id if running else ""
            ),
            "thread_id": (
                f"self-issue-assessment:{draft.draft_id}:{run_id}"
                if running else ""
            ),
        }

    def _draft_view(self, draft: IssueDraft) -> dict[str, Any]:
        value = draft.to_dict()
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
            primary = targets.get("gitlab") or next(iter(targets.values()))
            value["target_binding"] = dict(primary)
        value["target_policy"] = {
            "locked": bool(self.policy.enabled and self.policy.target_locked),
            "targets": {name: dict(target) for name, target in targets.items()},
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

    def _target_for_provider(
        self, provider: str, *, draft: IssueDraft | None = None,
    ) -> dict[str, str]:
        target = self._configured_targets().get(provider)
        if (
            target is None
            and draft is not None
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

    @staticmethod
    def _permission_scope(provider: str) -> str:
        return "api" if provider == "gitlab" else "issues:write"

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
                enabled=bool(self.policy.browser_capture_enabled),
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
        values = {
            "bug_description": draft.bug_description,
            "reproduction_steps": draft.reproduction_steps,
            "expected_behavior": draft.expected_behavior,
            "environment": draft.environment,
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
            "analysis": draft.analysis,
            "recommended_next_action": draft.recommended_next_action,
            "evidence_collection_status": draft.evidence_status,
            "published_attachments": [{
                "filename": str(item.get("filename") or "attachment"),
                "markdown": str(item.get("markdown") or ""),
                "url": str(item.get("url") or ""),
                "sha256": str(item.get("sha256") or ""),
                "content_type": str(item.get("content_type") or ""),
                "kind": str(item.get("kind") or "self_issue_user_attachment"),
                "capture_source": str(item.get("capture_source") or "user"),
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
        disclosed = {key: values[key] for key in sorted(fields)}
        if provider == "github":
            disclosed.pop("published_attachments", None)
            if draft.attachment_refs:
                disclosed["binary_attachments_omitted"] = True
        redacted = _safe_export_obj(redact_obj(disclosed))
        body = render_publication_markdown(redacted, marker=marker)
        payload = {
            "title": _safe_report_text(draft.title),
            "body": body,
            "labels": [draft.classification, draft.severity.lower()],
        }
        return payload, stable_digest(redacted), stable_digest(sorted(fields))

    def _required_draft(self, payload: dict[str, Any]) -> IssueDraft:
        draft = self.drafts.get(str(payload.get("draft_id") or ""))
        if draft is None:
            raise ValueError("draft not found")
        return draft

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
