"""Feishu inbound ingestion + `zf feishu consume` runner (feishu-S1/S2, doc 98 §2).

Live inbound transport without a public webhook: shell out to the official
`lark-cli event consume` / `event +subscribe` (WS long-connection, NDJSON to
stdout), and dispatch each event:

- card.action.trigger (button) → _handle_event_data (feishu-B identity gate +
  feishu-A2 signed-token gate already enforce approve/reject/interrupt);
- im.message.receive_v1 (text) → resolve_feishu_route (feishu-S3) → the existing
  channel-post-message ControlledAction (channel target) / kanban / worker.

The dispatch + stream-loop are pure-ish (injectable line source) so they test
without a live lark-cli. The subprocess runner is thin glue.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from typing import Iterable

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.integrations.feishu.routing import resolve_feishu_route
from zf.integrations.feishu.storage import IdempotencyStore
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.channel_reply_turn import run_channel_reply_turn


def ingest_feishu_event(raw_event: dict, *, context) -> dict:
    """Dispatch one parsed Feishu event to its ZaoFu target. Returns a status dict."""
    from zf.cli.feishu import _handle_event_data  # local: cli/feishu is large

    event = MockFeishuTransport().parse_webhook(raw_event)
    if event is None:
        return {"status": "ignored", "reason": "unsupported_event"}
    if event.event_type == "button_action":
        # card.action.trigger — approve/reject/interrupt, fully gated by B + A2.
        return _handle_event_data(raw_event, context=context, user_levels={})
    if event.event_type == "message":
        return _route_inbound_message(event, context=context)
    return {"status": "ignored", "reason": event.event_type}


def _route_inbound_message(event, *, context) -> dict:
    route = resolve_feishu_route(
        context.config,
        event.chat_id,
        bot_open_id=str(event.payload.get("bot_open_id") or ""),
        app_id=str(event.payload.get("app_id") or ""),
    )
    if route is None:
        return {"status": "dropped", "reason": "unmapped_chat",
                "chat_id": event.chat_id}

    message_id = str(event.payload.get("message_id") or "")
    store = IdempotencyStore(
        context.state_dir / "integrations" / "feishu" / "inbound_idempotency.jsonl")
    if message_id and store.check_and_record(
            f"feishu:msg:{message_id}", command="inbound", user_id=event.user_id,
            chat_id=event.chat_id, source="feishu-consume"):
        return {"status": "duplicate", "message_id": message_id}

    if route.target == "channel":
        return _post_to_channel(event, route, context=context)
    if route.target == "kanban_agent":
        writer = EventWriter(
            event_log_from_project(context.state_dir, config=context.config))
        setattr(event, "route", route)
        from zf.integrations.feishu.kanban_agent_inbound import (
            kanban_agent_inbound_reply,
        )

        return kanban_agent_inbound_reply(
            context.state_dir,
            context.config,
            event,
            writer,
        )
    if route.target == "run_manager":
        writer = EventWriter(
            event_log_from_project(context.state_dir, config=context.config))
        setattr(event, "route", route)
        from zf.integrations.feishu.run_manager_inbound import (
            run_manager_inbound_reply,
        )

        return run_manager_inbound_reply(
            context.state_dir,
            context.config,
            event,
            writer,
        )
    if route.target == "agent":
        return bridge_inbound_message(event, context=context)
    # worker: the chat is bound to an existing worker session bridge; record the
    # routing decision here because worker delivery is owned by that bridge.
    return {"status": "routed", "target": route.target,
            "worker_session_id": route.worker_session_id, "chat_id": event.chat_id}


def _post_to_channel(event, route, *, context) -> dict:
    """Reuse channel-post-message ControlledAction (audit + member resolution)."""
    writer = EventWriter(
        event_log_from_project(context.state_dir, config=context.config))
    actor = f"feishu:{event.user_id or 'unknown'}"
    message_id = str(event.payload.get("message_id") or "")
    payload = {
        "channel_id": route.channel_id,
        "member_id": event.user_id or "feishu",
        "text": str(event.payload.get("text") or ""),
        "message_id": message_id,
        "mentions": [route.default_member] if route.default_member else [],
        "refs": {"feishu": {"chat_id": event.chat_id, "message_id": message_id}},
    }
    requested = writer.emit(
        "feishu.inbound.received", actor=actor,
        payload={"chat_id": event.chat_id, "message_id": message_id,
                 "channel_id": route.channel_id, "surface": "feishu-consume"})
    return ControlledActionService(
        context.state_dir, writer, config=context.config,
        actor=actor, source="feishu", surface="feishu",
    ).execute(action="channel-post-message", requested_action="feishu-inbound",
              payload=payload, requested=requested)


def bridge_inbound_message(event, *, context) -> dict:
    """B4 turnkey reply: post an inbound Feishu message into its bound channel
    and drive the REAL agent reply (route → dispatch), replacing the canned echo.

    The agent's reply (its channel.message.posted, streamed via stream_card when
    the backend emits deltas) is what flows back to Feishu — no synthesized text.
    """
    from pathlib import Path

    from zf.integrations.feishu import catchup
    from zf.integrations.feishu.routing import resolve_feishu_route
    from zf.runtime.channel_projection import project_channel

    route = resolve_feishu_route(
        context.config,
        event.chat_id,
        bot_open_id=str(event.payload.get("bot_open_id") or ""),
        app_id=str(event.payload.get("app_id") or ""),
    )
    if route is None or route.target not in (
            "channel",
            "agent",
            "kanban_agent",
            "run_manager",
    ):
        return {"status": "skipped", "reason": "no_channel_route"}
    setattr(event, "route", route)

    # W5 dedup: gate live + catchup-replay on the Feishu message_id so a restart
    # replay (or a re-delivered WS frame) never re-fires a reply. message_id-less
    # events (rare) fall through and are processed.
    message_id = str(event.payload.get("message_id") or "")
    if message_id:
        store = IdempotencyStore(
            context.state_dir / "integrations" / "feishu" / "inbound_idempotency.jsonl")
        if store.check_and_record(f"feishu:msg:{message_id}", command="bridge-inbound",
                                  user_id=event.user_id, chat_id=event.chat_id,
                                  source="feishu-bridge"):
            return {"status": "duplicate", "message_id": message_id}

    writer = EventWriter(
        event_log_from_project(context.state_dir, config=context.config))

    # target=kanban_agent / run_manager: Feishu is only the bridge; every
    # inbound message is delivered to the corresponding specialist agent.
    if route.target == "kanban_agent":
        from zf.integrations.feishu.kanban_agent_inbound import (
            kanban_agent_inbound_reply)
        return kanban_agent_inbound_reply(
            context.state_dir, context.config, event, writer)

    if route.target == "run_manager":
        from zf.integrations.feishu.run_manager_inbound import (
            run_manager_inbound_reply,
        )

        return run_manager_inbound_reply(
            context.state_dir,
            context.config,
            event,
            writer,
        )

    # target=agent (P0-2 direct-bind): no channel/member to create by hand — the
    # bridge provisions an ephemeral channel + a single agent member from the
    # route's backend/cwd, then drives the same real reply path.
    channel_id = route.channel_id
    member_id = route.default_member or "agent"
    project_root = None
    if route.target == "agent":
        channel_id = route.channel_id or f"agent-{event.chat_id}"
        member_id = route.default_member or "agent"
        project_root = Path(route.cwd) if route.cwd else None
        existing = project_channel(context.state_dir, channel_id) or {}
        if not any(str(m.get("member_id") or "") == member_id
                   for m in (existing.get("members") or [])):
            writer.emit("channel.member.invited", actor="feishu-bridge",
                        correlation_id=channel_id,
                        payload={"channel_id": channel_id, "member_id": member_id,
                                 "member_type": "provider_agent",
                                 "provider": route.backend, "backend": route.backend,
                                 "channel_role": "dev",
                                 "permissions": ["read", "message"],
                                 "source": "feishu"})

    msg = writer.emit(
        "channel.message.posted",
        actor=f"feishu:{event.user_id or 'unknown'}",
        correlation_id=channel_id,
        payload={
            "channel_id": channel_id, "thread_id": "main",
            "message_id": message_id or f"feishu-{event.user_id}",
            "member_id": event.user_id or "feishu", "role": "user",
            "source": "feishu", "text": str(event.payload.get("text") or ""),
            "mentions": [member_id],
            "refs": {"feishu": {"chat_id": event.chat_id, "message_id": message_id}},
        },
    )
    turn = run_channel_reply_turn(
        context.state_dir, writer, context.config,
        message_event=msg, message_payload=msg.payload, project_root=project_root)
    # W5: advance the per-chat cursor so a restart only replays the gap after this
    # message (live path); create_time rides in on the payload (catchup replay) or
    # is absent for a live WS frame that didn't carry it (cursor then stays put).
    catchup.record(context.state_dir, event.chat_id, message_id,
                   event.payload.get("create_time"))
    return {"status": "replied", "target": route.target, "channel_id": channel_id,
            "reply_requests": list(turn["route"].reply_requests),
            "dispatched": len(turn["dispatched"])}


_BRIDGE_EXECUTOR = None


def dispatch_inbound_async(event, *, context, transport=None, executor=None):
    """Run the inbound→reply→stream-card chain in a BACKGROUND thread, returning
    immediately so a WS event handler is never blocked for the agent's run time.

    The live codex e2e showed that running the agent reply synchronously inside
    the lark WS on_message handler blocks the WS ping → the connection is dropped
    (3003 ping_timeout). lark-bridge avoids this with an async ProcessPool; this
    is the ZaoFu equivalent. Returns the submitted future (or thread)."""
    import concurrent.futures
    global _BRIDGE_EXECUTOR

    pool = executor
    if pool is None:
        if _BRIDGE_EXECUTOR is None:
            _BRIDGE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="feishu-bridge")
        pool = _BRIDGE_EXECUTOR

    def _work():
        if transport is None:
            return bridge_inbound_message(event, context=context)

        import threading

        from zf.integrations.feishu.stream_card import push_stream_card_once

        def _push() -> None:
            try:
                push_stream_card_once(context.state_dir, transport,
                                      receive_id=event.chat_id)
            except Exception as exc:  # noqa: BLE001 — a push failure (e.g. a card
                # the Feishu API rejects) must be VISIBLE, not silently swallowed
                # by the background future (this hid a 400 "unknown property").
                print(f"[bridge] stream-card push failed chat={event.chat_id}: "
                      f"{exc!r}", file=sys.stderr, flush=True)

        # Push the streaming card WHILE the reply runs (bbc-style Thinking→stream),
        # not once at the end: the synchronous reply writes part.delta to
        # events.jsonl as it streams, so a 0.5s ticker reading them sends the card
        # early and updates it in place (push is ledger-idempotent). A final push
        # after the run finalizes the card (streaming_mode off, terminal state).
        done = threading.Event()

        def _ticker() -> None:
            while not done.wait(0.5):
                _push()

        ticker = threading.Thread(target=_ticker, name="feishu-stream-tick",
                                  daemon=True)
        ticker.start()
        try:
            result = bridge_inbound_message(event, context=context)
        finally:
            done.set()
            ticker.join(timeout=5)
        # kanban_agent replies are one-shot cards (no part.delta stream) — push the
        # card directly; everything else finalizes via the stream-card push.
        card = result.get("card")
        if card is not None:
            import json as _json

            from zf.integrations.feishu.transport import FeishuMessage
            try:
                transport.send_card(FeishuMessage(
                    chat_id=event.chat_id,
                    content=_json.dumps(card, ensure_ascii=False),
                    msg_type="interactive"))
            except Exception as exc:  # noqa: BLE001 — visible, not swallowed
                print(f"[bridge] kanban-card push failed chat={event.chat_id}: "
                      f"{exc!r}", file=sys.stderr, flush=True)
        elif result.get("status") == "replied":
            _push()
        return result

    return pool.submit(_work)


def consume_stream(lines: Iterable[str], *, context) -> dict:
    """Ingest a stream of NDJSON event lines. Returns counts by status."""
    counts: dict[str, int] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            counts["bad_json"] = counts.get("bad_json", 0) + 1
            continue
        if not isinstance(raw, dict):
            continue
        result = ingest_feishu_event(raw, context=context)
        status = str(result.get("status") or "ok")
        counts[status] = counts.get(status, 0) + 1
    return counts


def run_bridge(args) -> int:
    """`zf feishu bridge --event-json <evt>` — one-shot turnkey reply: ingest an
    inbound message into its channel and drive the real agent reply.

    `--watch` runs the in-process WS long-connection instead (always-on bridge,
    doc 99 §4.1) — the continuous deployment form of this one-shot path."""
    import json

    if getattr(args, "watch", False):
        from zf.integrations.feishu.bridge_watch import run_bridge_watch
        return run_bridge_watch(args)

    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    raw = getattr(args, "event_json", "") or "{}"
    try:
        data = json.loads(raw) if raw.strip().startswith("{") else json.loads(
            __import__("pathlib").Path(raw).read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"Error: invalid event JSON: {exc}", file=sys.stderr)
        return 1
    event = MockFeishuTransport().parse_webhook(data)
    if event is None:
        print("ignored: unsupported event", file=sys.stderr)
        return 0
    if event.event_type == "button_action":
        from zf.cli.feishu import _handle_event_data
        result = _handle_event_data(data, context=context, user_levels={})
    else:
        result = bridge_inbound_message(event, context=context)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def run_consume(args) -> int:
    """`zf feishu consume <event_key>` — drive lark-cli and ingest its NDJSON.

    Honors lark-cli's subprocess contract: wait for the stderr ready marker,
    read stdout NDJSON, SIGTERM on shutdown (never kill -9 — PreConsume hooks
    leak server subscriptions). The command is injectable for tests.
    """
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # NB: avoid the attr name `command` — it collides with the top-level
    # subparser dest (args.command == "feishu"). Tests inject via _consume_cmd.
    # P0-3 single-instance guard: refuse a second WS consumer for this app, else
    # Feishu load-balances events across connections and a zombie steals them.
    from zf.integrations.feishu.single_instance import acquire_ws_lock

    app_id = os.environ.get("FEISHU_APP_ID", "") or os.environ.get(
        "LARKSUITE_CLI_APP_ID", "")
    _ws_lock = acquire_ws_lock(context.state_dir, app_id)
    if _ws_lock is None:
        print("Error: another Feishu WS consumer is already running for this "
              "app (single-instance guard). Stop it first.", file=sys.stderr)
        return 1

    command = getattr(args, "_consume_cmd", None) or [
        "lark-cli", "event", "consume", args.event_key, "--as",
        getattr(args, "as_identity", "bot")]
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.PIPE, text=True)

    # Wait for the `[event] ready event_key=...` marker on stderr before reading
    # stdout (no sleep). Bail on a fatal error line first. NB: match the precise
    # marker — a substring "ready" also matches "already connected".
    assert proc.stderr is not None
    for err_line in proc.stderr:
        if "ready event_key" in err_line or "[event] ready" in err_line:
            print(f"feishu consume ready: {err_line.strip()}", file=sys.stderr)
            break
        if err_line.strip().startswith("Error:") or "already connected" in err_line:
            print(f"feishu consume failed: {err_line.strip()}", file=sys.stderr)
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
            return 1

    counts: dict[str, int] = {}
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            result = ingest_feishu_event(json.loads(line), context=context) \
                if line.strip() else {"status": "blank"}
            status = str(result.get("status") or "ok")
            counts[status] = counts.get(status, 0) + 1
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGTERM)
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        _ws_lock.release()
    print(f"feishu consume done: {counts}", file=sys.stderr)
    return proc.returncode or 0
