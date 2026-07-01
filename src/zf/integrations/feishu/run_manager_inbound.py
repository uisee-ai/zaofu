"""Feishu -> resident Run Manager Agent inbound driver.

Feishu routes the architect bot here. This handler records receipt, then sends
every message to the Run Manager Agent's normal channel conversation path.
"""

from __future__ import annotations

from pathlib import Path


def run_manager_inbound_reply(state_dir, config, event, writer) -> dict[str, Any]:
    state = Path(state_dir)
    payload = getattr(event, "payload", None) or {}
    text = str(payload.get("text") or "")
    user_id = str(getattr(event, "user_id", "") or payload.get("member_id") or "")
    message_id = str(payload.get("message_id") or "")
    writer.emit(
        "run.manager.inbound.received",
        actor=f"feishu:{user_id or 'unknown'}",
        payload={
            "schema_version": "run-manager.feishu-inbound.v1",
            "chat_id": str(getattr(event, "chat_id", "") or ""),
            "message_id": message_id,
            "text_excerpt": text[:500],
        },
    )
    from zf.integrations.feishu.agent_conversation import run_specialist_conversation

    route = getattr(event, "route", None)
    if route is None:
        from zf.integrations.feishu.routing import resolve_feishu_route

        route = resolve_feishu_route(
            config,
            str(getattr(event, "chat_id", "") or ""),
            bot_open_id=str(payload.get("bot_open_id") or ""),
            app_id=str(payload.get("app_id") or ""),
        )
    return run_specialist_conversation(
        state_dir=state,
        config=config,
        event=event,
        writer=writer,
        route=route,
        agent_kind="run_manager",
        default_member="run-manager",
        display_name="Run Manager Agent",
        source="feishu-run-manager-agent",
    )
