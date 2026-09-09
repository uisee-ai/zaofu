"""GitHub App Device Flow for confirmed Self-Issue publication batches."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from zf.core.security.secret_provider import SecretKey, forge_credential_workspace_id
from zf.core.self_issue.models import IssueDraft, PublicationBatch, utc_now
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


class SelfIssueGitHubOAuthMixin:
    """Keep Device Flow transactions and credentials inside the Kernel."""

    def github_device_start(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        draft = self._required_draft(payload)
        batch = self._optional_github_batch(payload, draft=draft)
        target = self._target_config("github")
        client_id = str(
            getattr(target, "oauth_client_id", "")
            or os.environ.get("ZF_GITHUB_APP_CLIENT_ID")
            or ""
        ).strip()
        session_id = str(payload.get("session_id") or "").strip()
        if not client_id or not session_id:
            raise ValueError("GitHub client_id and session_id are required")
        key = self._secret_key(payload, draft, provider="github")
        existing = self._provider_secret(key, causation_id=causation_id)
        if existing:
            return self._resume_github_batch(
                draft=draft,
                batch=batch,
                payload=payload,
                credential_subject=key.subject,
                causation_id=causation_id,
            )

        response = self.github_oauth.start(client_id=client_id)
        now = datetime.now(timezone.utc)
        expires_in = max(1, int(response.get("expires_in") or 900))
        interval = max(5, int(response.get("interval") or 5))
        transaction_id = f"ghd-{secrets.token_hex(12)}"
        transaction = {
            "transaction_id": transaction_id,
            "draft_id": draft.draft_id,
            "batch_id": batch.batch_id if batch else "",
            "confirmation_id": batch.confirmation_id if batch else "",
            "session_binding_digest": hashlib.sha256(session_id.encode()).hexdigest(),
            "client_id": client_id,
            "device_code": str(response["device_code"]),
            "user_code": str(response["user_code"]),
            "verification_uri": str(response["verification_uri"]),
            "interval": interval,
            "next_poll_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
            "consumed": False,
            "user_id": key.user_id,
            "workspace_id": key.workspace_id,
            "provider": key.provider,
            "authorization_domain": key.authorization_domain,
            "created_at": utc_now(),
        }
        self._save_github_device_transaction(transaction)
        self.writer.emit(
            "self_issue.oauth.started",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "batch_id": transaction["batch_id"],
                "provider": "github",
                "authorization_domain": "github.com",
                "session_binding_digest": transaction["session_binding_digest"],
                "publication_continuation": bool(batch),
            },
        )
        return {
            "ok": True,
            "status": "authorization_required",
            "provider": "github",
            "draft_id": draft.draft_id,
            "batch_id": transaction["batch_id"],
            "confirmation_id": transaction["confirmation_id"],
            "transaction_id": transaction_id,
            "user_code": transaction["user_code"],
            "verification_uri": transaction["verification_uri"],
            "expires_at": transaction["expires_at"],
            "interval": interval,
            "scope": "issues:write",
            "scope_notice": (
                "GitHub authorization is limited to creating Issues in the installed "
                "repository; binary attachments are not uploaded."
            ),
        }

    def github_device_poll(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        transaction_id = str(payload.get("transaction_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        if not transaction_id or not session_id:
            raise ValueError("transaction_id and session_id are required")
        session_digest = hashlib.sha256(session_id.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        with locked_path(self._github_device_path):
            rows = self._load_github_device_transactions()
            transaction = next((
                row for row in rows
                if str(row.get("transaction_id") or "") == transaction_id
            ), None)
            if (
                transaction is None
                or bool(transaction.get("consumed"))
                or str(transaction.get("session_binding_digest") or "") != session_digest
            ):
                raise ValueError(
                    "GitHub Device Flow transaction is invalid, consumed, or session-bound"
                )
            if now >= self._parse_time(str(transaction["expires_at"])):
                transaction["consumed"] = True
                self._write_github_device_transactions(rows)
                return {"ok": False, "status": "expired_token", "provider": "github"}
            next_poll_at = self._parse_time(str(transaction["next_poll_at"]))
            if now < next_poll_at:
                return {
                    "ok": False,
                    "status": "authorization_pending",
                    "provider": "github",
                    "transaction_id": transaction_id,
                    "retry_after": max(1, int((next_poll_at - now).total_seconds())),
                }
            result = self.github_oauth.poll(
                client_id=str(transaction["client_id"]),
                device_code=str(transaction["device_code"]),
            )
            status = str(result.get("status") or "")
            interval = int(transaction.get("interval") or 5)
            if status in {"authorization_pending", "slow_down"}:
                if status == "slow_down":
                    interval += 5
                    transaction["interval"] = interval
                transaction["next_poll_at"] = (
                    now + timedelta(seconds=interval)
                ).isoformat()
                self._write_github_device_transactions(rows)
                return {
                    "ok": False,
                    "status": status,
                    "provider": "github",
                    "transaction_id": transaction_id,
                    "retry_after": interval,
                }
            if status in {"access_denied", "expired_token"}:
                transaction["consumed"] = True
                self._write_github_device_transactions(rows)
                return {"ok": False, "status": status, "provider": "github"}
            if status != "connected":
                raise ValueError("GitHub Device Flow returned an unsupported status")

            key = SecretKey(
                user_id=str(transaction["user_id"]),
                workspace_id=str(transaction["workspace_id"]),
                provider="github",
                authorization_domain=str(transaction["authorization_domain"]),
            )
            result["client_id"] = str(transaction["client_id"])
            self.secrets.put(key, result)
            transaction["consumed"] = True
            self._write_github_device_transactions(rows)

        draft = self._required_draft({"draft_id": str(transaction["draft_id"])})
        batch = self.batches.get(str(transaction.get("batch_id") or ""))
        self.writer.emit(
            "self_issue.oauth.connected",
            actor="kernel",
            causation_id=causation_id or None,
            payload={
                "draft_id": draft.draft_id,
                "batch_id": str(transaction.get("batch_id") or ""),
                "provider": "github",
                "authorization_domain": "github.com",
                "credential_subject": key.subject,
                "publication_continuation": bool(batch),
            },
        )
        return self._resume_github_batch(
            draft=draft,
            batch=batch,
            payload={
                "user_id": str(transaction["user_id"]),
                "workspace_id": str(transaction["workspace_id"]),
            },
            credential_subject=key.subject,
            causation_id=causation_id,
        )

    def _resume_github_batch(
        self,
        *,
        draft: IssueDraft,
        batch: PublicationBatch | None,
        payload: dict[str, Any],
        credential_subject: str,
        causation_id: str,
    ) -> dict[str, Any]:
        if batch is None:
            return {
                "ok": True,
                "status": "connected",
                "provider": "github",
                "draft_id": draft.draft_id,
                "draft": self._draft_view(draft),
                "credential_subject": credential_subject,
                "published": False,
            }
        result = self.publish({
            "batch_id": batch.batch_id,
            "confirmation_id": batch.confirmation_id,
            "user_id": str(payload.get("user_id") or "local-user"),
            "workspace_id": str(
                payload.get("workspace_id")
                or forge_credential_workspace_id(self.project_root)
            ),
        }, causation_id=causation_id)
        return {
            **result,
            "provider": "github",
            "credential_subject": credential_subject,
            "resumed_publication": True,
        }

    def _optional_github_batch(
        self, payload: dict[str, Any], *, draft: IssueDraft,
    ) -> PublicationBatch | None:
        batch_id = str(payload.get("batch_id") or "").strip()
        confirmation_id = str(payload.get("confirmation_id") or "").strip()
        if not batch_id and not confirmation_id:
            return None
        if not batch_id or not confirmation_id:
            raise ValueError("GitHub continuation requires batch_id and confirmation_id")
        batch = self.batches.get(batch_id)
        if (
            batch is None
            or batch.draft_id != draft.draft_id
            or batch.status != "confirmed"
            or batch.confirmation_id != confirmation_id
            or "github" not in batch.selected_providers
        ):
            raise ValueError("GitHub continuation is not confirmed for this Draft")
        if datetime.now(timezone.utc) >= self._parse_time(batch.confirmation_expires_at):
            raise ValueError("GitHub continuation confirmation expired")
        return batch

    @property
    def _github_device_path(self) -> Path:
        return self.state_dir / "self-issues" / "github-device-transactions.json"

    def _load_github_device_transactions(self) -> list[dict[str, Any]]:
        if not self._github_device_path.exists():
            return []
        value = json.loads(self._github_device_path.read_text(encoding="utf-8") or "[]")
        return value if isinstance(value, list) else []

    def _save_github_device_transaction(self, transaction: dict[str, Any]) -> None:
        with locked_path(self._github_device_path):
            rows = self._load_github_device_transactions()
            rows.append(transaction)
            self._write_github_device_transactions(rows)

    def _write_github_device_transactions(self, rows: list[dict[str, Any]]) -> None:
        atomic_write_text(
            self._github_device_path,
            json.dumps(rows, indent=2, sort_keys=True) + "\n",
        )
        os.chmod(self._github_device_path, 0o600)
