"""feishu W1: in-process always-on WS bridge (`zf feishu bridge --watch`, doc 99 §4.1).

Replaces the throwaway nohup receiver script from doc 98's live verification. Runs
the lark-oapi WS long-connection in-process and, per inbound chat:

  on_message → PendingQueue.push (W2 debounce)
    → flush(batch) → block(scope) → dispatch_inbound_async (B4 async, never blocks
      the WS ping) → on completion unblock(scope)

The transport is constructed ONCE at startup (a fixed FeishuHttpTransport), which
removes the "two replies merged" bug from doc 99 §3 (that came from swapping
transport mid-stream while reusing a state_dir). Session continuity across turns
is already provided by the channel HeadlessThreadStore (stable channel_id + thread
from bridge_inbound_message), so each turn resumes the previous provider session —
no separate session store here (doc 99 §4.3).

`BridgeWatch` (the queue+dispatch core) has no lark dependency so it tests without
a live WS. `run_bridge_watch` is the thin WS glue that lazily imports lark-oapi.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from typing import Any, Callable

from zf.cli.feishu_consume import dispatch_inbound_async
from zf.integrations.feishu.transport import MockFeishuTransport

DEFAULT_DEBOUNCE_MS = 600


def sdk_log_level(lark_module: Any) -> Any:
    """Avoid INFO logs because the SDK prints WS URL query parameters there."""
    log_level = getattr(lark_module, "LogLevel", None)
    return getattr(log_level, "WARNING", getattr(log_level, "ERROR", None))


def merge_batch(batch: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse a debounced batch of normalized inbound messages into one raw
    event dict (newline-joined text, last message's ids)."""
    texts = [str(m.get("text") or "") for m in batch if m.get("text")]
    last = batch[-1] if batch else {}
    return {
        "type": "message",
        "payload": {"text": "\n".join(texts),
                    "message_id": str(last.get("message_id") or ""),
                    "create_time": str(last.get("create_time") or ""),
                    "bot_open_id": str(last.get("bot_open_id") or ""),
                    "app_id": str(last.get("app_id") or ""),
                    "mention_ids": list(last.get("mention_ids") or [])},
        "user_id": str(last.get("user_id") or ""),
        "chat_id": str(last.get("chat_id") or ""),
    }


class BridgeWatch:
    """Queue + dispatch core. Inject `dispatch` in tests to avoid a live backend."""

    def __init__(self, context, transport, *, debounce_ms: int = DEFAULT_DEBOUNCE_MS,
                 dispatch: Callable | None = None) -> None:
        from zf.integrations.feishu.pending_queue import PendingQueue

        self.context = context
        self.transport = transport
        self._dispatch = dispatch or dispatch_inbound_async
        self._futures: set[Any] = set()
        self._futures_lock = threading.Lock()
        self.queue = PendingQueue(debounce_ms, self._on_flush)

    def on_message(self, normalized: dict[str, Any]) -> int:
        """Feed one normalized inbound message {text, message_id, user_id, chat_id}
        into the per-chat debounce queue. Returns queued count (0 if no chat_id)."""
        scope = str(normalized.get("chat_id") or "")
        if not scope:
            return 0
        return self.queue.push(scope, normalized)

    def _on_flush(self, scope: str, batch: list[dict[str, Any]]) -> None:
        # block the scope so messages arriving during the run accumulate without
        # firing a second run; unblock when this run's future settles.
        self.queue.block(scope)
        print(f"[bridge] flushing chat={scope} batch={len(batch)}", flush=True)
        event = MockFeishuTransport().parse_webhook(merge_batch(batch))
        if event is None:
            self.queue.unblock(scope)
            return
        try:
            future = self._dispatch(event, context=self.context, transport=self.transport)
        except Exception as exc:  # noqa: BLE001 - keep the bridge observable and live.
            print(f"[bridge] dispatch submit failed chat={scope}: {exc!r}",
                  file=sys.stderr, flush=True)
            self.queue.unblock(scope)
            return
        with self._futures_lock:
            self._futures.add(future)

        def _done(settled: Any) -> None:
            try:
                result = settled.result()
                print(
                    f"[bridge] dispatch done chat={scope} "
                    f"status={result.get('status') if isinstance(result, dict) else type(result).__name__} "
                    f"kind={result.get('kind') if isinstance(result, dict) else ''}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001 - background failures are otherwise invisible.
                print(f"[bridge] dispatch failed chat={scope}: {exc!r}",
                      file=sys.stderr, flush=True)
            with self._futures_lock:
                self._futures.discard(settled)
            self.queue.unblock(scope)

        future.add_done_callback(_done)

    def drain(self, timeout: float = 30.0) -> None:
        with self._futures_lock:
            pending = list(self._futures)
        for future in pending:
            try:
                future.result(timeout=timeout)
            except Exception:  # noqa: BLE001 — drain must not raise on a failed run
                pass

    def shutdown(self) -> None:
        self.queue.cancel_all()
        self.drain()


def _catchup_chat_id(route_key: str) -> str:
    key = str(route_key or "").strip()
    if not key or key == "*":
        return ""
    if "#" in key:
        key = key.split("#", 1)[0]
    elif "@" in key:
        key = key.split("@", 1)[0]
    elif ":" in key:
        key = key.split(":", 1)[1]
    if key == "*" or key.startswith("__") or key.endswith("_unset__"):
        return ""
    return key


def _catchup_on_start(context, transport, bridge: BridgeWatch,
                      bot_open_id: str = "", app_id: str = "") -> None:
    """W5: replay the restart gap for every explicitly-routed chat before the WS
    loop takes over. Wildcard "*" (p2p / dynamic chats) is skipped — we don't
    pre-scan unknown conversation history (doc 99 §4.5 boundary)."""
    from zf.integrations.feishu import catchup

    integrations = getattr(context.config, "integrations", None)
    routing = getattr(integrations, "feishu_routing", None)
    if not isinstance(routing, dict):
        return
    seen_chat_ids: set[str] = set()
    for route_key, route in routing.items():
        chat_id = _catchup_chat_id(str(route_key))
        if not chat_id or getattr(route, "target", "") not in (
            "channel",
            "agent",
            "kanban_agent",
            "run_manager",
        ):
            continue
        if chat_id in seen_chat_ids:
            continue
        seen_chat_ids.add(chat_id)
        try:
            def _dispatch_replay(raw: dict) -> Any:
                payload = raw.setdefault("payload", {})
                payload["bot_open_id"] = bot_open_id
                payload["app_id"] = app_id
                return bridge._dispatch(
                    MockFeishuTransport().parse_webhook(raw),
                    context=context,
                    transport=transport,
                )

            result = catchup.catchup_chat(
                context.state_dir, chat_id, bot_open_id=bot_open_id,
                list_recent=lambda cid: transport.list_recent(cid),
                dispatch=_dispatch_replay)
            if result["replayed"]:
                print(f"[bridge] catchup chat={chat_id} replayed="
                      f"{result['replayed']}", flush=True)
        except Exception as exc:  # noqa: BLE001 — catchup must not block startup
            print(f"[bridge] catchup error chat={chat_id}: {exc!r}",
                  file=sys.stderr, flush=True)


def run_bridge_watch(args) -> int:
    """`zf feishu bridge --watch` — in-process WS long-connection that drives the
    real agent reply for every inbound message, debounced per chat."""
    from zf.core.config.loader import ConfigError
    from zf.core.config.project_context import resolve_project_context
    from zf.integrations.feishu.single_instance import acquire_ws_lock
    from zf.integrations.feishu.transport import FeishuHttpTransport

    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    app_id = os.environ.get("FEISHU_APP_ID", "") or os.environ.get(
        "LARKSUITE_CLI_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "") or os.environ.get(
        "LARKSUITE_CLI_APP_SECRET", "")
    if not app_id or not app_secret:
        print("Error: FEISHU_APP_ID / FEISHU_APP_SECRET must be set for --watch.",
              file=sys.stderr)
        return 1

    lock = acquire_ws_lock(context.state_dir, app_id)
    if lock is None:
        print("Error: another Feishu WS bridge is already running for this app "
              "(single-instance guard). Stop it first.", file=sys.stderr)
        return 1

    try:
        import lark_oapi as lark
    except ImportError:
        print("Error: lark-oapi is not installed. Run: pip install 'zf[feishu]'",
              file=sys.stderr)
        lock.release()
        return 1

    transport = FeishuHttpTransport()
    bridge = BridgeWatch(context, transport,
                         debounce_ms=int(getattr(args, "debounce_ms", DEFAULT_DEBOUNCE_MS)))
    bot_open_id = transport.bot_open_id()

    def _on_msg(data: Any) -> None:
        from zf.integrations.feishu.catchup import addressed_to_bot

        try:
            message = data.event.message
            text = ""
            if message.content:
                import json
                try:
                    text = json.loads(message.content).get("text", "")
                except (ValueError, TypeError):
                    text = message.content
            sender = getattr(data.event, "sender", None)
            user_id = ""
            if sender is not None and getattr(sender, "sender_id", None) is not None:
                user_id = getattr(sender.sender_id, "open_id", "") or ""
            # multi-bot group: only answer when WE are the @-target (p2p always).
            chat_type = getattr(message, "chat_type", "") or ""
            mention_ids = []
            for mention in (getattr(message, "mentions", None) or []):
                mid = getattr(mention, "id", None)
                oid = getattr(mid, "open_id", "") if mid is not None else ""
                if oid:
                    mention_ids.append(oid)
            if not addressed_to_bot(mention_ids, bot_open_id, chat_type=chat_type):
                print(f"[bridge] skip (not @us) chat={message.chat_id} "
                      f"mentions={mention_ids}", flush=True)
                return
            queued = bridge.on_message({
                "text": text, "message_id": message.message_id,
                "user_id": user_id, "chat_id": message.chat_id,
                "create_time": getattr(message, "create_time", "") or "",
                "bot_open_id": bot_open_id,
                "app_id": app_id,
                "mention_ids": mention_ids})
            print(f"[bridge] queued chat={message.chat_id} n={queued}", flush=True)
        except Exception as exc:  # noqa: BLE001 — a bad event must not kill the WS
            print(f"[bridge] on_message error: {exc!r}", file=sys.stderr, flush=True)

    def _on_card_action(data: Any) -> Any:
        """card.action.trigger (button click over the long-connection) → the
        same gated approve/reject path as a webhook (ingest → identity + A2 gate
        → ControlledAction). Returns a toast so the button doesn't spin."""
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            CallBackToast, P2CardActionTriggerResponse)

        from zf.cli.feishu_consume import ingest_feishu_event

        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        try:
            ev = data.event
            action = getattr(ev, "action", None)
            value = getattr(action, "value", None) or {}
            operator = getattr(ev, "operator", None)
            open_id = getattr(operator, "open_id", "") or ""
            ctx = getattr(ev, "context", None)
            chat_id = getattr(ctx, "open_chat_id", "") or ""
            raw = {
                "header": {"event_type": "card.action.trigger"},
                "event": {
                    "action": {"value": value, "tag": getattr(action, "tag", "")},
                    "operator": {"operator_id": {"open_id": open_id}},
                    "context": {"open_chat_id": chat_id,
                                "open_message_id": getattr(ctx, "open_message_id", "")},
                },
            }
            result = ingest_feishu_event(raw, context=context)
            ok = bool(result.get("ok", result.get("status") not in ("rejected", "error")))
            toast.type = "success" if ok else "error"
            toast.content = str(result.get("message") or result.get("status") or "已处理")
            print(f"[bridge] card.action {value} by {open_id}: {result.get('status')}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 — a bad callback must not kill the WS
            toast.type = "error"
            toast.content = "处理失败"
            print(f"[bridge] card.action error: {exc!r}", file=sys.stderr, flush=True)
        resp.toast = toast
        return resp

    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(_on_msg)
               .register_p2_card_action_trigger(_on_card_action).build())
    client = lark.ws.Client(app_id, app_secret, event_handler=handler,
                            log_level=sdk_log_level(lark))

    def _stop(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    print(f"[bridge] watch starting (app={app_id[:12]}…, bot={bot_open_id[:18] or '?'}, "
          f"debounce={bridge.queue._delay * 1000:.0f}ms)", flush=True)
    _catchup_on_start(context, bridge.transport, bridge, bot_open_id, app_id)
    try:
        client.start()  # blocking WS loop with internal reconnect
    except KeyboardInterrupt:
        print("[bridge] shutdown signal — draining in-flight runs…", flush=True)
    finally:
        bridge.shutdown()
        lock.release()
        print("[bridge] stopped.", flush=True)
    return 0
