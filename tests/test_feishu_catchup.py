"""feishu W5: catchup-on-restart — cursor, replay filter, dedup (backlog 2026-06-22-1130)."""

from __future__ import annotations

from pathlib import Path

import yaml

from zf.cli.feishu_consume import bridge_inbound_message
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.integrations.feishu import catchup
from zf.integrations.feishu.transport import MockFeishuTransport

_MIN = 60_000
_BASE = 1_700_000_400_000  # minute-aligned epoch ms


def _msg(mid, ms, *, sender_type="user", text="hi"):
    return {"message_id": mid, "chat_id": "oc_x", "msg_type": "text",
            "content": text, "create_time": str(ms),
            "sender": {"id": "ou_u", "sender_type": sender_type}}


# --- C1: cursor + epoch-ms ---------------------------------------------------

def test_cursor_roundtrip_stores_epoch_ms(tmp_path):
    assert catchup.read_cursor(tmp_path, "oc_x") == {}
    catchup.record(tmp_path, "oc_x", "m1", str(_BASE))
    cur = catchup.read_cursor(tmp_path, "oc_x")
    assert cur["message_id"] == "m1" and cur["create_time_ms"] == _BASE


def test_record_is_noop_without_id_or_time(tmp_path):
    catchup.record(tmp_path, "oc_x", "", str(_BASE))
    catchup.record(tmp_path, "oc_x", "m1", "")
    assert catchup.read_cursor(tmp_path, "oc_x") == {}


def test_to_epoch_ms_shapes():
    assert catchup._to_epoch_ms("1700000400000") == 1700000400000
    assert catchup._to_epoch_ms("2026-05-06 14:08") > 0
    assert catchup._to_epoch_ms("2026-05-06 14:08:32") > 0
    assert catchup._to_epoch_ms("not a time") == 0
    assert catchup._to_epoch_ms("") == 0


# --- C2: pending_events filtering --------------------------------------------

def test_no_cursor_replays_nothing(tmp_path):
    msgs = [_msg("m1", _BASE), _msg("m2", _BASE + _MIN)]
    assert catchup.pending_events(tmp_path, "oc_x", list_fn=lambda: msgs) == []


def test_pending_returns_newer_than_cursor_oldest_first(tmp_path):
    catchup.record(tmp_path, "oc_x", "m5", str(_BASE + 5 * _MIN))
    msgs = [_msg("m7", _BASE + 7 * _MIN), _msg("m1", _BASE - 10 * _MIN),
            _msg("m6", _BASE + 6 * _MIN)]
    out = catchup.pending_events(tmp_path, "oc_x", list_fn=lambda: msgs)
    ids = [e["payload"]["message_id"] for e in out]
    assert ids == ["m6", "m7"]  # older-than-cursor dropped, rest oldest-first


# --- gotcha: minute-floor + lookback keeps a cross-minute reorder miss --------

def test_lookback_keeps_message_just_before_cursor_minute(tmp_path):
    catchup.record(tmp_path, "oc_x", "m5", str(_BASE + 5 * _MIN))
    # m4 is 1 min before the cursor (out-of-order silent-drop miss); default
    # 120s lookback floors cutoff to base+3min, so m4 (base+4min) survives.
    msgs = [_msg("m4", _BASE + 4 * _MIN), _msg("m1", _BASE + 1 * _MIN)]
    ids = [e["payload"]["message_id"]
           for e in catchup.pending_events(tmp_path, "oc_x", list_fn=lambda: msgs)]
    assert ids == ["m4"]  # m4 kept (within lookback), m1 (4 min before) dropped


# --- gotcha: bot-self rows skipped on replay ---------------------------------

def test_bot_self_rows_skipped(tmp_path):
    catchup.record(tmp_path, "oc_x", "m5", str(_BASE + 5 * _MIN))
    msgs = [_msg("m6", _BASE + 6 * _MIN, sender_type="app", text="bot card"),
            _msg("m7", _BASE + 7 * _MIN, sender_type="user")]
    ids = [e["payload"]["message_id"]
           for e in catchup.pending_events(tmp_path, "oc_x", list_fn=lambda: msgs)]
    assert ids == ["m7"]  # the app/bot row is not re-ingested


# --- gotcha: epoch-ms cursor wins over an ambiguous string -------------------

def test_cursor_ms_wins_over_string(tmp_path):
    # cursor string is garbage but create_time_ms is authoritative
    path = tmp_path / "integrations" / "feishu" / "bridge_cursor.json"
    path.parent.mkdir(parents=True)
    import json
    path.write_text(json.dumps({"oc_x": {"message_id": "m5", "create_time": "garbage",
                                          "create_time_ms": _BASE + 5 * _MIN}}))
    msgs = [_msg("m6", _BASE + 6 * _MIN), _msg("m1", _BASE - 10 * _MIN)]
    ids = [e["payload"]["message_id"]
           for e in catchup.pending_events(tmp_path, "oc_x", list_fn=lambda: msgs)]
    assert ids == ["m6"]  # filtered by epoch-ms, not the unparseable string


# --- catchup_chat advances cursor to high-water ------------------------------

def test_catchup_chat_replays_and_advances_cursor(tmp_path):
    catchup.record(tmp_path, "oc_x", "m5", str(_BASE + 5 * _MIN))
    msgs = [_msg("m6", _BASE + 6 * _MIN), _msg("m8", _BASE + 8 * _MIN, sender_type="app")]
    dispatched = []
    r = catchup.catchup_chat(tmp_path, "oc_x", list_recent=lambda cid: msgs,
                             dispatch=lambda raw: dispatched.append(raw))
    assert r["replayed"] == 1  # only the user row m6
    # cursor advanced to the newest LISTED row (m8) so the bot row isn't re-listed
    assert catchup.read_cursor(tmp_path, "oc_x")["message_id"] == "m8"


# --- multi-bot group: only answer when WE are the @-target -------------------

def test_addressed_to_bot_rules():
    me = "ou_me"
    # p2p (DM) is always for us
    assert catchup.addressed_to_bot([], me, chat_type="p2p") is True
    # group: only when our open_id is among the mentions
    assert catchup.addressed_to_bot(["ou_other"], me, chat_type="group") is False
    assert catchup.addressed_to_bot(["ou_other", me], me, chat_type="group") is True
    # group with no mention = ambient chatter, not for us
    assert catchup.addressed_to_bot([], me, chat_type="group") is False
    # unknown our-own id → fail open (reply rather than black-hole)
    assert catchup.addressed_to_bot(["ou_other"], "", chat_type="group") is True


def test_catchup_skips_messages_at_other_bot(tmp_path):
    catchup.record(tmp_path, "oc_x", "m5", str(_BASE + 5 * _MIN))
    msgs = [
        {**_msg("m6", _BASE + 6 * _MIN), "mentions": ["ou_otherbot"],
         "chat_type": "group"},  # @ another bot → skip
        {**_msg("m7", _BASE + 7 * _MIN), "mentions": ["ou_me"],
         "chat_type": "group"},  # @ us → keep
    ]
    out = catchup.pending_events(tmp_path, "oc_x", list_fn=lambda: msgs,
                                 bot_open_id="ou_me")
    assert [e["payload"]["message_id"] for e in out] == ["m7"]


# --- C3: bridge inbound dedup + live cursor advance --------------------------

def _project(tmp_path):
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_routing": {
            "oc_x": {"target": "agent", "backend": "fake",
                     "default_member": "dev-agent"}}}}))
    main(["init"])
    return resolve_project_context()


def _event(mid):
    return MockFeishuTransport().parse_webhook({
        "type": "message",
        "payload": {"text": "@dev-agent hi", "message_id": mid,
                    "create_time": str(_BASE)},
        "user_id": "ou_u", "chat_id": "oc_x"})


def test_bridge_dedup_and_cursor_advance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = _project(tmp_path)
    r1 = bridge_inbound_message(_event("m1"), context=ctx)
    assert r1["status"] == "replied"
    # same message_id again (a restart replay or re-delivered frame) → deduped
    r2 = bridge_inbound_message(_event("m1"), context=ctx)
    assert r2["status"] == "duplicate"
    # exactly one user post landed
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    posts = [e for e in events if e.type == "channel.message.posted"
             and e.payload.get("member_id") == "ou_u"]
    assert len(posts) == 1
    # live path advanced the cursor
    assert catchup.read_cursor(ctx.state_dir, "oc_x")["message_id"] == "m1"
