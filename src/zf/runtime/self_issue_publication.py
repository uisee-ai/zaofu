"""Kernel-owned multi-provider Self-Issue publication coordination."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from issue_core.enrichment import stable_content_digest
from issue_core.publication import HandoffBundle, HandoffItem, PublicationSnapshot
from zf.core.self_issue.models import (
    PublicationBatch,
    PublicationIntent,
    stable_digest,
    utc_now,
)
from zf.integrations.forge.base import ForgeResult, IssuePublishRequest
from zf.core.self_issue.safe_export import safe_report_text
from zf.runtime.sidecar_refs import write_sidecar_json

_MAX_GITHUB_ATTACHMENT_BYTES = 20 * 1024 * 1024
_MAX_GITHUB_COMMENT_BODY_CHARS = 60_000
_MAX_GITHUB_TEXT_EVIDENCE_CHARS = 48_000
_MAX_GITHUB_TEXT_READ_CHARS = 120_000
_GITHUB_TEXT_SUFFIXES = frozenset({
    ".csv", ".json", ".log", ".md", ".txt", ".xml", ".yaml", ".yml",
})


class SelfIssuePublicationMixin:
    """One immutable batch with independently recoverable provider intents."""

    def preview(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        with self._publication_lock("preview.lock"):
            draft = self._required_draft(payload)
            locked = self._evaluation_lock_response(draft, action="publication_preview")
            if locked is not None:
                return locked
            if draft.evidence_status not in self._publication_ready_evidence_statuses:
                return {
                    "ok": False,
                    "status": f"evidence_{draft.evidence_status}",
                    "reason": "evidence assessment must settle before publication preview",
                    "draft": self._draft_view(draft),
                }
            if draft.publication_state == "published":
                published_batch = self.batches.latest_for_draft(draft.draft_id)
                if published_batch is not None and published_batch.status == "published":
                    return self._batch_response(published_batch)
            mode, providers = self._publication_selection(payload, draft=draft)
            if (
                "gitlab" in providers
                and draft.attachment_refs
                and len(draft.published_attachments) != len(draft.attachment_refs)
            ):
                return {
                    "ok": False,
                    "status": "attachment_preparation_required",
                    "reason": "confirmed GitLab attachment preparation is required first",
                    "draft": self._draft_view(draft),
                    "publication_mode": mode,
                }
            locked = self.intents.locked_for_draft(draft.draft_id)
            if locked is not None:
                return {
                    "ok": False,
                    "status": "outcome_unknown",
                    "provider": str(locked.target_binding.get("provider") or ""),
                    "intent_id": locked.intent_id,
                    "reason": "publication recovery is locked",
                }
            intent_ids: dict[str, str] = {}
            previews: dict[str, dict[str, Any]] = {}
            child_digests: dict[str, str] = {}
            for provider in providers:
                target = self._target_for_provider(provider, draft=draft)
                key = self._secret_key(payload, draft, provider=provider)
                permission_snapshot = self._permission_snapshot(provider)
                marker_seed = stable_digest({
                    "draft_id": draft.draft_id,
                    "draft_revision": draft.revision,
                    "provider": provider,
                    "target": target,
                    "title": draft.title,
                    "disclosure_fields": draft.disclosure_fields,
                })
                marker = hashlib.sha256(marker_seed.encode()).hexdigest()[:24]
                publication_payload, redaction_digest, disclosure_digest = (
                    self._publication_payload(draft, marker=marker, provider=provider)
                )
                payload_digest = stable_digest(publication_payload)
                reusable = next((
                    candidate
                    for candidate in reversed(self.intents.for_draft(draft.draft_id))
                    if candidate.draft_revision == draft.revision
                    and candidate.target_binding == target
                    and candidate.payload_digest == payload_digest
                    and candidate.permission_snapshot == permission_snapshot
                    and candidate.status in {"previewed", "confirmed"}
                    and (candidate.status != "confirmed" or (
                        candidate.confirmation_expires_at
                        and self._parse_time(candidate.confirmation_expires_at) > datetime.now(timezone.utc)
                    ))
                ), None)
                if reusable is None:
                    projection = self.enrichment.get(draft.draft_id)
                    understanding = draft.analysis.get("understanding", {})
                    understanding_status = (
                        str(understanding.get("status") or "unknown")
                        if isinstance(understanding, dict) else "unknown"
                    )
                    selected_claim_refs = tuple(
                        item.claim_id for item in (projection.claims if projection else ())
                        if item.verification_status == "verified"
                        and item.invalidated_at_revision is None
                    )
                    selected_evidence_refs = tuple(
                        item.evidence_id for item in (projection.evidences if projection else ())
                        if item.integrity == "verified" and item.applicability == "current"
                    )
                    snapshot = PublicationSnapshot(
                        draft_id=draft.draft_id,
                        draft_revision=draft.revision,
                        provider=provider,
                        payload_digest=payload_digest,
                        draft_digest=(
                            projection.draft_digest if projection is not None
                            else self.enrichment.draft_digest(draft)
                        ),
                        attachment_manifest_digest=stable_content_digest([
                            {
                                "sha256": str(item.get("sha256") or ""),
                                "filename": str(item.get("filename") or ""),
                            }
                            for item in draft.attachment_refs
                        ]),
                        target_project=str(target.get("project") or ""),
                        understanding_status=understanding_status,
                        assessment_status=draft.assessment_status,
                        unresolved_publication_policy="handoff_private",
                        limited_report_acknowledged=(
                            draft.evidence_collection_mode == "limited"
                        ),
                        selected_claim_refs=selected_claim_refs,
                        selected_evidence_refs=selected_evidence_refs,
                    )
                    snapshot_ref = write_sidecar_json(
                        self.state_dir,
                        f"artifacts/self-issues/{draft.draft_id}/publication-snapshot-{provider}-{draft.revision}.json",
                        snapshot.to_dict(),
                        kind="self_issue_publication_snapshot",
                        schema_version="publication-snapshot.v1",
                        created_by="kernel",
                        access_scope={"external_disclosure": False},
                        required=True,
                        preview="Private immutable publication snapshot",
                    )
                    reusable = PublicationIntent(
                        intent_id=f"pub-{uuid.uuid4().hex[:12]}",
                        draft_id=draft.draft_id,
                        draft_revision=draft.revision,
                        payload=publication_payload,
                        payload_digest=payload_digest,
                        redaction_digest=redaction_digest,
                        disclosure_digest=disclosure_digest,
                        target_binding=target,
                        credential_subject=key.subject,
                        permission_snapshot=permission_snapshot,
                        marker=marker,
                        publication_snapshot_ref=snapshot_ref,
                    )
                    self.intents.save(reusable)
                intent_ids[provider] = reusable.intent_id
                previews[provider] = dict(reusable.payload)
                child_digests[provider] = reusable.payload_digest

            digest = (
                next(iter(child_digests.values()))
                if len(child_digests) == 1 else stable_digest(child_digests)
            )
            reusable_batch = next((
                item for item in reversed(self.batches.for_draft(draft.draft_id))
                if item.draft_revision == draft.revision
                and item.publication_mode == mode
                and item.intent_ids == intent_ids
                and item.payload_digest == digest
                and item.status in {"previewed", "confirmed"}
            ), None)
            batch = reusable_batch or PublicationBatch(
                batch_id=f"pubb-{uuid.uuid4().hex[:12]}",
                draft_id=draft.draft_id,
                draft_revision=draft.revision,
                publication_mode=mode,
                selected_providers=list(providers),
                intent_ids=intent_ids,
                payload_digest=digest,
            )
            for intent_id in batch.intent_ids.values():
                intent = self.intents.get(intent_id)
                if intent is not None and intent.batch_id != batch.batch_id:
                    intent.batch_id = batch.batch_id
                    intent.updated_at = utc_now()
                    self.intents.save(intent)
            self.batches.save(batch)
            draft.publication_state = batch.status
            draft.updated_at = utc_now()
            self.drafts.save(draft)
            if reusable_batch is None:
                self.writer.emit(
                    "self_issue.publication.previewed",
                    actor="kernel",
                    causation_id=causation_id or None,
                    payload={
                        "draft_id": draft.draft_id,
                        "batch_id": batch.batch_id,
                        "intent_ids": dict(batch.intent_ids),
                        "providers": list(batch.selected_providers),
                        "payload_digest": batch.payload_digest,
                    },
                )
            return self._batch_response(batch, previews=previews)

    def confirm(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        batch = self._required_batch(payload)
        draft = self._required_draft({"draft_id": batch.draft_id})
        locked = self._evaluation_lock_response(draft, action="publication_confirm")
        if locked is not None:
            return locked
        intents = self._batch_intents(batch)
        if (
            batch.status != "previewed"
            or draft.revision != batch.draft_revision
            or any(intent.status != "previewed" for intent in intents)
        ):
            return {"ok": False, "status": "stale_preview", "reason": "preview no longer matches draft"}
        if str(payload.get("payload_digest") or "") != batch.payload_digest:
            return {"ok": False, "status": "confirmation_mismatch", "reason": "payload digest mismatch"}
        confirmation_id = secrets.token_urlsafe(24)
        expires = datetime.now(timezone.utc) + self._confirmation_ttl
        for intent in intents:
            self._validate_publication_target(draft, intent)
            intent.status = "confirmed"
            intent.confirmation_id = confirmation_id
            intent.confirmation_expires_at = expires.isoformat()
            intent.confirmed_at = utc_now()
            intent.updated_at = utc_now()
            self.intents.save(intent)
        batch.status = "confirmed"
        batch.confirmation_id = confirmation_id
        batch.confirmation_expires_at = expires.isoformat()
        batch.confirmed_at = utc_now()
        batch.updated_at = utc_now()
        self.batches.save(batch)
        draft.publication_state = "confirmed"
        draft.updated_at = utc_now()
        self.drafts.save(draft)
        self.writer.emit(
            "self_issue.publication.confirmed",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "batch_id": batch.batch_id,
                "intent_ids": dict(batch.intent_ids),
                "payload_digest": batch.payload_digest,
                "expires_at": batch.confirmation_expires_at,
            },
        )
        return {
            "ok": True,
            "status": "confirmed",
            "batch_id": batch.batch_id,
            "intent_id": intents[0].intent_id if len(intents) == 1 else "",
            "confirmation_id": confirmation_id,
            "expires_at": batch.confirmation_expires_at,
        }

    def publish(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        batch = self._required_batch(payload)
        draft = self._required_draft({"draft_id": batch.draft_id})
        locked = self._evaluation_lock_response(draft, action="publish")
        if locked is not None:
            return locked
        if batch.status == "published":
            return self._batch_response(batch)
        if batch.status == "outcome_unknown":
            return {
                "ok": False,
                "status": "outcome_unknown",
                "batch_id": batch.batch_id,
                "providers": self._provider_statuses(batch),
            }
        if batch.status == "invalidated":
            return {
                "ok": False,
                "status": "stale_confirmation",
                "reason": batch.failure_reason or "publication snapshot changed",
            }
        # The current UI's Publish button is the owner's explicit confirmation
        # of the exact preview.  Keep the old confirm endpoint for API/CLI
        # compatibility, but allow that single controlled action to mint the
        # one-time confirmation before publishing.
        if batch.status == "previewed" and bool(payload.get("implicit_publish")):
            confirmed = self.confirm({
                "batch_id": batch.batch_id,
                "payload_digest": batch.payload_digest,
            }, causation_id=causation_id)
            if not confirmed.get("ok"):
                return confirmed
            batch = self._required_batch({"batch_id": batch.batch_id})
            draft = self._required_draft({"draft_id": batch.draft_id})
        confirmation_id = str(payload.get("confirmation_id") or batch.confirmation_id or "")
        if batch.status != "confirmed" or confirmation_id != batch.confirmation_id:
            return {"ok": False, "status": "confirmation_required", "reason": "valid one-time confirmation required"}
        if datetime.now(timezone.utc) >= self._parse_time(batch.confirmation_expires_at):
            return {"ok": False, "status": "confirmation_expired", "reason": "confirmation expired"}

        provider_secrets: dict[str, tuple[Any, dict[str, str]]] = {}
        for intent in self._batch_intents(batch):
            provider = str(intent.target_binding.get("provider") or "")
            try:
                self._validate_publication_snapshot(draft, intent)
            except ValueError:
                return {
                    "ok": False,
                    "status": "stale_confirmation",
                    "reason": "permission snapshot changed",
                }
            key = self._secret_key(payload, draft, provider=provider)
            if key.subject != intent.credential_subject:
                return {"ok": False, "status": "stale_confirmation", "reason": "credential subject changed"}
            secret = self._provider_secret(key, causation_id=causation_id)
            if not secret:
                return {
                    "ok": False,
                    "status": "authorization_required",
                    "provider": provider,
                    "authorization_domain": key.authorization_domain,
                    "draft_id": draft.draft_id,
                    "batch_id": batch.batch_id,
                    "confirmation_id": batch.confirmation_id,
                }
            if provider == "github":
                assessment = self._github_credential_assessment(draft, secret)
                if not assessment.compatible:
                    return {
                        "ok": False,
                        "status": "authorization_required",
                        "provider": "github",
                        "reason": assessment.reason,
                        "draft_id": draft.draft_id,
                        "batch_id": batch.batch_id,
                        "confirmation_id": batch.confirmation_id,
                    }
                preflight = self._github_publication_preflight(
                    str(intent.target_binding.get("project") or ""),
                    secret,
                )
                if preflight.status != "ready":
                    return {
                        "ok": False,
                        "status": (
                            "authorization_required"
                            if preflight.status == "authorization_required"
                            else "failed_before_remote_write"
                        ),
                        "provider": "github",
                        "reason": preflight.reason,
                    }
            elif str(secret.get("scope") or self._permission_scope(provider)) != self._permission_scope(provider):
                return {
                    "ok": False,
                    "status": "stale_confirmation",
                    "reason": "permission snapshot changed",
                }
            provider_secrets[provider] = (key, secret)

        batch.status = "publishing"
        batch.confirmation_id = ""
        batch.updated_at = utc_now()
        self.batches.save(batch)
        for intent in self._batch_intents(batch):
            provider = str(intent.target_binding.get("provider") or "")
            key, secret = provider_secrets[provider]
            self._publish_intent(
                draft=draft,
                intent=intent,
                confirmation_id=confirmation_id,
                secret=secret,
                secret_key=key,
                causation_id=causation_id,
            )
        return self._finish_batch(batch, draft=draft)

    def recover(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        intent = self._required_intent(payload)
        if intent.status == "partially_published":
            # The Issue was created, but a follow-up metadata-label write did
            # not settle.  Reconcile labels against the existing Issue
            # instead of replaying publish (which could create a duplicate).
            draft = self._required_draft({"draft_id": intent.draft_id})
            provider_name = str(intent.target_binding.get("provider") or "")
            provider = self._forge_for(provider_name)
            reconcile = getattr(provider, "reconcile_issue_labels", None)
            if not callable(reconcile):
                return {
                    "ok": False,
                    "status": "partially_published",
                    "provider": provider_name,
                    "issue": intent.published_issue_ref,
                    "reason": "provider cannot reconcile metadata labels",
                }
            key = self._secret_key(payload, draft, provider=provider_name)
            if key.subject != intent.credential_subject:
                return {
                    "ok": False,
                    "status": "stale_confirmation",
                    "reason": "credential subject changed",
                }
            secret = self._provider_secret(key, causation_id=causation_id)
            if not secret:
                return {
                    "ok": False,
                    "status": "authorization_required",
                    "provider": provider_name,
                }
            if provider_name == "github":
                assessment = self._github_credential_assessment(draft, secret)
                if not assessment.compatible:
                    return {
                        "ok": False,
                        "status": "authorization_required",
                        "provider": "github",
                        "reason": assessment.reason,
                    }
                preflight = self._github_publication_preflight(
                    str(intent.target_binding.get("project") or ""), secret,
                )
                if preflight.status != "ready":
                    return {
                        "ok": False,
                        "status": (
                            "authorization_required"
                            if preflight.status == "authorization_required"
                            else "failed_before_remote_write"
                        ),
                        "provider": "github",
                        "reason": preflight.reason,
                    }
            try:
                issue_number = int(str(intent.published_issue_ref.get("number") or "0"))
            except (TypeError, ValueError):
                issue_number = 0
            result = reconcile(
                str(intent.target_binding.get("project") or ""),
                issue_number,
                tuple(intent.payload.get("labels") or ()),
                access_token=str(secret["access_token"]),
            )
            if result.status != "reconciled":
                intent.failure_reason = result.reason
                intent.updated_at = utc_now()
                self.intents.save(intent)
                batch = self.batches.get(intent.batch_id) if intent.batch_id else None
                return self._finish_batch(batch, draft=draft) if batch else {
                    "ok": False,
                    "status": "partially_published",
                    "provider": provider_name,
                    "issue": intent.published_issue_ref,
                    "reason": result.reason,
                }
            intent.status = "published"
            intent.failure_reason = ""
            if (
                provider_name == "github"
                and intent.evidence_comment_status in {"publish_failed", "outcome_unknown"}
            ):
                self._publish_github_evidence_comment(
                    draft=draft,
                    intent=intent,
                    provider=provider,
                    access_token=str(secret["access_token"]),
                    causation_id=causation_id,
                )
            intent.updated_at = utc_now()
            self.intents.save(intent)
            self.writer.emit(
                "self_issue.published",
                actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "draft_id": draft.draft_id,
                    "intent_id": intent.intent_id,
                    "provider": provider_name,
                    "issue_ref": intent.published_issue_ref,
                    "recovered": True,
                    "labels_reconciled": True,
                },
            )
            batch = self.batches.get(intent.batch_id) if intent.batch_id else None
            return self._finish_batch(batch, draft=draft) if batch else {
                "ok": True,
                "status": "published",
                "issue": intent.published_issue_ref,
            }
        if (
            intent.status == "published"
            and str(intent.target_binding.get("provider") or "") == "github"
            and intent.evidence_comment_status in {"publish_failed", "outcome_unknown"}
        ):
            draft = self._required_draft({"draft_id": intent.draft_id})
            key = self._secret_key(payload, draft, provider="github")
            secret = self._provider_secret(key, causation_id=causation_id)
            if not secret:
                return {"ok": False, "status": "authorization_required", "provider": "github"}
            assessment = self._github_credential_assessment(draft, secret)
            if not assessment.compatible:
                return {
                    "ok": False,
                    "status": "authorization_required",
                    "provider": "github",
                    "reason": assessment.reason,
                }
            preflight = self._github_publication_preflight(
                str(intent.target_binding.get("project") or ""), secret,
            )
            if preflight.status != "ready":
                return {
                    "ok": False,
                    "status": (
                        "authorization_required"
                        if preflight.status == "authorization_required"
                        else "failed_before_remote_write"
                    ),
                    "provider": "github",
                    "reason": preflight.reason,
                }
            self._publish_github_evidence_comment(
                draft=draft,
                intent=intent,
                provider=self._forge_for("github"),
                access_token=str(secret["access_token"]),
                causation_id=causation_id,
            )
            intent.updated_at = utc_now()
            self.intents.save(intent)
            batch = self.batches.get(intent.batch_id) if intent.batch_id else None
            return self._finish_batch(batch, draft=draft) if batch else {
                "ok": intent.evidence_comment_status == "published",
                "status": intent.evidence_comment_status,
                "issue": intent.published_issue_ref,
            }
        if intent.status not in {"publishing", "outcome_unknown"}:
            return {"ok": False, "status": "not_recoverable", "reason": "intent is not outcome_unknown"}
        draft = self._required_draft({"draft_id": intent.draft_id})
        self._validate_publication_target(draft, intent)
        if intent.status == "publishing":
            intent.status = "outcome_unknown"
            intent.updated_at = utc_now()
            self.intents.save(intent)
        provider = str(intent.target_binding.get("provider") or "")
        key = self._secret_key(payload, draft, provider=provider)
        if key.subject != intent.credential_subject:
            return {"ok": False, "status": "stale_confirmation", "reason": "credential subject changed"}
        secret = self._provider_secret(key, causation_id=causation_id)
        if not secret:
            return {"ok": False, "status": "authorization_required", "provider": provider}
        if provider == "github":
            assessment = self._github_credential_assessment(draft, secret)
            if not assessment.compatible:
                return {
                    "ok": False,
                    "status": "authorization_required",
                    "provider": "github",
                    "reason": assessment.reason,
                }
        try:
            matches = self._forge_for(provider).find_by_marker(
                str(intent.target_binding.get("project") or ""),
                intent.marker,
                access_token=str(secret["access_token"]),
            )
        except Exception:
            matches = []
        if len(matches) == 1:
            intent.status = "published"
            intent.published_issue_ref = matches[0].__dict__
            if provider == "github":
                self._publish_github_evidence_comment(
                    draft=draft,
                    intent=intent,
                    provider=self._forge_for(provider),
                    access_token=str(secret["access_token"]),
                    causation_id=causation_id,
                )
            intent.updated_at = utc_now()
            self.intents.save(intent)
            self.writer.emit(
                "self_issue.published",
                actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "draft_id": draft.draft_id,
                    "intent_id": intent.intent_id,
                    "provider": provider,
                    "issue_ref": intent.published_issue_ref,
                    "recovered": True,
                },
            )
            batch = self.batches.get(intent.batch_id) if intent.batch_id else None
            return self._finish_batch(batch, draft=draft) if batch else {
                "ok": True, "status": "published", "issue": intent.published_issue_ref,
            }
        return {
            "ok": False,
            "status": "outcome_unknown",
            "provider": provider,
            "intent_id": intent.intent_id,
            "matches": len(matches),
        }

    def resolve_unknown(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        intent = self._required_intent(payload)
        if intent.status not in {"publishing", "outcome_unknown"}:
            return {"ok": False, "status": "not_recoverable"}
        evidence_refs = [str(item) for item in payload.get("evidence_refs") or [] if str(item)]
        decision = str(payload.get("decision") or "")
        if not evidence_refs or decision not in {"published", "not_published"}:
            raise ValueError("manual outcome decision and evidence_refs are required")
        draft = self._required_draft({"draft_id": intent.draft_id})
        intent.outcome_evidence_refs = evidence_refs
        if decision == "published":
            issue_ref = dict(payload.get("issue_ref") or {})
            if not str(issue_ref.get("url") or ""):
                raise ValueError("published manual outcome requires issue_ref.url")
            intent.status = "published"
            intent.published_issue_ref = {str(key): str(value) for key, value in issue_ref.items()}
        else:
            intent.status = "publish_failed"
            intent.failure_reason = "manual_not_published"
        intent.updated_at = utc_now()
        self.intents.save(intent)
        event = self.writer.emit(
            "self_issue.publication.resolved",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "intent_id": intent.intent_id,
                "decision": decision,
                "evidence_refs": evidence_refs,
                "issue_ref": intent.published_issue_ref,
            },
        )
        batch = self.batches.get(intent.batch_id) if intent.batch_id else None
        response = self._finish_batch(batch, draft=draft) if batch else {
            "ok": True, "status": intent.status, "issue": intent.published_issue_ref,
        }
        response["event_id"] = event.id
        return response

    def _create_handoff_bundles(self, batch: PublicationBatch, *, draft: Any) -> None:
        projection = self.enrichment.get(draft.draft_id)
        if projection is None:
            return
        items = [
            HandoffItem(
                item_id=item.suggestion_id,
                item_type="suggestion",
                field_path=item.field_path,
                status=item.status,
                claim_refs=item.claim_refs,
                evidence_refs=item.evidence_refs,
            )
            for item in projection.suggestions
            if item.status in {"pending", "stale"}
        ]
        items.extend(
            HandoffItem(
                item_id=item.conflict_id,
                item_type="conflict",
                field_path=item.field_path,
                status="pending" if item.status == "unresolved" else "stale",
                claim_refs=(item.left_claim_ref, item.right_claim_ref),
            )
            for item in projection.conflicts
            if item.status in {"unresolved", "needs_reinvestigation"}
        )
        items.extend(
            HandoffItem(
                item_id=item.gap_id,
                item_type="gap",
                field_path=item.field_path,
                status="pending",
            )
            for item in projection.gaps
        )
        for intent in self._batch_intents(batch):
            if intent.status != "published" or intent.handoff_ref:
                continue
            snapshot_ref = intent.publication_snapshot_ref
            if not snapshot_ref:
                continue
            relative = (
                f"artifacts/self-issues/{draft.draft_id}/handoff-"
                f"{intent.intent_id}.json"
            )
            issue = intent.published_issue_ref
            bundle = HandoffBundle(
                handoff_id=f"handoff-{uuid.uuid4().hex[:12]}",
                provider=str(intent.target_binding.get("provider") or ""),
                repository=str(intent.target_binding.get("project") or ""),
                remote_issue_id=str(issue.get("number") or issue.get("url") or ""),
                publication_snapshot_digest=f"sha256:{snapshot_ref['sha256']}",
                source_revision=draft.source_code_revision or "unknown",
                unresolved_items=tuple(items),
                artifact_ref=relative,
            )
            intent.handoff_ref = write_sidecar_json(
                self.state_dir,
                relative,
                bundle.to_dict(),
                kind="self_issue_handoff_bundle",
                schema_version="handoff-bundle.v1",
                created_by="kernel",
                access_scope={"external_disclosure": False},
                required=True,
                preview="Private Developer handoff bundle",
            )
            intent.updated_at = utc_now()
            self.intents.save(intent)
            self.writer.emit(
                "self_issue.handoff.created", actor="kernel",
                payload={
                    "draft_id": draft.draft_id,
                    "intent_id": intent.intent_id,
                    "handoff_id": bundle.handoff_id,
                    "handoff_ref": intent.handoff_ref["ref"],
                },
            )

    def _publish_intent(
        self,
        *,
        draft: Any,
        intent: PublicationIntent,
        confirmation_id: str,
        secret: dict[str, str],
        secret_key: Any,
        causation_id: str,
    ) -> None:
        claimed_intent, claimed = self.intents.claim_publish(intent.intent_id, confirmation_id)
        if not claimed or claimed_intent is None:
            return
        intent = claimed_intent
        provider_name = str(intent.target_binding.get("provider") or "")
        provider = self._forge_for(provider_name)
        try:
            result = provider.publish(IssuePublishRequest(
                project=str(intent.target_binding.get("project") or ""),
                title=str(intent.payload["title"]),
                body=str(intent.payload["body"]),
                labels=tuple(intent.payload.get("labels") or ()),
                marker=intent.marker,
            ), access_token=str(secret["access_token"]))
        except Exception as exc:
            result = ForgeResult(
                status="outcome_unknown", reason=f"provider_exception:{type(exc).__name__}",
            )
        if result.status in {"published", "partially_published"} and result.issue:
            intent.status = result.status
            intent.published_issue_ref = result.issue.__dict__
            if result.status == "partially_published":
                intent.failure_reason = result.reason
            event_type = "self_issue.published"
            if result.status == "partially_published":
                event_type = "self_issue.published.partial"
            if provider_name == "github":
                self._publish_github_evidence_comment(
                    draft=draft,
                    intent=intent,
                    provider=provider,
                    access_token=str(secret["access_token"]),
                    causation_id=causation_id,
                )
        elif result.status == "outcome_unknown":
            intent.status = "outcome_unknown"
            event_type = "self_issue.publication.outcome_unknown"
        else:
            intent.status = "publish_failed"
            intent.failure_reason = result.reason
            if result.status == "authorization_required":
                self.secrets.delete(secret_key)
            event_type = "self_issue.publish.failed"
        intent.updated_at = utc_now()
        self.intents.save(intent)
        self.writer.emit(
            event_type,
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "batch_id": intent.batch_id,
                "intent_id": intent.intent_id,
                "provider": provider_name,
                "status": intent.status,
                "issue_ref": intent.published_issue_ref,
                "reason": result.reason,
            },
        )

    def _publish_github_evidence_comment(
        self,
        *,
        draft: Any,
        intent: PublicationIntent,
        provider: Any,
        access_token: str,
        causation_id: str,
    ) -> None:
        """Project local evidence to one idempotent GitHub comment."""

        refs = [item for item in (draft.attachment_refs or []) if isinstance(item, dict)]
        if not refs:
            intent.evidence_comment_status = "not_required"
            return
        issue = intent.published_issue_ref
        try:
            issue_number = int(str(issue.get("number") or "0"))
        except ValueError:
            intent.evidence_comment_status = "publish_failed"
            intent.evidence_comment_failure_reason = "github_issue_number_missing"
            return
        marker = f"<!-- zf-self-issue-evidence:{intent.marker} -->"
        lines = [
            marker,
            "## ZaoFu evidence",
            "",
            "This comment is generated from the confirmed Self-Issue evidence snapshot.",
            "",
        ]
        attachment_results: list[dict[str, Any]] = []
        omitted_files: list[tuple[str, str]] = []
        release_candidates: list[tuple[str, str, str, bytes]] = []
        for item in refs:
            filename = safe_report_text(
                str(item.get("filename") or item.get("preview") or "attachment")
            )
            content_type = str(item.get("content_type") or "").lower()
            digest = str(item.get("sha256") or "")
            source = str(item.get("source") or "")
            if source not in {"user", "agent"}:
                source = "user" if str(item.get("kind") or "").startswith("self_issue_user_") else "agent"
            description = safe_report_text(str(item.get("description") or f"{source.title()} attachment ({filename})."))
            purpose = safe_report_text(str(item.get("purpose") or "Issue assessment evidence."))
            try:
                path = self._verified_attachment_path(draft, item)
            except (OSError, ValueError):
                attachment_results.append({"filename": filename, "sha256": digest, "status": "omitted", "reason": "artifact_unavailable", "content_type": content_type, "source": source, "description": description, "purpose": purpose})
                continue
            try:
                byte_count = path.stat().st_size
            except OSError:
                byte_count = _MAX_GITHUB_ATTACHMENT_BYTES + 1
            if byte_count > _MAX_GITHUB_ATTACHMENT_BYTES:
                attachment_results.append({"filename": filename, "sha256": digest, "status": "omitted", "reason": "attachment_too_large", "content_type": content_type, "source": source, "description": description, "purpose": purpose})
                omitted_files.append((filename, "artifact exceeds the 20 MB safe limit"))
                continue
            try:
                release_candidates.append((filename, content_type, digest, path.read_bytes()))
            except OSError:
                attachment_results.append({"filename": filename, "sha256": digest, "status": "omitted", "reason": "artifact_unavailable", "content_type": content_type, "source": source, "description": description, "purpose": purpose})
                continue
            attachment_results.append({"filename": filename, "sha256": digest, "status": "queued_release", "content_type": content_type, "source": source, "description": description, "purpose": purpose})
        release_result: dict[str, Any] = {}
        release_uploader = getattr(provider, "publish_release_assets", None)
        if release_candidates and callable(release_uploader):
            try:
                release_result = dict(release_uploader(
                    str(intent.target_binding.get("project") or ""),
                    draft.draft_id,
                    tuple(release_candidates),
                    access_token=access_token,
                ))
            except Exception as exc:
                release_result = {"status": "outcome_unknown", "reason": f"release_provider_exception:{type(exc).__name__}"}
        release_assets = {
            str(item.get("sha256") or ""): item
            for item in release_result.get("assets", [])
            if isinstance(item, dict) and str(item.get("url") or "")
        }
        if release_assets:
            lines.extend(["Original artifact downloads:", ""])
            for item in attachment_results:
                asset = release_assets.get(str(item.get("sha256") or ""))
                if asset:
                    item["download_url"] = str(asset["url"])
                    item["release_asset"] = str(asset.get("asset_name") or "")
                    item["status"] = "published"
                    item.pop("reason", None)
                    omitted_files = [(name, reason) for name, reason in omitted_files if name != item["filename"]]
                    lines.append(
                        f"- [{item.get('filename', 'artifact')}]({asset['url']}) "
                        f"· source=`{item.get('source', 'agent')}` "
                        f"· {item.get('description', '')} "
                        f"· purpose: {item.get('purpose', '')} "
                        f"· SHA-256 `{item.get('sha256', '')}`"
                    )
            if str(release_result.get("manifest_url") or ""):
                lines.append(f"- [Evidence manifest]({release_result['manifest_url']})")
            lines.append("")
        elif release_candidates and release_result:
            omitted_files.append(("original artifacts", str(release_result.get("reason") or "release asset publication unavailable")))
        pending_release = [item for item in attachment_results if item.get("status") == "queued_release"]
        if pending_release:
            lines.extend(["", "The following artifacts were not uploaded:", "", *[
                f"- {item.get('filename', 'artifact')} · source=`{item.get('source', 'agent')}` · {item.get('description', '')} · purpose: {item.get('purpose', '')} · reason: release asset upload unavailable"
                for item in pending_release
            ]])
        if omitted_files:
            lines.extend([
                "",
                "The following files could not be attached automatically:",
                "",
                *[f"- {filename}: {reason}." for filename, reason in omitted_files],
            ])
        body = "\n".join(lines)
        if len(body) > _MAX_GITHUB_COMMENT_BODY_CHARS:
            body = body[:_MAX_GITHUB_COMMENT_BODY_CHARS]
        intent.evidence_comment_status = "publishing"
        intent.evidence_comment_digest = stable_digest({"marker": marker, "body": body, "attachments": [str(item.get("sha256") or "") for item in refs]})
        self.writer.emit(
            "self_issue.publication.evidence_comment.requested",
            actor="kernel",
            causation_id=causation_id or None,
            payload={"draft_id": draft.draft_id, "intent_id": intent.intent_id, "provider": "github", "attachment_count": len(release_candidates)},
        )
        try:
            if any(item.get("status") != "published" for item in attachment_results):
                # Do not freeze an incomplete index behind the success marker.
                # Asset retries are idempotent; publish the index when complete.
                result = {"status": "outcome_unknown" if release_result.get("status") == "outcome_unknown" else "publish_failed",
                          "reason": str(release_result.get("reason") or "evidence_assets_incomplete")}
            else:
                result = provider.publish_issue_comment(
                    str(intent.target_binding.get("project") or ""),
                    issue_number, body, marker=marker, access_token=access_token,
                )
        except Exception as exc:
            result = {"status": "outcome_unknown", "reason": f"provider_exception:{type(exc).__name__}"}
        intent.evidence_comment_status = str(result.get("status") or "outcome_unknown")
        intent.evidence_comment_id = str(result.get("id") or "")
        intent.evidence_comment_url = str(result.get("url") or "")
        intent.evidence_comment_failure_reason = str(result.get("reason") or "")
        for item in attachment_results:
            if item.get("status") != "queued_release":
                continue
            if intent.evidence_comment_status == "published":
                # Release assets carry the actual download URL. If an asset
                # was unavailable, keep the item pending instead of claiming
                # that a comment URL is a file download.
                asset = release_assets.get(str(item.get("sha256") or ""))
                if asset:
                    item["status"] = "published"
                    item["download_url"] = str(asset.get("url") or "")
                else:
                    item["status"] = "pending"
                    item["reason"] = "release_asset_upload_unavailable"
            else:
                item["status"] = "pending"
                item["reason"] = intent.evidence_comment_failure_reason or "comment_not_published"
        intent.evidence_comment_attachments = attachment_results
        if intent.evidence_comment_status == "published":
            self.writer.emit(
                "self_issue.publication.evidence_comment.published",
                actor="kernel",
                causation_id=causation_id or None,
                payload={"draft_id": draft.draft_id, "intent_id": intent.intent_id, "comment_id": intent.evidence_comment_id, "comment_url": intent.evidence_comment_url},
            )
        else:
            self.writer.emit(
                "self_issue.publication.evidence_comment." + ("outcome_unknown" if intent.evidence_comment_status == "outcome_unknown" else "failed"),
                actor="kernel",
                causation_id=causation_id or None,
                payload={"draft_id": draft.draft_id, "intent_id": intent.intent_id, "reason": intent.evidence_comment_failure_reason},
            )

    def _finish_batch(self, batch: PublicationBatch | None, *, draft: Any) -> dict[str, Any]:
        if batch is None:
            raise ValueError("publication batch not found")
        statuses = self._provider_statuses(batch)
        values = set(statuses.values())
        comment_statuses = {
            item.evidence_comment_status
            for item in self._batch_intents(batch)
            if item.evidence_comment_status not in {"", "not_required"}
        }
        if values.intersection({"outcome_unknown", "publishing"}):
            status = "outcome_unknown"
        elif "partially_published" in values:
            status = "partially_published"
        elif values == {"published"} and comment_statuses.intersection({"publish_failed", "outcome_unknown"}):
            status = "partially_published" if "publish_failed" in comment_statuses else "outcome_unknown"
        elif values == {"published"}:
            status = "published"
        elif "published" in values:
            status = "partially_published"
        elif values == {"publish_failed"}:
            status = "publish_failed"
        else:
            status = "publishing"
        batch.status = status
        batch.updated_at = utc_now()
        self.batches.save(batch)
        refs = self._published_refs(batch)
        draft.publication_state = status
        if refs:
            draft.published_issue_ref = dict(next(iter(refs.values())))
        draft.updated_at = utc_now()
        self.drafts.save(draft)
        if status == "published":
            self._create_handoff_bundles(batch, draft=draft)
        response = self._batch_response(batch)
        response.update({
            "ok": status == "published",
            "status": status,
            "providers": statuses,
            "issues": refs,
            "issue": dict(next(iter(refs.values()))) if len(refs) == 1 else {},
            "draft": self._draft_view(draft),
        })
        return response

    def _batch_response(
        self, batch: PublicationBatch, *, previews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        intents = self._batch_intents(batch)
        previews = previews or {str(item.target_binding["provider"]): dict(item.payload) for item in intents}
        refs = self._published_refs(batch)
        response: dict[str, Any] = {
            "ok": batch.status in {"previewed", "confirmed", "published"},
            "status": batch.status,
            "batch_id": batch.batch_id,
            "draft_revision": batch.draft_revision,
            "publication_mode": batch.publication_mode,
            "intent_ids": dict(batch.intent_ids),
            "payload_digest": batch.payload_digest,
            "previews": previews,
            "providers": self._provider_statuses(batch),
            "issues": refs,
            "confirmation_id": batch.confirmation_id,
            "evidence_comments": {
                str(item.target_binding.get("provider") or ""): {
                    "status": item.evidence_comment_status,
                    "id": item.evidence_comment_id,
                    "url": item.evidence_comment_url,
                    "failure_reason": item.evidence_comment_failure_reason,
                    "attachments": list(item.evidence_comment_attachments),
                }
                for item in intents
                if item.evidence_comment_status not in {"", "not_required"}
            },
        }
        if len(intents) == 1:
            response["intent_id"] = intents[0].intent_id
            response["preview"] = dict(intents[0].payload)
            if refs:
                response["issue"] = dict(next(iter(refs.values())))
        return response

    def _required_batch(self, payload: dict[str, Any]) -> PublicationBatch:
        batch_id = str(payload.get("batch_id") or "").strip()
        if not batch_id:
            intent_id = str(payload.get("intent_id") or "").strip()
            intent = self.intents.get(intent_id) if intent_id else None
            batch_id = intent.batch_id if intent is not None else ""
        batch = self.batches.get(batch_id) if batch_id else None
        if batch is None:
            raise ValueError("publication batch not found")
        return batch

    def _batch_intents(self, batch: PublicationBatch) -> list[PublicationIntent]:
        intents = [self.intents.get(batch.intent_ids[provider]) for provider in batch.selected_providers]
        if any(item is None for item in intents):
            raise ValueError("publication batch intent is missing")
        return [item for item in intents if item is not None]

    def _provider_statuses(self, batch: PublicationBatch) -> dict[str, str]:
        return {
            provider: str(self.intents.get(intent_id).status)
            for provider, intent_id in batch.intent_ids.items()
            if self.intents.get(intent_id) is not None
        }

    def _published_refs(self, batch: PublicationBatch) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for provider, intent_id in batch.intent_ids.items():
            intent = self.intents.get(intent_id)
            if (
                intent is not None
                and intent.status in {"published", "partially_published"}
                and intent.published_issue_ref
            ):
                result[provider] = dict(intent.published_issue_ref)
        return result

    def _validate_publication_snapshot(self, draft: Any, intent: PublicationIntent) -> None:
        self._validate_publication_target(draft, intent)
        provider = str(intent.target_binding.get("provider") or "")
        if intent.permission_snapshot != self._permission_snapshot(provider):
            raise ValueError("publication permission snapshot changed")
        current_payload, redaction, disclosure = self._publication_payload(
            draft, marker=intent.marker, provider=provider,
        )
        if (
            draft.revision != intent.draft_revision
            or stable_digest(intent.payload) != intent.payload_digest
            or stable_digest(current_payload) != intent.payload_digest
            or redaction != intent.redaction_digest
            or disclosure != intent.disclosure_digest
        ):
            raise ValueError("publication snapshot changed")
