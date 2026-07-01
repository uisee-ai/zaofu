"""feishu W5: catchup-on-restart (doc 99 §4.5, backlog 2026-06-22-1130).

A WS bridge process restart (Ctrl-C / OOM / deploy) loses every message the
operator sent during the gap — the live long-connection only resumes from
re-attach. Persist a per-chat cursor (last processed message + epoch-ms) and,
on restart, replay the gap via the transport's message-list REST endpoint into
the SAME inbound path, deduped.

Three gotchas covered by regression tests:
- epoch-ms vs TZ-ambiguous string: lark renders local-time strings; store the
  epoch-ms reading at event time so a TZ change across restart can't shift the
  cutoff by hours. Readers prefer create_time_ms, fall back to parsing.
- REST minute-precision vs live ms-precision + reorder: floor the cutoff to the
  minute (so same-minute REST rows survive) AND step back a lookback margin (so a
  cross-minute out-of-order/silent-drop miss survives); dedup absorbs re-applies.
- bot-self回环: list returns the bot's own cards too — skip app/bot-sent rows or
  every restart re-fires the bot's replies as inbound.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Callable

_DEFAULT_LOOKBACK_MS = 120_000  # observed WS silent-drop / reorder window


def _cursor_path(state_dir) -> Path:
    return Path(state_dir) / "integrations" / "feishu" / "bridge_cursor.json"


def _read_all(state_dir) -> dict:
    try:
        data = json.loads(_cursor_path(state_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_cursor(state_dir, chat_id: str) -> dict:
    """Return the persisted cursor for one chat, or {} (missing / corrupt)."""
    entry = _read_all(state_dir).get(chat_id)
    return entry if isinstance(entry, dict) else {}


def record(state_dir, chat_id: str, message_id: str, create_time: object) -> None:
    """Advance one chat's cursor. No-op if message_id / create_time is empty."""
    if not chat_id or not message_id or not create_time:
        return
    data = _read_all(state_dir)
    data[chat_id] = {"message_id": str(message_id), "create_time": str(create_time),
                     "create_time_ms": _to_epoch_ms(create_time)}
    path = _cursor_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_epoch_ms(create_time: object) -> int:
    """Coerce a create_time into epoch ms. Accepts an epoch(-ms) numeric string
    or "YYYY-MM-DD HH:MM[:SS]" (lark REST local-time shape). 0 if uninterpretable
    → _newer_than treats it as older than any non-zero cursor (skip safely)."""
    if not create_time:
        return 0
    s = str(create_time).strip()
    if s.isdigit():
        return int(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(_dt.datetime.strptime(s, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return 0


def addressed_to_bot(mention_ids, bot_open_id: str, *, chat_type: str = "") -> bool:
    """True when a message should be answered by THIS bot.

    A group can hold several bots; with broad read scope our app receives every
    message, so we must only reply when we're the @-target (the user's bug: an
    @other-bot message got a reply from us). p2p (DM) is always for us. When our
    own open_id is unknown we fail OPEN (reply) rather than black-hole everything.
    """
    if chat_type == "p2p":
        return True
    ids = [str(m) for m in (mention_ids or []) if m]
    if ids:
        return (not bot_open_id) or (bot_open_id in ids)
    # no mentions: a DM-like message (reply); in a group a no-mention message is
    # ambient chatter not addressed to us (skip only when we know it's a group).
    return chat_type != "group"


def _is_bot(msg: dict) -> bool:
    """True when a listed row was sent by the app/bot itself (skip on replay so
    the bridge never re-ingests its own reply cards)."""
    sender = msg.get("sender") if isinstance(msg.get("sender"), dict) else {}
    kind = str(sender.get("sender_type") or sender.get("id_type") or "").lower()
    return kind in {"app", "bot"}


def _newer_than(messages, cursor_create_time: str, *, cursor_ms: int = 0,
                lookback_ms: int = _DEFAULT_LOOKBACK_MS) -> list[dict]:
    """Keep rows at-or-after (cursor minute floor − lookback), oldest-first."""
    raw_cutoff = cursor_ms or _to_epoch_ms(cursor_create_time)
    minute_floor = (raw_cutoff // 60_000) * 60_000
    cutoff = minute_floor - max(0, lookback_ms)

    def keep(m: dict) -> bool:
        ts = _to_epoch_ms(m.get("create_time"))
        return ts > 0 and ts >= cutoff

    fresh = [m for m in messages if keep(m)]
    fresh.sort(key=lambda m: _to_epoch_ms(m.get("create_time")))
    return fresh


def _msg_to_raw_event(msg: dict) -> dict:
    """A listed message row → the raw event dict parse_webhook(...) consumes
    (the local-fixture `type=message` shape), carrying create_time for cursor
    advance on the replay's way through bridge_inbound_message."""
    sender = msg.get("sender") if isinstance(msg.get("sender"), dict) else {}
    return {
        "type": "message",
        "payload": {
            "text": str(msg.get("content") or ""),
            "message_id": str(msg.get("message_id") or ""),
            "create_time": str(msg.get("create_time") or ""),
            "mentions": list(msg.get("mentions") or []),
            "chat_type": str(msg.get("chat_type") or ""),
        },
        "user_id": str(sender.get("id") or sender.get("open_id") or ""),
        "chat_id": str(msg.get("chat_id") or ""),
    }


def pending_events(state_dir, chat_id: str, *, list_fn: Callable[[], list[dict]],
                   bot_open_id: str = "",
                   lookback_ms: int = _DEFAULT_LOOKBACK_MS) -> list[dict]:
    """Raw event dicts for messages newer than the saved cursor, oldest-first.

    No cursor (fresh deploy) → [] (never replay arbitrary history — the live
    stream picks up from now and writes the first cursor). Bot-self rows skipped,
    and rows that @-target a DIFFERENT bot are skipped (addressed_to_bot).
    `list_fn` is injectable for tests (production: transport.list_recent)."""
    cursor = read_cursor(state_dir, chat_id)
    cursor_ct = str(cursor.get("create_time") or "")
    if not cursor_ct:
        return []
    try:
        cursor_ms = int(cursor.get("create_time_ms") or 0)
    except (TypeError, ValueError):
        cursor_ms = 0
    messages = list_fn() or []
    fresh = _newer_than(messages, cursor_ct, cursor_ms=cursor_ms, lookback_ms=lookback_ms)
    out = []
    for m in fresh:
        if _is_bot(m):
            continue
        if not addressed_to_bot(m.get("mentions"), bot_open_id,
                                chat_type=str(m.get("chat_type") or "")):
            continue
        out.append(_msg_to_raw_event(m))
    return out


def newest_marker(messages) -> tuple[str, str]:
    """(message_id, create_time) of the newest listed row — the monotonic
    high-water mark to advance the cursor to after a catchup pass (covers
    bot-self rows that were skipped, so they aren't re-listed forever)."""
    best, best_ms = None, -1
    for m in messages or []:
        ts = _to_epoch_ms(m.get("create_time"))
        if ts > best_ms:
            best, best_ms = m, ts
    if not best:
        return ("", "")
    return (str(best.get("message_id") or ""), str(best.get("create_time") or ""))


def catchup_chat(state_dir, chat_id: str, *, list_recent: Callable[[str], list[dict]],
                 dispatch: Callable[[dict], Any], bot_open_id: str = "",
                 lookback_ms: int = _DEFAULT_LOOKBACK_MS) -> dict:
    """Replay the gap for one chat: list → filter newer-than-cursor → dispatch
    each (dedup is enforced downstream by IdempotencyStore) → advance cursor to
    the high-water mark. Returns {replayed, chat_id}."""
    messages = list_recent(chat_id) or []
    events = pending_events(state_dir, chat_id, list_fn=lambda: messages,
                            bot_open_id=bot_open_id, lookback_ms=lookback_ms)
    for raw in events:
        dispatch(raw)
    marker_id, marker_ct = newest_marker(messages)
    if marker_id and marker_ct:
        record(state_dir, chat_id, marker_id, marker_ct)
    return {"chat_id": chat_id, "replayed": len(events)}
