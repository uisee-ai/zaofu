"""Two-stage attachment preparation for providers that support binary upload."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zf.core.self_issue.models import AttachmentPreparationIntent, stable_digest, utc_now
from zf.integrations.forge.base import AttachmentUploadRequest


ATTACHMENT_CONFIRMATION_TTL = timedelta(minutes=10)


class SelfIssueAttachmentMixin:
    def attachment_preview(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        draft = self._required_draft(payload)
        locked = self._evaluation_lock_response(draft, action="attachment_preview")
        if locked is not None:
            return locked
        if draft.evidence_status not in {"completed", "interrupted", "failed"}:
            return {
                "ok": False, "status": f"evidence_{draft.evidence_status}",
                "reason": "evidence assessment must settle before attachment preparation",
                "draft": self._draft_view(draft),
            }
        if not draft.attachment_refs:
            return {
                "ok": True, "status": "attachments_not_required",
                "draft": self._draft_view(draft),
            }
        mode, providers = self._publication_selection(payload, draft=draft)
        # Attachment preparation is a separate disclosure gate, but the
        # operator should still be able to review the exact Markdown that will
        # be published before uploading binaries.  Build provider previews
        # without creating publication intents; the final publication preview
        # is regenerated after preparation and therefore remains authoritative.
        previews: dict[str, dict[str, Any]] = {}
        for provider in providers:
            marker = hashlib.sha256(
                f"self-issue-preview:{draft.draft_id}:{draft.revision}:{provider}".encode(),
            ).hexdigest()[:24]
            preview, _redaction_digest, _disclosure_digest = self._publication_payload(
                draft, marker=marker, provider=provider,
            )
            previews[provider] = preview
        if "gitlab" not in providers:
            return {
                "ok": True,
                "status": "attachments_omitted_for_github",
                "publication_mode": mode,
                "reason": "GitHub Issue binary attachment upload is not supported",
                "previews": previews,
                "preview": previews.get("github", {}),
                "draft": self._draft_view(draft),
            }
        locked = self.attachments.locked_for_draft(draft.draft_id)
        if locked is not None:
            return {
                "ok": False, "status": "attachment_outcome_unknown",
                "preparation_id": locked.preparation_id,
                "reason": "attachment recovery is locked",
            }
        target = self._target_for_provider("gitlab", draft=draft)
        key = self._secret_key(payload, draft, provider="gitlab")
        manifest = self._attachment_manifest(draft)
        digest = stable_digest(manifest)
        reusable = next((
            item for item in reversed(self.attachments.for_draft(draft.draft_id))
            if item.draft_revision == draft.revision
            and item.target_binding == target
            and item.manifest_digest == digest
            and item.status in {"previewed", "confirmed", "prepared"}
        ), None)
        if reusable is None:
            reusable = AttachmentPreparationIntent(
                preparation_id=f"apu-{uuid.uuid4().hex[:12]}",
                draft_id=draft.draft_id,
                draft_revision=draft.revision,
                target_binding=target,
                credential_subject=key.subject,
                attachment_manifest=manifest,
                manifest_digest=digest,
            )
            self.attachments.save(reusable)
            self.writer.emit(
                "self_issue.attachments.previewed", actor="kernel",
                causation_id=causation_id or None,
                payload={
                    "draft_id": draft.draft_id,
                    "preparation_id": reusable.preparation_id,
                    "manifest_digest": digest,
                    "attachment_count": len(manifest),
                },
            )
        return {
            "ok": True, "status": f"attachments_{reusable.status}",
            "preparation_id": reusable.preparation_id,
            "manifest_digest": reusable.manifest_digest,
            # ``local_path`` is a transient local-Web projection. It is never
            # persisted in the preparation intent or provider payload.
            "attachments": self._local_attachment_views(
                draft, reusable.attachment_manifest,
            ),
            "publication_mode": mode,
            "previews": previews,
            "preview": previews.get("gitlab", {}),
            "confirmation_id": reusable.confirmation_id,
            "draft": self._draft_view(draft),
        }

    def attachment_confirm(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        intent = self._required_attachment_intent(payload)
        draft = self._required_draft({"draft_id": intent.draft_id})
        locked = self._evaluation_lock_response(draft, action="attachment_confirm")
        if locked is not None:
            return locked
        if intent.status != "previewed" or draft.revision != intent.draft_revision:
            return {"ok": False, "status": "stale_attachment_preview"}
        if str(payload.get("manifest_digest") or "") != intent.manifest_digest:
            return {"ok": False, "status": "attachment_confirmation_mismatch"}
        if stable_digest(self._attachment_manifest(draft)) != intent.manifest_digest:
            return {"ok": False, "status": "stale_attachment_preview"}
        confirmation_id = secrets.token_urlsafe(24)
        intent.status = "confirmed"
        intent.confirmation_id = confirmation_id
        intent.confirmed_at = utc_now()
        intent.confirmation_expires_at = (
            datetime.now(timezone.utc) + ATTACHMENT_CONFIRMATION_TTL
        ).isoformat()
        intent.updated_at = utc_now()
        self.attachments.save(intent)
        self.writer.emit(
            "self_issue.attachments.confirmed", actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id, "preparation_id": intent.preparation_id,
                "manifest_digest": intent.manifest_digest,
                "expires_at": intent.confirmation_expires_at,
            },
        )
        return {
            "ok": True, "status": "attachments_confirmed",
            "preparation_id": intent.preparation_id,
            "confirmation_id": confirmation_id,
            "expires_at": intent.confirmation_expires_at,
            "draft_id": draft.draft_id,
        }

    def attachment_prepare(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        intent = self._required_attachment_intent(payload)
        draft = self._required_draft({"draft_id": intent.draft_id})
        locked = self._evaluation_lock_response(draft, action="attachment_prepare")
        if locked is not None:
            return locked
        if intent.status == "prepared":
            return {
                "ok": True, "status": "attachments_prepared",
                "draft": self._draft_view(draft),
            }
        if intent.status in {"preparing", "outcome_unknown"}:
            return {
                "ok": False, "status": "attachment_outcome_unknown",
                "reason": "attachment preparation is locked pending manual resolution",
            }
        if intent.status != "confirmed":
            return {"ok": False, "status": "attachment_confirmation_required"}
        if datetime.now(timezone.utc) >= _parse_time(intent.confirmation_expires_at):
            return {"ok": False, "status": "attachment_confirmation_expired"}
        if draft.revision != intent.draft_revision:
            return {"ok": False, "status": "stale_attachment_confirmation"}
        provider = str(intent.target_binding.get("provider") or "")
        if provider != "gitlab":
            raise ValueError("binary attachment preparation supports GitLab only")
        if intent.target_binding != self._target_for_provider(provider, draft=draft):
            raise ValueError("attachment target no longer matches the locked policy")
        key = self._secret_key(payload, draft, provider=provider)
        if key.subject != intent.credential_subject:
            return {"ok": False, "status": "stale_attachment_confirmation"}
        secret = self._provider_secret(key, causation_id=causation_id)
        if not secret:
            return {
                "ok": False, "status": "authorization_required",
                "draft_id": draft.draft_id,
                "preparation_id": intent.preparation_id,
                "confirmation_id": intent.confirmation_id,
            }
        claimed, won = self.attachments.claim_prepare(
            intent.preparation_id, str(payload.get("confirmation_id") or ""),
        )
        if claimed is None or not won:
            return {"ok": False, "status": "attachment_confirmation_consumed"}
        intent = claimed
        prepared: list[dict[str, Any]] = []
        for item in intent.attachment_manifest:
            try:
                path = self._verified_attachment_path(draft, item)
                result = self._forge_for(provider).upload_attachment(
                    AttachmentUploadRequest(
                        project=str(intent.target_binding.get("project") or ""),
                        filename=str(item["filename"]),
                        content_type=str(item["content_type"]),
                        content=path.read_bytes(),
                        digest=str(item["sha256"]),
                    ),
                    access_token=str(secret["access_token"]),
                )
            except Exception as exc:
                intent.status = "outcome_unknown"
                intent.failure_reason = type(exc).__name__
                intent.prepared_attachments = prepared
                intent.updated_at = utc_now()
                self.attachments.save(intent)
                self.writer.emit(
                    "self_issue.attachments.outcome_unknown", actor="kernel",
                    causation_id=causation_id or None,
                    payload={
                        "draft_id": draft.draft_id,
                        "preparation_id": intent.preparation_id,
                        "attachment_digest": item["sha256"],
                    },
                )
                return {"ok": False, "status": "attachment_outcome_unknown"}
            if result.status != "published" or result.attachment is None:
                intent.status = (
                    "outcome_unknown" if result.status == "outcome_unknown" else "failed"
                )
                intent.failure_reason = result.reason
                intent.prepared_attachments = prepared
                intent.updated_at = utc_now()
                self.attachments.save(intent)
                return {
                    "ok": False,
                    "status": (
                        "attachment_outcome_unknown"
                        if intent.status == "outcome_unknown"
                        else "attachment_prepare_failed"
                    ),
                    "reason": result.reason,
                }
            prepared.append({
                **result.attachment.__dict__,
                "sha256": str(item["sha256"]),
                "byte_count": int(item["byte_count"]),
                "content_type": str(item["content_type"]),
                "kind": str(item.get("kind") or "self_issue_user_attachment"),
                "capture_source": str(item.get("capture_source") or "user"),
                "source": str(item.get("source") or ("user" if str(item.get("kind") or "").startswith("self_issue_user_") else "agent")),
                "description": str(item.get("description") or f"Attachment ({item.get('filename') or 'artifact'})."),
                "purpose": str(item.get("purpose") or "Issue assessment evidence."),
            })
            intent.prepared_attachments = list(prepared)
            intent.updated_at = utc_now()
            self.attachments.save(intent)
        intent.status = "prepared"
        intent.updated_at = utc_now()
        self.attachments.save(intent)
        draft.published_attachments = prepared
        draft.revision += 1
        draft.updated_at = utc_now()
        draft.publication_state = "draft"
        self.intents.invalidate_unpublished(
            draft.draft_id, reason="GitLab attachment URLs were prepared",
        )
        self.batches.invalidate_unpublished(
            draft.draft_id, reason="GitLab attachment URLs were prepared",
        )
        self.drafts.save(draft)
        event = self.writer.emit(
            "self_issue.attachments.prepared", actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id, "preparation_id": intent.preparation_id,
                "attachment_count": len(prepared), "revision": draft.revision,
            },
        )
        return {
            "ok": True, "status": "attachments_prepared",
            "draft": self._draft_view(draft), "event_id": event.id,
        }

    def resolve_attachment_unknown(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        intent = self._required_attachment_intent(payload)
        if intent.status != "outcome_unknown":
            return {"ok": False, "status": "attachment_not_recoverable"}
        evidence_refs = [
            str(item).strip() for item in payload.get("evidence_refs") or []
            if str(item).strip()
        ]
        decision = str(payload.get("decision") or "")
        if not evidence_refs or decision not in {"prepared", "not_prepared"}:
            raise ValueError("attachment outcome decision and evidence_refs are required")
        draft = self._required_draft({"draft_id": intent.draft_id})
        intent.outcome_evidence_refs = evidence_refs
        if decision == "prepared":
            prepared = payload.get("prepared_attachments")
            if not isinstance(prepared, list) or len(prepared) != len(intent.attachment_manifest):
                raise ValueError(
                    "prepared attachment resolution requires one result per manifest item"
                )
            normalized = self._validated_manual_attachments(
                intent.attachment_manifest,
                prepared,
                project=str(intent.target_binding.get("project") or ""),
            )
            intent.status = "prepared"
            intent.prepared_attachments = normalized
            draft.published_attachments = normalized
            draft.revision += 1
            draft.publication_state = "draft"
            draft.updated_at = utc_now()
            self.intents.invalidate_unpublished(
                draft.draft_id, reason="attachment outcome was manually resolved",
            )
            self.batches.invalidate_unpublished(
                draft.draft_id, reason="attachment outcome was manually resolved",
            )
            self.drafts.save(draft)
        else:
            intent.status = "failed"
            intent.failure_reason = "manual_not_prepared"
        intent.updated_at = utc_now()
        self.attachments.save(intent)
        event = self.writer.emit(
            "self_issue.attachments.resolved", actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "preparation_id": intent.preparation_id,
                "decision": decision,
                "evidence_refs": evidence_refs,
            },
        )
        return {
            "ok": True,
            "status": (
                "attachments_prepared" if decision == "prepared"
                else "attachment_prepare_failed"
            ),
            "draft": self._draft_view(draft),
            "event_id": event.id,
        }

    @staticmethod
    def _validated_manual_attachments(
        manifest: list[dict[str, Any]], value: list[Any], *, project: str,
    ) -> list[dict[str, Any]]:
        expected = {str(item.get("sha256") or ""): item for item in manifest}
        normalized: list[dict[str, Any]] = []
        for raw in value:
            if not isinstance(raw, dict):
                raise ValueError("prepared attachment result must be an object")
            digest = str(raw.get("sha256") or "")
            item = expected.get(digest)
            url = str(raw.get("url") or "")
            markdown = str(raw.get("markdown") or "")
            if (
                item is None
                or not url.startswith("https://gitlab.com/")
                or not markdown
                or "\r" in markdown
                or "\n" in markdown
            ):
                raise ValueError("prepared attachment evidence is invalid")
            normalized.append({
                "provider": "gitlab",
                "project": project,
                "filename": str(item.get("filename") or "attachment"),
                "markdown": markdown,
                "url": url,
                "upload_id": str(raw.get("upload_id") or ""),
                "sha256": digest,
                "byte_count": int(item.get("byte_count") or 0),
                "content_type": str(item.get("content_type") or ""),
                "kind": str(item.get("kind") or "self_issue_user_attachment"),
                "capture_source": str(item.get("capture_source") or "user"),
                "source": str(item.get("source") or ("user" if str(item.get("kind") or "").startswith("self_issue_user_") else "agent")),
                "description": str(item.get("description") or f"Attachment ({item.get('filename') or 'artifact'})."),
                "purpose": str(item.get("purpose") or "Issue assessment evidence."),
            })
        if len({item["sha256"] for item in normalized}) != len(expected):
            raise ValueError("prepared attachment evidence has duplicate or missing digests")
        return normalized

    def _attachment_manifest(self, draft: Any) -> list[dict[str, Any]]:
        return [{
            "attachment_id": str(item.get("attachment_id") or ""),
            "filename": str(item.get("filename") or item.get("preview") or "attachment"),
            "ref": str(item.get("ref") or ""),
            "sha256": str(item.get("sha256") or ""),
            "byte_count": int(item.get("byte_count") or 0),
            "content_type": str(item.get("content_type") or ""),
            "kind": str(item.get("kind") or "self_issue_user_attachment"),
            "capture_source": str(item.get("capture_source") or "user"),
            "source": str(item.get("source") or ("user" if str(item.get("kind") or "").startswith("self_issue_user_") else "agent")),
            "description": str(item.get("description") or f"Attachment ({item.get('filename') or 'artifact'})."),
            "purpose": str(item.get("purpose") or "Issue assessment evidence."),
            "redaction_applied": bool(item.get("redaction_applied")),
            "public_disclosure_confirmed": True,
        } for item in draft.attachment_refs]

    def _verified_attachment_path(self, draft: Any, item: dict[str, Any]) -> Path:
        relative = Path(str(item.get("ref") or ""))
        root = self.state_dir.resolve()
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root) or not path.is_file():
            raise ValueError("attachment artifact is outside controlled state")
        if hashlib.sha256(path.read_bytes()).hexdigest() != str(item.get("sha256") or ""):
            raise ValueError("attachment artifact digest mismatch")
        expected = next((
            ref for ref in draft.attachment_refs
            if str(ref.get("sha256") or "") == str(item.get("sha256") or "")
        ), None)
        if expected is None:
            raise ValueError("attachment no longer belongs to this Draft")
        return path

    def local_attachment_file(
        self, *, draft_id: str, digest: str,
    ) -> tuple[Path, str, str]:
        """Resolve one Draft-owned local attachment for the read-only Web route."""
        draft = self._required_draft({"draft_id": draft_id})
        item = next((
            candidate for candidate in self._attachment_manifest(draft)
            if str(candidate.get("sha256") or "") == digest
        ), None)
        if item is None:
            raise ValueError("attachment does not belong to this Draft")
        path = self._verified_attachment_path(draft, item)
        return (
            path,
            str(item.get("content_type") or "application/octet-stream"),
            str(item.get("filename") or "attachment"),
        )

    def _local_attachment_views(
        self, draft: Any, manifest: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {**item, "local_path": str(self._verified_attachment_path(draft, item))}
            for item in manifest
        ]

    def _required_attachment_intent(
        self, payload: dict[str, Any],
    ) -> AttachmentPreparationIntent:
        intent = self.attachments.get(str(payload.get("preparation_id") or ""))
        if intent is None:
            raise ValueError("attachment preparation intent not found")
        return intent


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
