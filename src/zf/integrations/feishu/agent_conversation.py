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
    )
    msg = writer.emit(
        "channel.message.posted",
        actor=f"feishu:{user_id or 'unknown'}",
        correlation_id=channel_id,
        payload={
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
    return {
        "status": "replied",
        "kind": f"{agent_kind}_conversation",
        "target": agent_kind,
        "channel_id": channel_id,
        "member_id": member_id,
        "backend": backend,
        "reply_requests": list(turn["route"].reply_requests),
        "dispatched": len(turn["dispatched"]),
    }


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
) -> None:
    existing = project_channel(state_dir, channel_id) or {}
    if not existing or not existing.get("created_by_event"):
        writer.emit(
            "channel.created",
            actor=source,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "name": f"Feishu {display_name}",
                "created_by": source,
                "scope": {"source": "feishu", "agent_kind": agent_kind},
            },
        )
    members = existing.get("members") if isinstance(existing, dict) else []
    if not any(
        isinstance(member, dict) and str(member.get("member_id") or "") == member_id
        for member in (members or [])
    ):
        writer.emit(
            "channel.member.invited",
            actor=source,
            correlation_id=channel_id,
            payload={
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
            },
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
