"""Sanitized publication action feedback; canonical state stays in the Kernel."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.self_issue.safe_export import safe_report_text
from zf.core.self_issue.store import PublicationBatchStore
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


PUBLICATION_COMMANDS = frozenset({
    "self-issue-preview", "self-issue-confirm", "self-issue-publish", "self-issue-recover",
    "self-issue-github-device-start", "self-issue-github-device-poll",
})


def github_session_binding(envelope: Any, draft_id: str) -> str:
    return hashlib.sha256(json.dumps([
        "feishu-github", envelope.chat_id, envelope.user_id, draft_id,
    ]).encode()).hexdigest()


def _index_path(state_dir: Path, chat_id: str, draft_id: str) -> Path:
    key = hashlib.sha256(json.dumps([chat_id, draft_id]).encode()).hexdigest()
    return state_dir / "integrations/feishu/publication-feedback" / f"{key}.json"


def read_publication_feedback(state_dir: Path, chat_id: str, draft_id: str) -> dict[str, Any]:
    try:
        ref = json.loads(_index_path(state_dir, chat_id, draft_id).read_text())
        body = hydrate_sidecar_ref(state_dir, ref, purpose="feishu_publication_feedback").payload
        return body if isinstance(body, dict) else {}
    except (OSError, ValueError):
        return {}


def record_publication_feedback(
    state_dir: Path, envelope: Any, payload: dict[str, Any], result: dict[str, Any], source_event_id: str,
) -> None:
    if envelope.command not in PUBLICATION_COMMANDS:
        return
    batch_id = str(result.get("batch_id") or payload.get("batch_id") or "")
    draft_id = str(result.get("draft_id") or payload.get("draft_id") or "")
    if not draft_id and batch_id:
        batch = PublicationBatchStore(state_dir / "self-issues/publication-batches.json").get(batch_id)
        draft_id = batch.draft_id if batch else ""
    if not draft_id:
        return
    # Never persist the arbitrary action response or OAuth credentials here.
    body = {"command": envelope.command, "draft_id": draft_id, "batch_id": batch_id,
            "status": str(result.get("status") or "unknown"), "source_event_id": source_event_id,
            "reason": safe_report_text(str(result.get("reason") or ""))[:500]}
    ref = write_immutable_json_sidecar(
        state_dir, body, root="feishu/publication-feedback", kind="feishu_publication_feedback",
        schema_version="feishu-publication-feedback.v1", created_by="feishu-adapter",
        source_event_id=source_event_id, required=False,
    )
    atomic_write_text(_index_path(state_dir, envelope.chat_id, draft_id), json.dumps(ref))


def feedback_text(status: str) -> str:
    return {
        "previewed": "预览已生成，尚未发布；请检查正文和目标仓库后确认。",
        "confirmed": "已确认，尚未发布；请点击执行发布。",
        "authorization_required": "尚未发布到 GitHub：需要 GitHub 授权。",
        "authorization_pending": "GitHub 授权尚未完成，请在授权页面操作后再次检查。",
        "slow_down": "GitHub 要求稍后再检查授权，请等待至少 10 秒。",
        "access_denied": "GitHub 授权被拒绝，尚未发布。可重新发起授权。",
        "expired_token": "GitHub 授权已过期，尚未发布。请重新发起授权。",
        "confirmation_expired": "发布确认已过期，尚未发布。请重新生成预览并确认。",
        "published": "已发布，Issue 链接见卡片。",
    }.get(status, f"操作结果：{status}")
