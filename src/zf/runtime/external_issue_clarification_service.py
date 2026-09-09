"""Controlled GitHub clarification publication and automatic re-triage."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.issue_triage.store import IssueMirrorStore
from zf.core.security.secret_provider import (
    LocalSecretProvider,
    SecretKey,
    forge_credential_store_path,
    forge_credential_workspace_id,
)
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.integrations.forge.github import GitHubComProvider, GitHubDeviceOAuthClient
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.external_issue_clarification import (
    hydrate_external_issue_clarification,
)
from zf.runtime.external_issue_ingress import ExternalIssueIngressService


class ExternalIssueClarificationService:
    def __init__(
        self,
        state_dir: Path,
        config: ZfConfig,
        *,
        project_root: Path,
        writer: EventWriter | None = None,
        github: GitHubComProvider | None = None,
        oauth: GitHubDeviceOAuthClient | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.config = config
        self.project_root = Path(project_root)
        self.writer = writer or EventWriter(
            event_log_from_project(self.state_dir, config=config),
            default_origin="kernel",
        )
        self.github = github or GitHubComProvider()
        self.oauth = oauth or GitHubDeviceOAuthClient()
        self.secrets = LocalSecretProvider(
            forge_credential_store_path(self.state_dir),
        )
        target = config.self_issue.targets.get("github")
        self.repository = str(getattr(target, "project", "") or "")
        self.authorization_domain = str(
            getattr(target, "authorization_domain", "") or "github.com"
        )

    def submit(self, payload: dict[str, Any], *, causation_id: str = "") -> dict[str, Any]:
        pending = self._validated_submission(payload)
        secret = self.secrets.reveal(self._secret_key(payload))
        if not secret or not str(secret.get("access_token") or ""):
            return {
                "ok": False,
                "status": "authorization_required",
                "provider": "github",
                "pending": pending,
            }
        answer_ref = write_immutable_json_sidecar(
            self.state_dir,
            pending,
            root="external-issues/clarification-answers",
            kind="external_issue_clarification_answers",
            schema_version="external-issue-clarification-answers.v1",
            created_by="external-issue-clarification",
            source_event_id=causation_id,
        )
        marker_id = hashlib.sha256(
            f"{pending['triage_id']}:{answer_ref['sha256']}".encode("utf-8")
        ).hexdigest()[:24]
        marker = f"<!-- zaofu-needs-info:{marker_id} -->"
        body = self._comment_body(pending, marker=marker)
        result = self.github.publish_issue_comment(
            self.repository,
            int(pending["issue_number"]),
            body,
            marker=marker,
            access_token=str(secret["access_token"]),
        )
        status = str(result.get("status") or "publish_failed")
        if status != "published":
            return {"ok": False, **result, "pending": pending}
        if not self._was_published(pending["triage_id"], marker_id):
            self.writer.emit(
                "external_issue.clarification.published",
                actor="kernel",
                task_id=str(pending.get("task_id") or "") or None,
                payload={
                    "source_key": pending["source_key"],
                    "source_revision": pending["source_revision"],
                    "workflow_run_id": pending["triage_id"],
                    "issue_number": pending["issue_number"],
                    "round": pending["round"],
                    "marker_id": marker_id,
                    "comment_url": str(result.get("url") or ""),
                    "answers_ref": answer_ref,
                },
                causation_id=causation_id or None,
                correlation_id=pending["triage_id"],
            )
        retriage = ExternalIssueIngressService(
            self.state_dir,
            self.config,
            project_root=self.project_root,
        ).start_manual_triage(int(pending["issue_number"]))
        return {
            "ok": True,
            "status": "published_and_retriaged" if retriage.queued else "published_refresh_pending",
            "comment_url": str(result.get("url") or ""),
            "replayed": result.get("replayed") == "true",
            "triage": {
                "status": retriage.status,
                "queued": retriage.queued,
                "task_id": retriage.task_id,
                "workflow_run_id": retriage.workflow_run_id,
                "source_revision": retriage.source_revision,
            },
        }

    def github_device_start(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        pending = self._validated_submission(payload)
        if self.secrets.reveal(self._secret_key(payload)):
            return self.submit(payload, causation_id=causation_id)
        client_id = self._github_client_id()
        session_id = str(payload.get("session_id") or "").strip()
        if not client_id or not session_id:
            raise ValueError("GitHub client_id and session_id are required")
        response = self.oauth.start(client_id=client_id)
        now = datetime.now(timezone.utc)
        transaction = {
            "transaction_id": f"ghc-{secrets.token_hex(12)}",
            "session_digest": hashlib.sha256(session_id.encode()).hexdigest(),
            "client_id": client_id,
            "device_code": str(response["device_code"]),
            "user_code": str(response["user_code"]),
            "verification_uri": str(response["verification_uri"]),
            "interval": max(5, int(response.get("interval") or 5)),
            "next_poll_at": now.isoformat(),
            "expires_at": (
                now + timedelta(seconds=max(1, int(response.get("expires_in") or 900)))
            ).isoformat(),
            "pending": pending,
            "user_id": str(payload.get("user_id") or "local-user"),
            "workspace_id": str(
                payload.get("workspace_id")
                or forge_credential_workspace_id(self.project_root)
            ),
            "consumed": False,
        }
        with locked_path(self._transactions_path):
            rows = self._transactions()
            rows.append(transaction)
            self._write_transactions(rows)
        return {
            "ok": True,
            "status": "authorization_required",
            **{key: transaction[key] for key in (
                "transaction_id", "user_code", "verification_uri", "expires_at", "interval",
            )},
        }

    def github_device_poll(
        self, payload: dict[str, Any], *, causation_id: str = "",
    ) -> dict[str, Any]:
        transaction_id = str(payload.get("transaction_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        session_digest = hashlib.sha256(session_id.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        with locked_path(self._transactions_path):
            rows = self._transactions()
            transaction = next((
                row for row in rows
                if str(row.get("transaction_id") or "") == transaction_id
            ), None)
            if (
                transaction is None or bool(transaction.get("consumed"))
                or str(transaction.get("session_digest") or "") != session_digest
            ):
                raise ValueError("GitHub clarification authorization is invalid or consumed")
            if now >= datetime.fromisoformat(str(transaction["expires_at"])):
                transaction["consumed"] = True
                self._write_transactions(rows)
                return {"ok": False, "status": "expired_token"}
            next_poll = datetime.fromisoformat(str(transaction["next_poll_at"]))
            if now < next_poll:
                return {
                    "ok": False,
                    "status": "authorization_pending",
                    "retry_after": max(1, int((next_poll - now).total_seconds())),
                }
            result = self.oauth.poll(
                client_id=str(transaction["client_id"]),
                device_code=str(transaction["device_code"]),
            )
            status = str(result.get("status") or "")
            interval = int(transaction.get("interval") or 5)
            if status in {"authorization_pending", "slow_down"}:
                interval += 5 if status == "slow_down" else 0
                transaction["interval"] = interval
                transaction["next_poll_at"] = (now + timedelta(seconds=interval)).isoformat()
                self._write_transactions(rows)
                return {"ok": False, "status": status, "retry_after": interval}
            if status != "connected":
                transaction["consumed"] = True
                self._write_transactions(rows)
                return {"ok": False, "status": status or "authorization_failed"}
            result["client_id"] = str(transaction["client_id"])
            key = SecretKey(
                user_id=str(transaction["user_id"]),
                workspace_id=str(transaction["workspace_id"]),
                provider="github",
                authorization_domain=self.authorization_domain,
            )
            self.secrets.put(key, {str(k): str(v) for k, v in result.items()})
            transaction["consumed"] = True
            self._write_transactions(rows)
            pending = dict(transaction["pending"])
        return self.submit({
            **pending,
            "user_id": str(transaction["user_id"]),
            "workspace_id": str(transaction["workspace_id"]),
        }, causation_id=causation_id)

    def _validated_submission(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        issue_number = int(payload.get("issue_number") or 0)
        triage_id = str(payload.get("triage_id") or "").strip()
        mirror = IssueMirrorStore(self.state_dir).get(issue_number)
        if mirror is None or mirror.repository.casefold() != self.repository.casefold():
            raise ValueError("GitHub Issue is not available in this project mirror")
        event = next((
            row for row in reversed(self.writer.event_log.read_all())
            if row.type in {
                "external_issue.triage.needs_info",
                "external_issue.triage.needs_info_exhausted",
            }
            and str((row.payload or {}).get("source_key") or "") == mirror.issue_key
        ), None)
        if event is None or str((event.payload or {}).get("workflow_run_id") or "") != triage_id:
            raise ValueError("needs_info request is stale or no longer current")
        clarification = hydrate_external_issue_clarification(
            self.state_dir, (event.payload or {}).get("clarification_ref"),
        )
        if clarification is None:
            raise ValueError("needs_info clarification artifact is unavailable")
        if clarification.get("status") == "needs_info_exhausted":
            raise ValueError("automatic clarification limit has been reached")
        raw_answers = payload.get("answers")
        answers = dict(raw_answers) if isinstance(raw_answers, Mapping) else {}
        normalized_answers: dict[str, str] = {}
        for question in clarification.get("questions") or []:
            question_id = str(question.get("id") or "")
            raw = answers.get(question_id)
            answer = (
                "Yes" if raw is True else "No" if raw is False else str(raw or "").strip()
            )
            if question.get("required") and not answer:
                raise ValueError(f"answer is required for {question_id}")
            if str(question.get("type") or "") == "single_choice" and answer not in (
                question.get("options") or []
            ):
                raise ValueError(f"answer is invalid for {question_id}")
            normalized_answers[question_id] = answer
        return {
            "schema_version": "external-issue-clarification-answers.v1",
            "issue_number": issue_number,
            "source_key": mirror.issue_key,
            "source_revision": str(clarification.get("source_revision") or ""),
            "triage_id": triage_id,
            "task_id": str(clarification.get("task_id") or event.task_id or ""),
            "round": int(clarification.get("round") or 1),
            "questions": list(clarification.get("questions") or []),
            "answers": normalized_answers,
        }

    def _comment_body(self, pending: Mapping[str, Any], *, marker: str) -> str:
        lines = [marker, "## Information requested by ZaoFu Triage", ""]
        answers = pending.get("answers") if isinstance(pending.get("answers"), Mapping) else {}
        for question in pending.get("questions") or []:
            question_id = str(question.get("id") or "")
            lines.extend([
                f"### {question.get('question')}",
                str(answers.get(question_id) or "(No answer provided)"),
                "",
            ])
        lines.append(
            f"_Submitted from ZaoFu after Triage {pending['triage_id']} · round {pending['round']}._"
        )
        return "\n".join(lines)

    def _secret_key(self, payload: Mapping[str, Any]) -> SecretKey:
        return SecretKey(
            user_id=str(payload.get("user_id") or "local-user"),
            workspace_id=str(
                payload.get("workspace_id")
                or forge_credential_workspace_id(self.project_root)
            ),
            provider="github",
            authorization_domain=self.authorization_domain,
        )

    def _was_published(self, triage_id: str, marker_id: str) -> bool:
        return any(
            event.type == "external_issue.clarification.published"
            and str((event.payload or {}).get("workflow_run_id") or "") == triage_id
            and str((event.payload or {}).get("marker_id") or "") == marker_id
            for event in self.writer.event_log.read_all()
        )

    def _github_client_id(self) -> str:
        target = self.config.self_issue.targets.get("github")
        return str(
            getattr(target, "oauth_client_id", "")
            or os.environ.get("ZF_GITHUB_APP_CLIENT_ID")
            or ""
        ).strip()

    @property
    def _transactions_path(self) -> Path:
        return self.state_dir / "external-issues" / "clarification-oauth.json"

    def _transactions(self) -> list[dict[str, Any]]:
        if not self._transactions_path.exists():
            return []
        value = json.loads(self._transactions_path.read_text(encoding="utf-8") or "[]")
        return [dict(row) for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    def _write_transactions(self, rows: list[dict[str, Any]]) -> None:
        atomic_write_text(
            self._transactions_path,
            json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


__all__ = ["ExternalIssueClarificationService"]
