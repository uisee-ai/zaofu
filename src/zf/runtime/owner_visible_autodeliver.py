"""Opt-in auto-delivery of owner.visible_message to Feishu from the watcher tick.

doc 78 O-7. The supervisor emits ``owner.visible_message.requested`` events for
operator-facing problem alerts. Delivering them to Feishu previously required
manually running ``zf feishu push --watch``. When ``ZF_OWNER_VISIBLE_CHAT`` (the
chat / receive id) and Feishu credentials are present in the environment, the
watcher tick delivers them in-process at the runtime edge — NOT the
deterministic kernel (the orchestrator never calls an external API; this runs in
the ``zf start`` watcher composition root). Opt-in by env presence: with
``ZF_OWNER_VISIBLE_CHAT`` and route-specific targets unset this records an
explicit failed receipt when pending messages exist. Reuses the existing
``deliver_owner_visible_messages_once`` delivery path and the signer-aware
event-log factory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

OWNER_VISIBLE_CHAT_ENV = "ZF_OWNER_VISIBLE_CHAT"
OWNER_VISIBLE_RECEIVE_ID_TYPE_ENV = "ZF_OWNER_VISIBLE_RECEIVE_ID_TYPE"
OWNER_VISIBLE_ROUTE_CHAT_ENVS = {
    "approval": "ZF_OWNER_VISIBLE_APPROVAL_CHAT",
    "alert": "ZF_OWNER_VISIBLE_ALERT_CHAT",
    "progress": "ZF_OWNER_VISIBLE_PROGRESS_CHAT",
}
OWNER_VISIBLE_ROUTE_RECEIVE_ID_TYPE_ENVS = {
    "approval": "ZF_OWNER_VISIBLE_APPROVAL_RECEIVE_ID_TYPE",
    "alert": "ZF_OWNER_VISIBLE_ALERT_RECEIVE_ID_TYPE",
    "progress": "ZF_OWNER_VISIBLE_PROGRESS_RECEIVE_ID_TYPE",
}


def owner_visible_autodeliver_enabled(env: dict[str, str] | None = None) -> bool:
    """True when the owner-visible Feishu routing target is configured in env."""
    src = os.environ if env is None else env
    return _owner_visible_routing_from_env(src) is not None


def deliver_owner_visible_to_feishu(
    *,
    state_dir: Path,
    config: Any = None,
    env: dict[str, str] | None = None,
    transport: Any = None,
) -> Any:
    """Best-effort one-shot delivery of pending owner.visible_message events.

    Returns the delivery result, or None when not configured / when transport
    construction fails. NEVER raises — it runs inside the watcher tick. The
    ``ZF_OWNER_VISIBLE_CHAT`` is a compatibility target and aliases to owner,
    approval, and alert. Route-specific env values override the alias.
    ``transport`` is injectable for tests; otherwise a real FeishuTransport is
    built from ``FEISHU_APP_ID``/``FEISHU_APP_SECRET``.
    """
    src = os.environ if env is None else env
    routing = _owner_visible_routing_from_env(src)
    if routing is None:
        return _record_owner_visible_feishu_unconfigured(
            state_dir=state_dir,
            config=config,
        )
    if transport is None and _live_feishu_delivery_blocked(Path(state_dir), src):
        return None
    try:
        from zf.core.events import EventWriter
        from zf.core.events.factory import event_log_from_project
        from zf.runtime.owner_visible_delivery import (
            deliver_owner_visible_messages_once,
        )

        if transport is None:
            # R13 fix (backlog 2026-06-06-0401 §E): FeishuTransport is the ABC —
            # ``FeishuTransport()`` raises TypeError, so this autodeliver path has
            # silently no-op'd since the transport was made abstract. Use the
            # concrete FeishuHttpTransport, which also carries the Tier3 token
            # re-mint (doc 79) so a stale tenant_access_token self-heals.
            from zf.integrations.feishu.transport import FeishuHttpTransport

            transport = FeishuHttpTransport()
        log = event_log_from_project(Path(state_dir), config=config)
        try:
            return deliver_owner_visible_messages_once(
                event_log=log,
                writer=EventWriter(log),
                transport=transport,
                routing=routing,
                max_attempts=1,
            )
        finally:
            close = getattr(log, "close", None)
            if callable(close):
                close()
    except Exception:
        return None


def _live_feishu_delivery_blocked(state_dir: Path, src: dict[str, str]) -> bool:
    """Fail closed for tests / self-repair unless live Feishu is explicit.

    The owner-visible delivery path is allowed to use real Feishu only in a real
    runtime process. Tests can still verify delivery by injecting a fake
    transport; this guard only blocks automatic construction of
    ``FeishuHttpTransport``.
    """

    allow_live = str(src.get("ZF_ALLOW_LIVE_FEISHU_IN_TESTS") or "").strip().lower()
    if allow_live in {"1", "true", "yes", "on"}:
        return False
    disabled = str(src.get("ZF_DISABLE_LIVE_FEISHU") or "").strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return True
    if src.get("PYTEST_CURRENT_TEST"):
        return True
    path_text = str(state_dir.resolve())
    return (
        "/pytest-" in path_text
        or "/pytest-of-" in path_text
        or "/zf-self-repair/" in path_text
        or path_text.endswith("/zf-self-repair")
    )


def _owner_visible_routing_from_env(src: dict[str, str]) -> Any | None:
    """Build owner-visible Feishu routing from env without exposing secrets.

    ``ZF_OWNER_VISIBLE_CHAT`` is the backwards-compatible single target. It
    aliases to owner + approval + alert so high-severity owner-visible messages
    cannot fall into an unconfigured approval route. Explicit route envs override
    the alias. Progress stays opt-in to avoid noisy low-value updates.
    """

    from zf.integrations.feishu.projection import RoutingConfig

    default_receive_id_type = (
        str(src.get(OWNER_VISIBLE_RECEIVE_ID_TYPE_ENV) or "chat_id").strip()
        or "chat_id"
    )
    channels: dict[str, str] = {}
    receive_id_types: dict[str, str] = {}
    owner_chat = str(src.get(OWNER_VISIBLE_CHAT_ENV) or "").strip()
    if owner_chat:
        for role in ("owner", "approval", "alert"):
            channels[role] = owner_chat
            receive_id_types[role] = default_receive_id_type
    for role, env_name in OWNER_VISIBLE_ROUTE_CHAT_ENVS.items():
        receive_id = str(src.get(env_name) or "").strip()
        if not receive_id:
            continue
        channels[role] = receive_id
        receive_id_type = (
            str(src.get(OWNER_VISIBLE_ROUTE_RECEIVE_ID_TYPE_ENVS[role]) or "").strip()
            or default_receive_id_type
        )
        receive_id_types[role] = receive_id_type
    if not channels:
        return None
    return RoutingConfig(
        channels=channels,
        receive_id_type=default_receive_id_type,
        receive_id_types=receive_id_types,
    )


def _record_owner_visible_feishu_unconfigured(
    *,
    state_dir: Path,
    config: Any = None,
) -> Any:
    """Emit explicit delivery-failed receipts when Feishu target is absent."""
    try:
        from zf.core.events import EventWriter
        from zf.core.events.factory import event_log_from_project
        from zf.integrations.feishu.projection import RoutingConfig
        from zf.runtime.owner_visible_delivery import (
            OWNER_MESSAGE_REQUESTED,
            deliver_owner_visible_messages_once,
        )

        class _UnconfiguredTransport:
            def send_message(self, message: Any) -> bool:
                return False

        log = event_log_from_project(Path(state_dir), config=config, warn=False)
        try:
            pending = [
                event for event in log.read_all()
                if event.type == OWNER_MESSAGE_REQUESTED
            ]
            if not pending:
                return None
            return deliver_owner_visible_messages_once(
                event_log=log,
                writer=EventWriter(log),
                transport=_UnconfiguredTransport(),
                routing=RoutingConfig(channels={}),
                max_attempts=1,
            )
        finally:
            close = getattr(log, "close", None)
            if callable(close):
                close()
    except Exception:
        return None
