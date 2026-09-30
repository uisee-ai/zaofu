"""Feishu → Kanban Agent inbound driver."""

from __future__ import annotations

from typing import Any


def kanban_agent_inbound_reply(
    state_dir, config, event, writer, *, transport=None, context=None,
) -> dict[str, Any]:
    """Handle one Feishu→kanban_agent inbound message."""
    from zf.integrations.feishu.agent_conversation import run_specialist_conversation
    from zf.runtime.kanban_agent_status import (
        is_project_status_query,
        render_project_status_reply,
    )

    payload = getattr(event, "payload", None) or {}
    route = getattr(event, "route", None)
    if route is None:
        from zf.integrations.feishu.routing import resolve_feishu_route

        route = resolve_feishu_route(
            config,
            str(getattr(event, "chat_id", "") or ""),
            bot_open_id=str(payload.get("bot_open_id") or ""),
            app_id=str(payload.get("app_id") or ""),
        )
    text = str(payload.get("text") or "")
    command_text = _extract_zf_command(text)
    if command_text:
        from zf.cli.feishu import _handle_event_data

        raw = {
            "type": "message",
            "payload": {
                **dict(payload),
                "text": command_text,
                "message_id": str(payload.get("message_id") or ""),
                "app_id": str(payload.get("app_id") or ""),
            },
            "user_id": getattr(event, "user_id", ""),
            "chat_id": getattr(event, "chat_id", ""),
        }
        if context is None:
            from zf.core.config.project_context import resolve_project_context

            context = resolve_project_context(
                explicit_state_dir=state_dir,
                load_config_with_explicit=True,
            )
        return _handle_event_data(
            raw,
            context=context,
            user_levels={},
            transport=transport,
        )
    status_reply = (
        render_project_status_reply(state_dir)
        if is_project_status_query(text)
        else None
    )
    return run_specialist_conversation(
        state_dir=state_dir,
        config=config,
        event=event,
        writer=writer,
        route=route,
        agent_kind="kanban_agent",
        default_member="kanban-agent",
        display_name="Kanban Agent",
        source="feishu-kanban-agent",
        deterministic_reply=status_reply,
        deterministic_reason="canonical project status projection",
    )


def _extract_zf_command(text: str) -> str:
    from zf.cli.feishu_consume import extract_zf_command_text

    return extract_zf_command_text(text)
