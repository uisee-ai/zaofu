"""feishu → Kanban Agent inbound driver.

Feishu routes the product-manager bot here. This handler records no semantic
decision itself; every message flows to the Kanban Agent channel conversation
path.
"""

from __future__ import annotations

def kanban_agent_inbound_reply(state_dir, config, event, writer) -> dict[str, Any]:
    """Handle one Feishu→kanban_agent inbound message."""
    from zf.integrations.feishu.agent_conversation import run_specialist_conversation

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
    )
