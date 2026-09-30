"""Feishu cards and idempotent projection for the Self-Issue lifecycle.

This module owns presentation only.  Intake, Draft and publication state are
read from their canonical stores; button callbacks are still handled by the
Issue adapter and the controlled-action path.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.self_issue.store import (
    IssueDraftStore,
    PublicationBatchStore,
    PublicationIntentStore,
    SelfIssueIntakeStore,
)
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.self_issue.safe_export import safe_export_obj, safe_report_text
from zf.core.security.redaction import redact_obj
from zf.integrations.feishu.callback_token import attach_action_token, sign_action
from zf.integrations.feishu.transport import FeishuMessage, FeishuTransport
from zf.integrations.feishu.reply_details import reply_details
from issue_core import JsonIssueSessionStore, SessionStatus
from zf.core.self_issue.report_projection import stage_report_contract


def _activity_timeline(working: Any) -> str:
    """Render step/status/command metadata only; never embed tool output."""
    workflow = dict(getattr(working, "workflow", {}) or {})
    entries = [item for item in workflow.get("activity_log") or [] if isinstance(item, dict)]
    if not entries:
        return ""
    lines = []
    for item in entries:
        status = safe_report_text(str(item.get("status") or "completed").lower(), preserve_personal_paths=True)
        label = safe_report_text(str(item.get("label") or item.get("kind") or "分析步骤"), preserve_personal_paths=True)
        tool = safe_report_text(str(item.get("tool") or ""), preserve_personal_paths=True)
        if tool:
            label += f"（{tool}）"
        line = f"• 动作：{label}；状态：{status}"
        command = safe_report_text(str(item.get("command") or ""), preserve_personal_paths=True)
        if command:
            line += f"\n命令：{command}"
        lines.append(line)
    # Bound the serialized payload, not character count. Keep whole recent
    # records instead of cutting through a command or silently hiding steps.
    selected: list[str] = []
    used = 0
    for line in reversed(lines):
        size = len(json.dumps(line, ensure_ascii=False).encode())
        if used + size > 12000:
            if not selected:
                selected.append(line[:1500] + "\n…【此动作或命令过长，请在 Web 查看完整记录。】")
            break
        selected.append(line)
        used += size
    header = "分析过程："
    if len(selected) < len(lines):
        header += f"（共 {len(lines)} 条，较早记录因卡片容量限制省略，可在 Web 查看。）"
    return header + "\n" + "\n\n".join(reversed(selected))


def _report_parts(working: Any) -> list[str]:
    """Render the same normalized report contract used by the Web workspace."""
    report = stage_report_contract(
        getattr(working, "report", {}) or {},
        getattr(working, "fields", {}) or {},
        getattr(working, "gaps", ()) or (),
    )
    report = safe_export_obj(redact_obj(report))
    if getattr(working, "evidences", ()):
        report["evidences"] = safe_export_obj(redact_obj(list(working.evidences)))
    labels = {
        "bug_description": "问题描述", "actual_behavior": "实际行为",
        "expected_behavior": "期望行为", "classification": "分类",
        "severity": "严重程度", "impact_scope": "影响范围",
        "component": "组件", "reproduction_status": "复现状态",
        "assessment_confidence": "评估置信度", "analysis": "分析结论",
        "evidences": "证据", "investigation_gaps": "调查缺口",
        "downstream_verification_tasks": "后续验证任务", "environment": "环境",
        "reproduction_steps": "复现步骤",
    }
    lines = ["Issue 报告："]
    for key in list(labels) + [key for key in report if key not in labels]:
        value = report.get(key)
        if value in (None, "", [], {}):
            continue
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        lines.append(f"{labels.get(key, key)}：\n{rendered}")
    body = "\n\n".join(lines)
    # Plain-text report blocks can split even a single large field without
    # breaking Markdown or silently losing evidence. Stay below card byte caps.
    return [body[index:index + 3500] for index in range(0, len(body), 3500)]


def _record_projection_failure(
    state_dir: Path,
    *,
    card_key: str,
    operation: str,
    error: Exception,
) -> None:
    """Audit provider failures without changing canonical business state."""
    try:
        EventWriter(event_log_from_project(Path(state_dir))).emit(
            "feishu.notification.failed",
            actor="feishu-projector",
            payload={
                "schema_version": "feishu-notification-failure.v1",
                "card_key": card_key,
                "operation": operation,
                "reason": str(error)[:500],
            },
        )
    except Exception:
        # A projection failure must never bring down the bridge.
        pass


def _github_device_transaction(state_dir: Path, *, draft_id: str, batch_id: str) -> dict[str, Any] | None:
    path = Path(state_dir) / "self-issues" / "github-device-transactions.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    transaction = next((row for row in reversed(rows)
                 if isinstance(row, dict)
                 and str(row.get("draft_id") or "") == draft_id
                 and str(row.get("batch_id") or "") == batch_id
                 and not bool(row.get("consumed"))), None)
    if not transaction or _expired(str(transaction.get("expires_at") or "")):
        return None
    return {key: transaction.get(key, "") for key in (
        "verification_uri", "user_code", "transaction_id", "expires_at", "next_poll_at",
    )}


def _expired(value: str) -> bool:
    if not value:
        return False
    try:
        return datetime.fromisoformat(value) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


def _append_github_device_card(card: dict[str, Any], state_dir: Path, batch: Any, draft: Any) -> None:
    if (str(getattr(batch, "status", "")) != "confirmed"
        or _expired(str(getattr(batch, "confirmation_expires_at", "")))
        or "github" not in list(getattr(batch, "selected_providers", ()) or ())):
        return
    transaction = _github_device_transaction(
        state_dir, draft_id=str(getattr(draft, "draft_id", "")), batch_id=str(getattr(batch, "batch_id", "")),
    )
    if transaction:
        uri = safe_report_text(str(transaction.get("verification_uri") or ""))
        code = safe_report_text(str(transaction.get("user_code") or ""))
        card.setdefault("elements", []).append({"tag": "div", "text": {"tag": "lark_md", "content": (
            "GitHub 尚未授权。请打开授权地址并输入设备码：\n"
            f"{uri}\n设备码：`{code}`\n有效期至：{transaction['expires_at']}\n"
            "请由发起授权的用户完成授权，再点击“检查授权并发布”。"
        )}})
        card.setdefault("elements", []).append({"tag": "action", "actions": [{
            "tag": "button", "text": {"tag": "plain_text", "content": "检查授权并发布"},
            "type": "primary", "value": {"action": (
                f"self-issue-github-device-poll:{draft.draft_id}:"
                f"{transaction.get('transaction_id', '')}"
            )},
        }]})
        # A pending poll consumes its nonce; next_poll_at changes only after a
        # real polling attempt, allowing the updated card to mint a new token.
        card["elements"].append({"tag": "note", "elements": [{
            "tag": "plain_text", "content": f"下次检查时间：{transaction['next_poll_at']}",
        }]})


def _append_publication_feedback(card: dict[str, Any], state_dir: Path, receive_id: str, draft: Any) -> str:
    from zf.integrations.feishu.publication_feedback import feedback_text, read_publication_feedback
    feedback = read_publication_feedback(state_dir, receive_id, str(getattr(draft, "draft_id", "")))
    status = str(feedback.get("status") or "")
    if status and (not feedback.get("batch_id") or feedback.get("batch_id") == card.get("_batch_id")):
        message = feedback_text(status)
        if feedback.get("reason"):
            message += "\n" + safe_report_text(str(feedback["reason"]))
        card["elements"].append({"tag": "div", "text": {"tag": "plain_text", "content": message}})
        return str(feedback.get("source_event_id") or "")
    return ""


def _publication_preview_parts(state_dir: Path, batch: Any) -> list[str]:
    """Show the exact immutable provider payload that confirmation approves."""
    store = PublicationIntentStore(state_dir / "self-issues/publication-intents.json")
    sections = []
    for provider, intent_id in batch.intent_ids.items():
        intent = store.get(intent_id)
        if intent is None:
            continue
        sections.append(safe_report_text(
            f"发布预览 · {provider}\n"
            f"目标仓库：{intent.target_binding.get('project', '')}\n"
            f"标题：{intent.payload.get('title', '')}\n\n{intent.payload.get('body', '')}"
        ))
    body = "\n\n".join(sections)
    return [body[index:index + 3500] for index in range(0, len(body), 3500)]


def build_self_issue_card(
    record: Any,
    *,
    kind: str,
    web_base_url: str = "",
    batch: Any | None = None,
    intent_id: str = "",
    questions: list[dict[str, Any]] | None = None,
    answers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render a compact Intake/Draft card; bodies and evidence stay sidecar-backed."""
    if kind == "intake":
        identifier = str(record.intake_id)
        status = str(record.status)
        title = "Self-Issue Intake"
        body = (
            f"状态：{status}\n步骤：{record.current_step}/7\n"
            f"附件：{len(record.attachment_refs)}\n"
            f"Intake ID：{identifier}\n"
            "上传附件：@Kanban Agent 发送 "
            f"/zf self-issue-intake-attachment-add {identifier} 并附文件"
        )
        action = {"action": f"self-issue-submit:{identifier}:{record.revision}"}
        label = "提交 Intake"
    else:
        identifier = str(record.draft_id)
        status = str(record.publication_state)
        title = "Self-Issue Draft"
        body = (
            f"**{record.title[:240]}**\n状态：`{status}`\n"
            f"证据：`{record.evidence_status}`\n版本：`{record.revision}`"
        )
        published_ref = getattr(record, "published_issue_ref", {}) or {}
        published_url = str(published_ref.get("url") or "").strip()
        if published_url and status in {"published", "partially_published"}:
            body += f"\n已发布：{published_url[:500]}"
        ready = str(record.evidence_status) in {"completed", "interrupted", "failed"}
        action_name = "self-issue-preview" if ready else "self-issue-evidence-start"
        action = {"action": f"{action_name}:{identifier}:{record.revision}"}
        label = "生成发布预览" if ready else "启动证据评估"
        if not ready:
            body += "\n请先完成证据评估，评估完成后才能生成发布预览。"

    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body}},
    ]
    if web_base_url:
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开 Web"},
                        "type": "default",
                        "url": (
                            f"{web_base_url.rstrip('/')}/?page=orchestrator"
                            f"&self_issue={identifier}"
                        ),
                    },
                ],
            },
        )
    if (
        status in {"collecting", "awaiting_user_review", "submitted"}
        and not (kind == "intake" and questions)
    ) or (kind == "draft" and status == "draft" and (
        str(record.evidence_status) in {"completed", "interrupted", "failed"}
        or str(record.evidence_status) in {"pending", "waiting_for_runtime"}
    )):
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": label},
                        "type": "primary",
                        "value": action,
                    },
                ],
            },
        )
    if kind == "intake" and questions:
        # Current tenant clients downgrade cards containing the legacy form
        # element to an upgrade placeholder. Keep this card v1-compatible and
        # collect answers through an explicit JSON command instead.
        command = (
            f"/zf self-issue-intake-save {identifier} {record.revision} "
            "'{\"title\":\"...\",\"bug_description\":\"...\","
            "\"reproduction_steps\":\"...\",\"zaofu_version\":\"...\"}'"
        )
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "当前客户端不支持卡片内嵌表单。请 @Kanban Agent 发送：\n"
                        f"{command}\n"
                        "至少填写 title、bug_description、reproduction_steps、"
                        "zaofu_version；保存后再点击“提交 Intake”。"
                    ),
                },
            },
        )
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "提交 Intake"},
                        "type": "primary",
                        "value": {
                            "action": f"self-issue-submit:{identifier}:{record.revision}",
                        },
                    },
                ],
            },
        )
    if kind == "draft" and batch is not None:
        batch_status = str(getattr(batch, "status", "") or "")
        batch_id = str(getattr(batch, "batch_id", "") or "")
        digest = str(getattr(batch, "payload_digest", "") or "")
        intent_id = intent_id or str(next(iter(getattr(batch, "intent_ids", {}).values()), ""))
        controls: list[dict[str, Any]] = []
        expired = batch_status == "confirmed" and _expired(str(getattr(batch, "confirmation_expires_at", "")))
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": (
            f"发布批次：{batch_id}\n"
            f"发布状态：{batch_status}\n"
            + ("发布确认已过期；尚未发布。请重新生成预览并确认。" if expired else
               "预览和确认不会创建远端 Issue，执行发布成功后显示 Issue 链接。")
        )}})
        if expired:
            controls.append({"tag": "button", "text": {"tag": "plain_text", "content": "重新生成发布预览"},
                             "type": "primary", "value": {"action": f"self-issue-preview:{identifier}:{record.revision}"}})
        elif batch_status == "previewed" and batch_id and digest:
            controls.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "确认发布"},
                    "type": "primary",
                    "value": {"action": f"self-issue-confirm:{batch_id}:{digest}"},
                },
            )
        elif batch_status == "confirmed" and batch_id and getattr(batch, "confirmation_id", ""):
            confirmation_id = str(getattr(batch, "confirmation_id", "") or "")
            controls.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "执行发布"},
                    "type": "primary",
                    "value": {
                        "action": (
                            f"self-issue-publish:{batch_id}:"
                            f"{batch.confirmation_id}"
                        ),
                    },
                },
            )
            if "github" in list(getattr(batch, "selected_providers", ()) or ()):
                analysis = getattr(record, "analysis", {}) or {}
                session_id = (str(analysis.get("portable_session_id") or "")
                              if isinstance(analysis, dict) else "") or record.draft_id
                controls.append({
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "GitHub 授权并继续发布"},
                        "type": "default",
                        "value": {"action": (
                            f"self-issue-github-device-start:{session_id}:{record.draft_id}:"
                            f"{batch_id}:{confirmation_id}"
                        )},
                })
        elif batch_status in {"publishing", "outcome_unknown"} and intent_id:
            controls.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "恢复发布状态"},
                    "type": "danger",
                    "value": {"action": f"self-issue-recover:{intent_id}"},
                },
            )
        if controls:
            elements.append({"tag": "action", "actions": controls})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": (
                "orange"
                if status in {"draft", "collecting", "awaiting_user_review"}
                else "blue"
            ),
        },
        "elements": elements,
        "_card_key": f"self-issue-{kind}-{identifier}",
    }


def build_self_issue_session_card(
    session: Any,
    working: Any,
    *,
    web_base_url: str = "",
    answer_token: str = "",
    confirm_token: str = "",
) -> dict[str, Any]:
    """Render the current `/issue` Portable Session without mutating it."""
    session_id = str(session.session_id)
    status = str(session.status.value if hasattr(session.status, "value") else session.status)
    workflow = dict(getattr(working, "workflow", {}) or {})
    report = stage_report_contract(
        getattr(working, "report", {}) or {},
        getattr(working, "fields", {}) or {},
        getattr(working, "gaps", ()) or (),
    )
    body = (
        f"状态：{status}\n阶段：{str(workflow.get('label') or workflow.get('phase') or status)}\n"
        f"版本：{session.revision}\nSession ID：{session_id}\n"
        f"附件：{len(session.attachments)}\n"
        f"问题：{safe_report_text(str(session.raw_report))[:500]}"
    )
    question = str(session.user_question or "").strip()
    if question:
        body += f"\n待补充：{question[:1000]}"
    if report.get("title"):
        body = f"**{safe_report_text(str(report['title']))[:240]}**\n" + body
    timeline = _activity_timeline(working)
    if session.publication_draft_id:
        body += f"\n发布草稿：{session.publication_draft_id}（预览、确认后再发布）"
    body = safe_report_text(body)
    if len(body) > 3000:
        body = body[:2950] + "\n（会话概况已缩短；步骤列表仍在分析过程面板中。）"
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": body}},
    ]
    if timeline:
        elements.append(reply_details("分析过程", [
            {"tag": "div", "text": {"tag": "plain_text", "content": timeline}},
        ]))
    if status in {SessionStatus.READY_FOR_CONFIRMATION.value, SessionStatus.COMPLETED.value}:
        parts = _report_parts(working)
        elements.append(reply_details("Issue 报告", [
            {"tag": "div", "text": {"tag": "plain_text", "content": parts[0]}},
        ]))
        if len(parts) > 1:
            elements.append({"tag": "div", "text": {"tag": "plain_text", "content": (
                f"报告共 {len(parts)} 段；报告版本：{hashlib.sha256(''.join(parts).encode()).hexdigest()[:12]}。"
                "其余内容见同一群内相同 Session ID、报告版本的续页。"
            )}})
    actions: list[dict[str, Any]] = []
    if web_base_url:
        actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "打开 Web"},
            "type": "default", "url": f"{web_base_url.rstrip('/')}/?page=orchestrator&self_issue={session_id}",
        })
    if status == SessionStatus.WAITING_FOR_USER.value:
        token_arg = f" --token {answer_token}" if answer_token else ""
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": (
            "请 @Kanban Agent 发送以下命令，替换回答内容：\n"
            f"/zf self-issue-session-answer {session_id}{token_arg} \"你的补充回答\""
            "\n需要补文件时，先上传文件，再引用该文件消息发送上面的命令。"
        )}})
    elif status == SessionStatus.READY_FOR_CONFIRMATION.value:
        actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "确认报告"},
            "type": "primary", "value": {"action": f"self-issue-session-confirm:{session_id}"},
        })
        if confirm_token:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": (
                "如果点击按钮提示回调服务未在线，请直接发送：\n"
                f"`/zf self-issue-session-confirm {session_id} --token {confirm_token}`"
            )}})
    elif status == SessionStatus.COMPLETED.value and not session.publication_draft_id:
        actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "生成发布草稿"},
            "type": "primary", "value": {"action": f"self-issue-session-materialize:{session_id}"},
        })
    elif status in {SessionStatus.CREATED.value, SessionStatus.INVESTIGATING.value}:
        actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "中断调查"},
            "type": "danger", "value": {"action": f"self-issue-session-pause:{session_id}"},
        })
    elif status in {SessionStatus.PAUSED.value, SessionStatus.FAILED.value}:
        actions.append({
            "tag": "button", "text": {"tag": "plain_text", "content": "恢复调查"},
            "type": "primary", "value": {"action": f"self-issue-session-retry:{session_id}"},
        })
    if actions:
        elements.append({"tag": "action", "actions": actions})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": "Issue Agent"}, "template": "red" if status == "failed" else "blue"},
        "elements": elements,
        "_card_key": f"self-issue-session-{session_id}",
    }


def push_self_issue_cards_once(
    state_dir: Path,
    transport: FeishuTransport,
    *,
    receive_id: str,
    receive_id_type: str = "chat_id",
    web_base_url: str = "",
    action_secret: bytes | None = None,
    action_ttl_seconds: int = 86400,
    now: float | None = None,
) -> dict[str, Any]:
    """Project Sessions and compatible Intake/Draft cards with durable idempotency."""
    if not receive_id:
        return {"sent": [], "updated": [], "skipped": ["missing_receive_id"]}
    ledger_path = Path(state_dir) / "integrations" / "feishu" / "self_issue_card_ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger = ledger if isinstance(ledger, dict) else {}
    except (OSError, ValueError):
        ledger = {}

    records: list[tuple[str, Any, Any | None]] = []
    session_root = Path(state_dir) / "self-issues" / "portable-sessions"
    session_store = JsonIssueSessionStore(session_root)
    session_rows: list[tuple[str, Any, Any]] = []
    for path in session_root.glob("*.session.json"):
        try:
            session = session_store.load_session(path.name.removesuffix(".session.json"))
            working = session_store.load_working_set(session.session_id)
        except (OSError, KeyError, ValueError):
            continue
        session_rows.append((session.updated_at, session, working))
    for _, session, working in sorted(session_rows, key=lambda row: row[0])[-10:]:
        records.append(("session", session, working))
        if session.status in {SessionStatus.READY_FOR_CONFIRMATION, SessionStatus.COMPLETED}:
            parts = _report_parts(working)
            report_digest = hashlib.sha256("".join(parts).encode()).hexdigest()[:12]
            for index, part in enumerate(parts[1:], start=2):
                records.append(("session-report", {
                    "config": {"wide_screen_mode": True, "update_multi": True},
                    "header": {"title": {"tag": "plain_text", "content": f"Issue 报告（{index}/{len(parts)}）"}},
                    "elements": [
                        {"tag": "div", "text": {"tag": "plain_text", "content": (
                            f"Session ID：{session.session_id}\n报告版本：{report_digest}"
                        )}},
                        reply_details("报告正文", [
                            {"tag": "div", "text": {"tag": "plain_text", "content": part}},
                        ]),
                    ],
                    "_card_key": f"self-issue-session-{session.session_id}-report-{report_digest}-{index}",
                }, None))
    intake = SelfIssueIntakeStore(Path(state_dir) / "self-issues" / "intakes.json").latest()
    draft = IssueDraftStore(Path(state_dir) / "self-issues" / "drafts.json").latest()
    if intake is not None:
        records.append(("intake", intake, None))
    if draft is not None:
        records.append(("draft", draft, None))
        batch = PublicationBatchStore(Path(state_dir) / "self-issues/publication-batches.json").latest_for_draft(draft.draft_id)
        if batch is not None:
            parts = _publication_preview_parts(Path(state_dir), batch)
            for index, part in enumerate(parts[1:], start=2):
                records.append(("publication-preview", {
                    "config": {"wide_screen_mode": True, "update_multi": True},
                    "header": {"title": {"tag": "plain_text", "content": f"发布预览（{index}/{len(parts)}）"}},
                    "elements": [
                        {"tag": "div", "text": {"tag": "plain_text", "content": (
                            f"Draft：{draft.draft_id}\n"
                            f"发布批次：{batch.batch_id}"
                        )}},
                        reply_details("发布预览正文", [
                            {"tag": "div", "text": {"tag": "plain_text", "content": part}},
                        ]),
                    ],
                    "_card_key": f"self-issue-preview-{batch.batch_id}-{index}",
                }, None))

    sent: list[str] = []
    updated: list[str] = []
    failed: list[str] = []
    for kind, record, working in records:
        effective_now = time.time() if now is None else now
        feedback_event_id = ""
        answer_token = ""
        if (
            action_secret
            and kind == "session"
            and str(record.status.value if hasattr(record.status, "value") else record.status)
            == SessionStatus.WAITING_FOR_USER.value
        ):
            answer_token = sign_action(
                action_secret,
                action="self-issue-session-answer",
                target=record.session_id,
                chat_id=receive_id,
                ttl_seconds=action_ttl_seconds,
                now=effective_now,
            )
        confirm_token = ""
        if (
            action_secret
            and kind == "session"
            and str(record.status.value if hasattr(record.status, "value") else record.status)
            == SessionStatus.READY_FOR_CONFIRMATION.value
        ):
            confirm_token = sign_action(
                action_secret,
                action="self-issue-session-confirm",
                target=record.session_id,
                chat_id=receive_id,
                ttl_seconds=action_ttl_seconds,
                now=effective_now,
            )
        if kind == "session":
            card = build_self_issue_session_card(
                record, working, web_base_url=web_base_url,
                answer_token=answer_token, confirm_token=confirm_token,
            )
        else:
            card = record if kind in {"session-report", "publication-preview"} else None
        batch = None
        intent_id = ""
        questions = None
        answers = None
        if kind == "draft":
            batch = PublicationBatchStore(
                Path(state_dir) / "self-issues" / "publication-batches.json",
            ).latest_for_draft(record.draft_id)
            if batch is not None:
                intent_id = str(next(iter(batch.intent_ids.values()), ""))
        elif kind == "intake":
            try:
                from zf.core.self_issue.intake import intake_questions
                from zf.runtime.sidecar_refs import hydrate_sidecar_ref

                questions = intake_questions()
                if record.answers_ref:
                    hydrated = hydrate_sidecar_ref(
                        Path(state_dir),
                        record.answers_ref,
                        purpose="self_issue_intake_answers",
                        actor="feishu-card-projector",
                    )
                    body = hydrated.payload if isinstance(hydrated.payload, dict) else {}
                    answers = body.get("answers") if isinstance(body, dict) else {}
            except Exception:
                questions = None
                answers = None

        if card is None:
            card = build_self_issue_card(
                record,
                kind=kind,
                web_base_url=web_base_url,
                batch=batch,
                intent_id=intent_id,
                questions=questions,
                answers=answers,
            )
        if kind == "draft" and batch is not None:
            card["_batch_id"] = batch.batch_id
            _append_github_device_card(card, Path(state_dir), batch, record)
            feedback_event_id = _append_publication_feedback(card, Path(state_dir), receive_id, record)
            card.pop("_batch_id", None)
            parts = _publication_preview_parts(Path(state_dir), batch)
            if parts:
                card["elements"].append(reply_details("发布预览", [{"tag": "div", "text": {"tag": "plain_text", "content": (
                    f"发布预览（1/{len(parts)}）；续页以同一发布批次标识。\n\n{parts[0]}"
                )}}]))
        semantic_card = card
        if kind == "session" and (answer_token or confirm_token):
            semantic_card = build_self_issue_session_card(
                record, working, web_base_url=web_base_url,
            )
        semantic_digest = hashlib.sha256(
            json.dumps(
                {"card": semantic_card, "feedback_event_id": feedback_event_id} if feedback_event_id else semantic_card,
                ensure_ascii=False, sort_keys=True,
            ).encode(),
        ).hexdigest()
        key = str(card.get("_card_key") or "")
        entry = ledger.get(key) if isinstance(ledger.get(key), dict) else {}
        token_valid = not action_secret
        if action_secret:
            try:
                token_valid = float(entry.get("token_expires_at") or 0) > effective_now
            except (TypeError, ValueError):
                token_valid = False
        if entry.get("message_id") and entry.get("semantic_digest") == semantic_digest and token_valid:
            continue
        if action_secret:
            card = attach_action_token(
                card,
                secret=action_secret,
                chat_id=receive_id,
                ttl_seconds=action_ttl_seconds,
                now=effective_now,
            )
            if confirm_token:
                # The button and text fallback are one capability, so using
                # either consumes the same nonce and prevents cross-form replay.
                for row in card["elements"]:
                    for button in row.get("actions", []):
                        value = button.get("value", {})
                        if value.get("action") == f"self-issue-session-confirm:{record.session_id}":
                            value["t"] = confirm_token
        digest = hashlib.sha256(
            json.dumps(card, ensure_ascii=False, sort_keys=True).encode(),
        ).hexdigest()
        if not entry.get("message_id"):
            try:
                message_id = transport.send_card(
                    FeishuMessage(
                        chat_id=receive_id,
                        content=json.dumps(card, ensure_ascii=False),
                        msg_type="interactive",
                        receive_id_type=receive_id_type,
                    ),
                )
            except Exception as exc:
                _record_projection_failure(
                    state_dir, card_key=key, operation="send", error=exc,
                )
                failed.append(key)
                continue
            if message_id:
                ledger[key] = {
                    "message_id": str(message_id),
                    "digest": digest,
                    "semantic_digest": semantic_digest,
                    "token_expires_at": (
                        effective_now + action_ttl_seconds if action_secret else 0
                    ),
                }
                sent.append(key)
        elif entry.get("digest") != digest:
            try:
                transport.update_card(str(entry["message_id"]), card)
            except Exception as exc:
                _record_projection_failure(
                    state_dir, card_key=key, operation="update", error=exc,
                )
                failed.append(key)
                continue
            ledger[key] = {
                **entry,
                "digest": digest,
                "semantic_digest": semantic_digest,
                "token_expires_at": (
                    effective_now + action_ttl_seconds if action_secret else 0
                ),
            }
            updated.append(key)
    with locked_path(ledger_path):
        atomic_write_text(
            ledger_path,
            json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    return {"sent": sent, "updated": updated, "failed": failed, "ledger": ledger}


__all__ = ["build_self_issue_card", "build_self_issue_session_card", "push_self_issue_cards_once"]
