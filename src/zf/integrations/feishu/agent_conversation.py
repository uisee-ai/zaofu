"""Feishu-bound specialist agent conversation helpers.

Feishu stays a transport bridge: bot/chat routing decides whether a message goes
to the Kanban Agent or Run Manager Agent, then the selected agent may answer via
the normal channel reply path instead of a fixed template.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_turn import run_channel_reply_turn
from zf.runtime.channel_sidecar import channel_message_event_payload


def run_specialist_conversation(
    *,
    state_dir,
    config,
    event,
    writer,
    route,
    agent_kind: str,
    default_member: str,
    display_name: str,
    source: str,
) -> dict[str, Any]:
    """Route one Feishu message to the selected specialist agent.

    This provisions a stable channel/member per Feishu chat and reuses
    ``run_channel_reply_turn``. With real headless backends the reply streams
    through the existing Feishu stream-card path; tests can use ``backend=fake``.
    """

    state = Path(state_dir)
    payload = getattr(event, "payload", None) or {}
    text = str(payload.get("text") or "")
    message_id = str(payload.get("message_id") or "") or f"feishu-{agent_kind}"
    chat_id = str(getattr(event, "chat_id", "") or "")
    user_id = str(getattr(event, "user_id", "") or payload.get("member_id") or "feishu")
    member_id = str(getattr(route, "default_member", "") or default_member)
    channel_id = str(getattr(route, "channel_id", "") or "") or _stable_channel_id(
        agent_kind,
        chat_id,
    )
    backend = _conversation_backend(config, route)
    project_root = _project_root(route)

    _ensure_channel(
        state,
        writer,
        channel_id=channel_id,
        member_id=member_id,
        display_name=display_name,
        backend=backend,
        source=source,
        agent_kind=agent_kind,
        chat_id=chat_id,
    )
    msg = writer.emit(
        "channel.message.posted",
        actor=f"feishu:{user_id or 'unknown'}",
        correlation_id=channel_id,
        payload=channel_message_event_payload(
            state,
            {
                "channel_id": channel_id,
                "thread_id": "main",
                "message_id": message_id,
                "member_id": user_id or "feishu",
                "role": "user",
                "source": "feishu",
                "text": text,
                "mentions": [member_id],
                "refs": {
                    "feishu": {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "agent_kind": agent_kind,
                    },
                },
            },
            created_by=f"feishu:{agent_kind}",
        ),
    )
    turn = run_channel_reply_turn(
        state,
        writer,
        config,
        message_event=msg,
        message_payload=msg.payload,
        actor=source,
        source=source,
        project_root=project_root,
    )
    result = {
        "status": "replied",
        "kind": f"{agent_kind}_conversation",
        "target": agent_kind,
        "channel_id": channel_id,
        "member_id": member_id,
        "backend": backend,
        "reply_requests": list(turn["route"].reply_requests),
        "dispatched": len(turn["dispatched"]),
    }
    if agent_kind == "kanban_agent":
        proposal = _emit_kanban_action_proposal(
            state,
            writer,
            channel_id=channel_id,
            member_id=member_id,
            user_text=text,
            trigger_event=msg,
            chat_id=chat_id,
            feishu_message_id=message_id,
            source=source,
        )
        if proposal is not None:
            result["action_proposal"] = proposal
    return result


def _emit_kanban_action_proposal(
    state_dir: Path,
    writer,
    *,
    channel_id: str,
    member_id: str,
    user_text: str,
    trigger_event,
    chat_id: str,
    feishu_message_id: str,
    source: str,
) -> dict[str, Any] | None:
    """Extract an action proposal from the kanban agent's channel reply.

    Same extractor and gates as the Web panel headless turn (racing-e2e P1:
    without this, a Feishu user saying 创建任务 only ever got prose back and no
    task-creation loop existed on this surface). dispatch runs synchronously in
    run_channel_reply_turn, so the assistant reply is already folded when we
    project here. Emits the same kanban.agent.action.proposed event the Web
    triage list renders with an Accept action — approval stays a controlled
    action; nothing executes from Feishu without the operator accepting it.
    """
    from zf.web.proposal_extraction import extract_action_proposal

    reply_text = _latest_assistant_reply_text(
        state_dir,
        channel_id=channel_id,
        member_id=member_id,
        after_ts=str(getattr(trigger_event, "ts", "") or ""),
    )
    if not reply_text:
        return None
    proposal = extract_action_proposal(reply_text, user_message=user_text)
    if proposal is None:
        return None
    writer.emit(
        "kanban.agent.action.proposed",
        actor=source,
        causation_id=trigger_event.id,
        correlation_id=channel_id,
        payload={
            "turn_id": trigger_event.id,
            "thread_key": f"channel:{channel_id}:main:{member_id}",
            "project_id": "",
            "conversation_id": channel_id,
            "reply_event_id": "",
            "proposal": proposal,
            "source": "feishu",
            "refs": {
                "feishu": {
                    "chat_id": chat_id,
                    "message_id": feishu_message_id,
                },
            },
        },
    )
    return proposal


def _latest_assistant_reply_text(
    state_dir: Path,
    *,
    channel_id: str,
    member_id: str,
    after_ts: str,
) -> str:
    channel = project_channel(state_dir, channel_id) or {}
    messages = channel.get("messages")
    if isinstance(messages, dict):
        messages = list(messages.values())
    replies = [
        m for m in (messages or [])
        if isinstance(m, dict)
        and str(m.get("member_id") or "") == member_id
        and str(m.get("role") or "") == "assistant"
        and str(m.get("ts") or "") >= after_ts
    ]
    if not replies:
        return ""
    replies.sort(key=lambda m: str(m.get("ts") or ""))
    return str(replies[-1].get("text") or "")


def _ensure_channel(
    state_dir: Path,
    writer,
    *,
    channel_id: str,
    member_id: str,
    display_name: str,
    backend: str,
    source: str,
    agent_kind: str,
    chat_id: str = "",
) -> None:
    existing = project_channel(state_dir, channel_id) or {}
    if not existing or not existing.get("created_by_event"):
        # One channel per Feishu chat — a bare "Feishu Kanban Agent" name makes
        # every p2p chat's channel indistinguishable in the Web list (racing-e2e
        # P3b), so suffix the chat identity.
        suffix = re.sub(r"[^A-Za-z0-9_.-]+", "-", chat_id or "").strip("-")[-8:]
        name = f"Feishu {display_name} · {suffix}" if suffix else f"Feishu {display_name}"
        writer.emit(
            "channel.created",
            actor=source,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "name": name,
                "created_by": source,
                "scope": {"source": "feishu", "agent_kind": agent_kind},
            },
        )
    members = existing.get("members") if isinstance(existing, dict) else []
    if not any(
        isinstance(member, dict) and str(member.get("member_id") or "") == member_id
        for member in (members or [])
    ):
        member_payload = {
            "channel_id": channel_id,
            "member_id": member_id,
            "display_name": display_name,
            "member_type": "provider_agent",
            "provider": backend,
            "backend": backend,
            "channel_role": "owner_delegate",
            "permission_profile": "dangerous_full",
            "permission_profile_ack": True,
            "dangerous_ack": True,
            "permissions": ["read", "message"],
            "source": source,
        }
        if agent_kind == "kanban_agent":
            # Teach the channel-dispatched turn the same proposal-output
            # contract the Web panel bakes into its system prompt — without
            # it, a real backend replies in prose and the extraction hook in
            # run_specialist_conversation never fires.
            from zf.web.operator_contract import (
                KANBAN_AGENT_CHANNEL_PROPOSAL_CONTRACT,
            )

            member_payload["reply_contract"] = KANBAN_AGENT_CHANNEL_PROPOSAL_CONTRACT
        writer.emit(
            "channel.member.invited",
            actor=source,
            correlation_id=channel_id,
            payload=member_payload,
        )
    discussion = existing.get("discussion") if isinstance(existing, dict) else {}
    if str((discussion or {}).get("default_responder_id") or "") != member_id:
        writer.emit(
            "channel.discussion.mode.set",
            actor=source,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "mode": "manual_mention",
                "default_responder_id": member_id,
                "source": source,
            },
        )


def _conversation_backend(config: object | None, route: object | None) -> str:
    route_backend = str(getattr(route, "backend", "") or "").strip()
    if route_backend:
        return route_backend
    runtime = getattr(config, "runtime", None)
    run_manager = getattr(runtime, "run_manager", None)
    backend = str(getattr(run_manager, "backend", "") or "").strip()
    if backend:
        return backend
    autoresearch = getattr(config, "autoresearch", None)
    trigger_policy = getattr(autoresearch, "trigger_policy", None)
    backend = str(getattr(trigger_policy, "self_repair_backend", "") or "").strip()
    return backend or "codex"


def _project_root(route: object | None) -> Path | None:
    cwd = str(getattr(route, "cwd", "") or "").strip()
    if cwd:
        return Path(cwd)
    return Path.cwd()


def _stable_channel_id(agent_kind: str, chat_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", chat_id or "unknown").strip("-")
    return f"feishu-{agent_kind}-{safe or 'unknown'}"
