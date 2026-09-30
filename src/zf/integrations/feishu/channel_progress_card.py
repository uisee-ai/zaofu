"""Exact-origin Feishu progress cards for Channel-to-delivery handoff.

The card is a restart-safe projection over canonical events. It does not own a
workflow state machine: every button is resolved again against EventLog and
then either requests one controlled Channel action or asks the Kanban Agent to
prepare the next Plan.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.integrations.feishu.transport import FeishuMessage, MockFeishuTransport
from zf.runtime.channel_owner_authority import channel_owner_authority_error
from zf.runtime.channel_projection import project_channel


FINALIZE_COMMAND = "channel-progress-finalize"
CONFIRM_COMMAND = "channel-progress-confirm"
CREATE_TASK_COMMAND = "channel-progress-create-task"
PLAN_WORKFLOW_COMMAND = "channel-progress-plan-workflow"
CHANNEL_PROGRESS_COMMANDS = frozenset(
    {
        FINALIZE_COMMAND,
        CONFIRM_COMMAND,
        CREATE_TASK_COMMAND,
        PLAN_WORKFLOW_COMMAND,
    }
)

_LEDGER_SCHEMA_VERSION = "feishu-channel-progress-ledger.v1"
_STAGE_ORDER = {
    "discussing": 0,
    "awaiting_finalize": 1,
    "drafting": 2,
    "awaiting_owner": 3,
    "prd_confirmed": 4,
    "task_created": 5,
    "workflow_terminal": 6,
    "delivery_terminal": 7,
    "failed": 0,
}


def progress_target(channel_id: str, thread_id: str = "main", task_id: str = "") -> str:
    return "~".join((str(channel_id), str(thread_id or "main"), str(task_id)))


def parse_progress_target(target: str) -> tuple[str, str, str]:
    parts = str(target or "").split("~", 2)
    if len(parts) < 2:
        return "", "", ""
    return parts[0], parts[1] or "main", parts[2] if len(parts) > 2 else ""


def fold_channel_progress(events: list[Any]) -> dict[str, dict[str, Any]]:
    """Fold exact Feishu-origin Channels into their current guided stage."""

    items: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = (
            event.payload if isinstance(getattr(event, "payload", None), dict) else {}
        )
        if str(getattr(event, "type", "") or "") != "channel.created":
            continue
        origin = (
            payload.get("origin_binding")
            if isinstance(payload.get("origin_binding"), dict)
            else {}
        )
        channel_id = str(payload.get("channel_id") or "").strip()
        thread_id = str(payload.get("thread_id") or origin.get("thread_id") or "main")
        chat_id = str(origin.get("chat_id") or "").strip()
        origin_message_id = str(origin.get("origin_message_id") or "").strip()
        if (
            not channel_id
            or str(origin.get("surface") or "") != "feishu"
            or not chat_id
            or not origin_message_id
        ):
            continue
        key = progress_target(channel_id, thread_id)
        items[key] = {
            "channel_id": channel_id,
            "channel_name": str(
                payload.get("name") or payload.get("channel_name") or channel_id
            ),
            "thread_id": thread_id,
            "owner_actor_ref": str(payload.get("owner_actor_ref") or ""),
            "origin_binding": dict(origin),
            "stage": "discussing",
            "status": "Channel 已创建，正在讨论。",
            "event_id": str(getattr(event, "id", "") or ""),
            "task_id": "",
            "artifact_ref": "",
            "artifact_digest": "",
            "prd_revision": 0,
            "summary": "",
            "product_mode": "conversation",
        }

    for event in events:
        payload = (
            event.payload if isinstance(getattr(event, "payload", None), dict) else {}
        )
        event_type = str(getattr(event, "type", "") or "")
        channel_id = str(
            payload.get("channel_id") or getattr(event, "correlation_id", "") or ""
        )
        thread_id = str(payload.get("thread_id") or "main")
        item = items.get(progress_target(channel_id, thread_id))
        if item is None:
            continue
        update: dict[str, Any] | None = None
        if event_type == "channel.discussion.started":
            update = {
                "stage": "discussing",
                "status": "Channel 已创建，正在讨论。",
                "product_mode": str(payload.get("product_mode") or "conversation"),
            }
        elif event_type == "channel.agent.reply.completed":
            if str(item.get("product_mode") or "conversation") == "multi_lens":
                update = {
                    "stage": "discussing",
                    "status": "多视角讨论正在收敛。",
                }
            else:
                update = {
                    "stage": "awaiting_finalize",
                    "status": "讨论已完成，等待生成 PRD。",
                }
        elif event_type == "channel.agent.reply.failed":
            update = {
                "stage": "failed",
                "status": "Agent 回复失败，请在 Channel 中查看详情后重试。",
            }
        elif event_type == "channel.synthesis.requested":
            update = {
                "stage": "drafting",
                "status": "已请求生成 PRD 草案。",
            }
        elif event_type in {"channel.synthesis.proposed", "channel.consensus.proposed"}:
            update = {
                "stage": "awaiting_owner",
                "status": "PRD 草案已就绪，等待确认。",
                "artifact_ref": str(
                    payload.get("artifact_ref")
                    or payload.get("prd_ref")
                    or item["artifact_ref"]
                ),
                "artifact_digest": str(
                    payload.get("artifact_digest")
                    or payload.get("prd_digest")
                    or item["artifact_digest"]
                ),
                "prd_revision": _safe_int(
                    payload.get("prd_revision")
                    or payload.get("revision")
                    or item["prd_revision"]
                ),
                "summary": str(payload.get("summary") or item["summary"]),
            }
        elif event_type == "channel.consensus.reached":
            update = {
                "stage": "prd_confirmed",
                "status": "PRD 已确认，等待创建任务。",
                "artifact_ref": str(
                    payload.get("prd_ref")
                    or payload.get("artifact_ref")
                    or item["artifact_ref"]
                ),
                "artifact_digest": str(
                    payload.get("prd_digest")
                    or payload.get("artifact_digest")
                    or item["artifact_digest"]
                ),
                "prd_revision": _safe_int(
                    payload.get("prd_revision") or item["prd_revision"]
                ),
            }
        elif event_type == "channel.result.receipt.recorded":
            kind = str(payload.get("receipt_kind") or "")
            if kind == "task_created":
                update = {
                    "stage": "task_created",
                    "status": "任务已创建，等待规划工作流。",
                    "task_id": str(payload.get("task_id") or ""),
                }
            elif kind == "workflow_terminal":
                update = {
                    "stage": "workflow_terminal",
                    "status": f"工作流已结束，状态：{str(payload.get('status') or 'available')}。",
                    "task_id": str(payload.get("task_id") or item["task_id"]),
                }
            elif kind == "delivery_terminal":
                update = {
                    "stage": "delivery_terminal",
                    "status": f"交付已结束，状态：{str(payload.get('status') or 'available')}。",
                    "task_id": str(payload.get("task_id") or item["task_id"]),
                }
        if update is None:
            continue
        current_rank = _STAGE_ORDER.get(str(item.get("stage") or ""), -1)
        next_rank = _STAGE_ORDER.get(str(update.get("stage") or ""), -1)
        if next_rank < current_rank and update.get("stage") not in {
            "drafting",
            "awaiting_owner",
        }:
            continue
        item.update(update)
        item["event_id"] = str(getattr(event, "id", "") or "")
    return items


def build_channel_progress_card(item: dict[str, Any]) -> dict[str, Any]:
    stage = str(item.get("stage") or "discussing")
    task_id = str(item.get("task_id") or "")
    target = progress_target(
        str(item.get("channel_id") or ""),
        str(item.get("thread_id") or "main"),
        task_id,
    )
    stage_line = "Channel  →  Discussion  →  PRD  →  Task  →  Workflow"
    details = [
        f"**{str(item.get('channel_name') or item.get('channel_id') or 'Channel')}**",
        stage_line,
        "",
        f"当前阶段：{_stage_label(stage)}",
        str(item.get("status") or ""),
    ]
    if item.get("summary"):
        details.extend(("", str(item["summary"])[:900]))
    if task_id:
        details.append(f"Task: `{task_id}`")
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "\n".join(details)},
        }
    ]
    action = _stage_action(stage, target)
    if action is not None:
        elements.append({"tag": "action", "actions": [action]})
    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "仅可执行当前一步；审批 Gate 不会自动跨越。",
                }
            ],
        }
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "ZaoFu 交付进度"},
            "template": _stage_template(stage),
        },
        "elements": elements,
        "_card_key": f"channel-progress-{target}",
        "_target_chat_id": str((item.get("origin_binding") or {}).get("chat_id") or ""),
        "_target_thread_id": str(
            (item.get("origin_binding") or {}).get("origin_message_id") or ""
        ),
    }


def sync_channel_progress_cards(
    state_dir: Path,
    *,
    send_card: Callable[[dict[str, Any], dict[str, Any]], str | None],
    update_card: Callable[[str, dict[str, Any], dict[str, Any]], Any],
    ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    ledger = ledger if isinstance(ledger, dict) else {}
    entries = ledger.get("entries") if isinstance(ledger.get("entries"), dict) else {}
    sent: list[str] = []
    updated: list[str] = []
    for key, item in fold_channel_progress(events).items():
        card = build_channel_progress_card(item)
        digest = _card_digest(card)
        entry = entries.get(key) if isinstance(entries.get(key), dict) else {}
        if not entry.get("message_id"):
            message_id = send_card(item, card)
            entries[key] = {
                "message_id": str(message_id or ""),
                "digest": digest,
                "stage": str(item.get("stage") or ""),
            }
            sent.append(key)
        elif str(entry.get("digest") or "") != digest:
            update_card(str(entry["message_id"]), item, card)
            entries[key] = {
                **entry,
                "digest": digest,
                "stage": str(item.get("stage") or ""),
            }
            updated.append(key)
    return {
        "sent": sent,
        "updated": updated,
        "ledger": {"schema_version": _LEDGER_SCHEMA_VERSION, "entries": entries},
    }


def push_channel_progress_cards_once(
    state_dir: Path,
    transport: Any,
    *,
    action_secret: bytes | None = None,
    action_ttl_seconds: int = 86400,
    action_key_version: str = "1",
    now: float | None = None,
) -> dict[str, Any]:
    import time

    from zf.integrations.feishu.callback_token import attach_action_token

    state_dir = Path(state_dir)
    ledger_path = state_dir / "integrations" / "feishu" / "channel_progress_ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}
    issued_at = time.time() if now is None else now

    def prepare(item: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
        chat_id = str((item.get("origin_binding") or {}).get("chat_id") or "")
        prepared = {
            key: value for key, value in card.items() if not key.startswith("_")
        }
        if action_secret:
            attach_action_token(
                prepared,
                secret=action_secret,
                chat_id=chat_id,
                ttl_seconds=action_ttl_seconds,
                now=issued_at,
                key_version=action_key_version,
            )
        return prepared

    def send_card(item: dict[str, Any], card: dict[str, Any]) -> str | None:
        origin = (
            item.get("origin_binding")
            if isinstance(item.get("origin_binding"), dict)
            else {}
        )
        return transport.send_card(
            FeishuMessage(
                chat_id=str(origin.get("chat_id") or ""),
                thread_id=str(origin.get("origin_message_id") or ""),
                content=json.dumps(prepare(item, card), ensure_ascii=False),
                msg_type="interactive",
                receive_id_type="chat_id",
            )
        )

    def update_card(message_id: str, item: dict[str, Any], card: dict[str, Any]) -> Any:
        return transport.update_card(message_id, prepare(item, card))

    result = sync_channel_progress_cards(
        state_dir,
        send_card=send_card,
        update_card=update_card,
        ledger=ledger,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        ledger_path,
        json.dumps(result["ledger"], ensure_ascii=False, indent=2) + "\n",
    )
    return result


def handle_channel_progress_action(
    *,
    command: str,
    target: str,
    context: Any,
    user_id: str,
    chat_id: str,
    message_id: str = "",
    execute_action: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
    dispatch_kanban: Callable[[str, dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Resolve a signed click against current truth and advance one explicit step."""

    channel_id, thread_id, target_task_id = parse_progress_target(target)
    if command not in CHANNEL_PROGRESS_COMMANDS or not channel_id:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": "Invalid progress action.",
        }
    events = EventLog(Path(context.state_dir) / "events.jsonl").read_all()
    item = fold_channel_progress(events).get(progress_target(channel_id, thread_id))
    if item is None:
        return {
            "ok": False,
            "status": "not_found",
            "message": "Channel progress is unavailable.",
        }
    origin = (
        item.get("origin_binding")
        if isinstance(item.get("origin_binding"), dict)
        else {}
    )
    if str(origin.get("chat_id") or "") != str(chat_id or ""):
        return {
            "ok": False,
            "status": "origin_mismatch",
            "message": "Channel origin does not match this chat.",
        }
    actor = f"feishu:{user_id or 'unknown'}"
    expected_stage = {
        FINALIZE_COMMAND: "awaiting_finalize",
        CONFIRM_COMMAND: "awaiting_owner",
        CREATE_TASK_COMMAND: "prd_confirmed",
        PLAN_WORKFLOW_COMMAND: "task_created",
    }[command]
    if str(item.get("stage") or "") != expected_stage:
        return {
            "ok": False,
            "status": "stale_progress_action",
            "message": f"This action is stale; current stage is {str(item.get('stage') or 'unknown')}.",
        }
    channel = project_channel(Path(context.state_dir), channel_id) or {}
    capability = (
        "channel.synthesis.request"
        if command == FINALIZE_COMMAND
        else "channel.consensus.confirm"
    )
    if command in {FINALIZE_COMMAND, CONFIRM_COMMAND}:
        authority_error = channel_owner_authority_error(
            channel,
            actor=actor,
            capability=capability,
        )
        if authority_error:
            return {"ok": False, "status": "forbidden", "message": authority_error}

    if command == FINALIZE_COMMAND:
        payload = {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "reason": "Finalize the current discussion into a canonical PRD draft.",
            "member_id": actor,
        }
        return _execute_progress_action(
            context=context,
            action="channel-synthesis-request",
            payload=payload,
            actor=actor,
            execute_action=execute_action,
        )
    if command == CONFIRM_COMMAND:
        consensus = (
            channel.get("consensus", {}).get(thread_id)
            if isinstance(channel.get("consensus"), dict)
            else {}
        )
        if not isinstance(consensus, dict):
            consensus = {}
        payload = {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "artifact_ref": str(
                consensus.get("artifact_ref") or item.get("artifact_ref") or ""
            ),
            "artifact_digest": str(
                consensus.get("artifact_digest") or item.get("artifact_digest") or ""
            ),
            "prd_revision": _safe_int(
                consensus.get("prd_revision") or item.get("prd_revision")
            ),
            "idempotency_key": f"feishu-progress-confirm:{channel_id}:{thread_id}:{_safe_int(item.get('prd_revision'))}:{actor}",
        }
        return _execute_progress_action(
            context=context,
            action="channel-consensus-confirm",
            payload=payload,
            actor=actor,
            execute_action=execute_action,
        )

    task_id = str(item.get("task_id") or target_task_id or "")
    if command == CREATE_TASK_COMMAND:
        prompt = (
            f"Create a Task from the exact confirmed PRD for Channel {channel_id}, "
            f"thread {thread_id}. Return a task_create Plan with controlled options; "
            "do not create the Task or start a Workflow without approval."
        )
    else:
        if not task_id:
            return {
                "ok": False,
                "status": "task_missing",
                "message": "Task identity is missing.",
            }
        prompt = (
            f"Plan the delivery Workflow for existing Task {task_id}, bound to the "
            f"confirmed PRD from Channel {channel_id}, thread {thread_id}. Return a "
            "task_workflow Plan; do not invoke a Workflow without approval."
        )
    dispatch = dispatch_kanban or _dispatch_guided_kanban
    dispatch(
        prompt,
        {
            "context": context,
            "actor": actor,
            "user_id": user_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "task_id": task_id,
            "origin_binding": dict(origin),
            "command": command,
        },
    )
    return {
        "ok": True,
        "status": "accepted",
        "message": "Accepted; Kanban Agent is preparing the next Plan.",
        "channel_id": channel_id,
        "thread_id": thread_id,
        "task_id": task_id,
    }


def _execute_progress_action(
    *,
    context: Any,
    action: str,
    payload: dict[str, Any],
    actor: str,
    execute_action: Callable[[str, dict[str, Any], str], dict[str, Any]] | None,
) -> dict[str, Any]:
    if execute_action is not None:
        return execute_action(action, payload, actor)
    from zf.core.events.factory import event_log_from_project
    from zf.runtime.control_actions import ControlledActionService

    writer = EventWriter(
        event_log_from_project(
            Path(context.state_dir),
            config=context.config,
        )
    )
    requested = writer.emit(
        "runtime.action.requested",
        actor=actor,
        correlation_id=str(payload.get("channel_id") or "") or None,
        payload={"action": action, "request": {"source": "feishu-progress"}},
    )
    return ControlledActionService(
        Path(context.state_dir),
        writer,
        config=context.config,
        project_root=context.project_root,
        actor=actor,
        source="feishu-progress",
        surface="feishu",
    ).execute(
        action=action,
        requested_action=action,
        payload=payload,
        requested=requested,
    )


def _dispatch_guided_kanban(prompt: str, identity: dict[str, Any]) -> Any:
    from zf.cli.feishu_consume import dispatch_inbound_async

    origin = (
        identity.get("origin_binding")
        if isinstance(identity.get("origin_binding"), dict)
        else {}
    )
    seed = "|".join(
        (
            str(identity.get("command") or ""),
            str(identity.get("channel_id") or ""),
            str(identity.get("thread_id") or ""),
            str(identity.get("task_id") or ""),
        )
    )
    stable = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    event = MockFeishuTransport().parse_webhook(
        {
            "type": "message",
            "payload": {
                "text": prompt,
                "message_id": f"zf-guided-{stable}",
                # The guided handoff must preserve the bridge app identity so
                # multi-bot `app_id:chat_id` routes resolve exactly as a native
                # Feishu inbound message would.
                "app_id": str(os.environ.get("FEISHU_APP_ID") or ""),
                "root_message_id": str(origin.get("origin_message_id") or ""),
                "parent_message_id": str(origin.get("origin_message_id") or ""),
                "thread_id": str(origin.get("thread_id") or ""),
            },
            "user_id": str(identity.get("user_id") or ""),
            "chat_id": str(identity.get("chat_id") or ""),
        }
    )
    if event is None:
        raise RuntimeError("failed to build guided Kanban Agent event")
    return dispatch_inbound_async(
        event,
        context=identity["context"],
        transport=None,
    )


def _stage_action(stage: str, target: str) -> dict[str, Any] | None:
    options = {
        "awaiting_finalize": ("下一步：生成 PRD", FINALIZE_COMMAND),
        "awaiting_owner": ("下一步：确认 PRD", CONFIRM_COMMAND),
        "prd_confirmed": ("下一步：从 PRD 创建任务", CREATE_TASK_COMMAND),
        "task_created": ("下一步：规划工作流", PLAN_WORKFLOW_COMMAND),
    }
    selected = options.get(stage)
    if selected is None:
        return None
    label, command = selected
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "primary",
        "value": {"action": f"{command}:{target}"},
    }


def _stage_label(stage: str) -> str:
    return {
        "discussing": "Discussion",
        "awaiting_finalize": "Discussion",
        "drafting": "PRD",
        "awaiting_owner": "PRD",
        "prd_confirmed": "Task",
        "task_created": "Workflow",
        "workflow_terminal": "Workflow",
        "delivery_terminal": "Workflow",
        "failed": "Discussion",
    }.get(stage, "Channel")


def _stage_template(stage: str) -> str:
    if stage in {"workflow_terminal", "delivery_terminal"}:
        return "green"
    if stage == "failed":
        return "red"
    if stage in {
        "awaiting_finalize",
        "awaiting_owner",
        "prd_confirmed",
        "task_created",
    }:
        return "orange"
    return "blue"


def _card_digest(card: dict[str, Any]) -> str:
    canonical = {key: value for key, value in card.items() if not key.startswith("_")}
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = [
    "CHANNEL_PROGRESS_COMMANDS",
    "CONFIRM_COMMAND",
    "CREATE_TASK_COMMAND",
    "FINALIZE_COMMAND",
    "PLAN_WORKFLOW_COMMAND",
    "build_channel_progress_card",
    "fold_channel_progress",
    "handle_channel_progress_action",
    "parse_progress_target",
    "progress_target",
    "push_channel_progress_cards_once",
    "sync_channel_progress_cards",
]
