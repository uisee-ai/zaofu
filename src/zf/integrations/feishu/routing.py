"""Feishu chat → ZaoFu target routing (doc 98 §4, backlog feishu-S3).

The inbound prerequisite: an event arriving from a Feishu chat must resolve to
exactly one ZaoFu target — a Channel (with a default member, @mention overrides),
the Kanban Agent (operator repair), or an existing worker session. The routing
table lives in zf.yaml (control plane, alongside feishu_identity); an unmapped
chat resolves to None so the caller drops it rather than guessing.

Pure function; the inbound transport (feishu-S1) calls it.
"""

from __future__ import annotations

from zf.core.config.schema import FeishuRouteConfig


_WILDCARD_KEY = "*"


def resolve_feishu_route(
    config: object | None,
    chat_id: str,
    *,
    bot_open_id: str = "",
    app_id: str = "",
):
    """Resolve a Feishu chat_id to its FeishuRouteConfig, or None if unmapped.

    An exact `chat_id` route wins; otherwise the wildcard key ``"*"`` (if present)
    is the default for any unmapped chat. This is what lets a single bot serve
    both group chat (explicit oc_... routes) AND single/p2p chat (the p2p chat has
    its own chat_id, which falls to the wildcard) without enumerating every chat.

    Fail-closed: no config / no exact + no wildcard → None. The caller drops an
    unmapped chat rather than guessing; this function never invents a route.
    """
    if not chat_id:
        return None
    integrations = getattr(config, "integrations", None)
    routing = getattr(integrations, "feishu_routing", None)
    if not isinstance(routing, dict):
        return None
    route = None
    for key in _candidate_keys(chat_id, bot_open_id=bot_open_id, app_id=app_id):
        route = routing.get(key)
        if route is not None:
            break
    return route if isinstance(route, FeishuRouteConfig) else None


def _candidate_keys(chat_id: str, *, bot_open_id: str = "", app_id: str = "") -> list[str]:
    """Most-specific first route keys for multi-bot chats."""
    keys: list[str] = []
    if bot_open_id:
        keys.extend([
            f"{chat_id}#{bot_open_id}",
            f"{chat_id}@{bot_open_id}",
        ])
    if app_id:
        keys.append(f"{app_id}:{chat_id}")
    keys.append(chat_id)
    if bot_open_id:
        keys.extend([
            f"{_WILDCARD_KEY}#{bot_open_id}",
            f"{_WILDCARD_KEY}@{bot_open_id}",
        ])
    if app_id:
        keys.append(f"{app_id}:{_WILDCARD_KEY}")
    keys.append(_WILDCARD_KEY)  # default for p2p / unmapped chats
    return keys
