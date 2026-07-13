"""feishu W1: in-process WS bridge core (doc 99 §4.1) + W3 continuity precondition.

BridgeWatch is tested with an injected dispatch so no live WS / backend is needed.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from pathlib import Path

import yaml

from zf.cli.feishu_consume import bridge_inbound_message
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.integrations.feishu.bridge_watch import (
    BridgeWatch,
    _catchup_chat_id,
    _catchup_on_start,
    merge_batch,
    sdk_log_level,
)
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.runtime.channel_projection import project_channel


def test_merge_batch_joins_text_keeps_last_ids():
    raw = merge_batch([
        {"text": "a", "message_id": "m1", "user_id": "u", "chat_id": "oc_x"},
        {
            "text": "b",
            "message_id": "m2",
            "user_id": "u",
            "chat_id": "oc_x",
            "bot_open_id": "ou_bot",
            "app_id": "cli_app",
            "mention_ids": ["ou_bot"],
        },
    ])
    assert raw["payload"]["text"] == "a\nb"
    assert raw["payload"]["message_id"] == "m2"
    assert raw["payload"]["bot_open_id"] == "ou_bot"
    assert raw["payload"]["app_id"] == "cli_app"
    assert raw["chat_id"] == "oc_x"


def test_merge_batch_preserves_reply_context_refs():
    raw = merge_batch([
        {"text": "a", "message_id": "m1", "user_id": "u", "chat_id": "oc_x"},
        {
            "text": "b",
            "message_id": "m2",
            "user_id": "u",
            "chat_id": "oc_x",
            "parent_message_id": "om_parent",
            "root_message_id": "om_root",
            "quote_message_id": "om_quote",
            "thread_id": "thread-1",
        },
    ])

    assert raw["payload"]["message_id"] == "m2"
    assert raw["payload"]["parent_message_id"] == "om_parent"
    assert raw["payload"]["root_message_id"] == "om_root"
    assert raw["payload"]["quote_message_id"] == "om_quote"
    assert raw["payload"]["thread_id"] == "thread-1"


def test_sdk_log_level_avoids_info_urls():
    class FakeLogLevel:
        INFO = "info"
        WARNING = "warning"
        ERROR = "error"

    class FakeLark:
        LogLevel = FakeLogLevel

    assert sdk_log_level(FakeLark) == "warning"


def test_catchup_chat_id_extracts_multi_bot_route_keys():
    assert _catchup_chat_id("oc_group#ou_bot") == "oc_group"
    assert _catchup_chat_id("oc_group@ou_bot") == "oc_group"
    assert _catchup_chat_id("cli_app:oc_group") == "oc_group"
    assert _catchup_chat_id("oc_group") == "oc_group"
    assert _catchup_chat_id("*#ou_bot") == ""
    assert _catchup_chat_id("cli_app:*") == ""
    assert _catchup_chat_id("__zf_pm_chat_unset__") == ""


def test_catchup_on_start_dedupes_chat_routes_and_skips_placeholders(monkeypatch):
    from types import SimpleNamespace

    calls = []

    def fake_catchup_chat(state_dir, chat_id, **kwargs):
        calls.append(chat_id)
        return {"chat_id": chat_id, "replayed": 0}

    monkeypatch.setattr(
        "zf.integrations.feishu.catchup.catchup_chat",
        fake_catchup_chat,
    )
    context = SimpleNamespace(
        state_dir=Path(".zf"),
        config=SimpleNamespace(
            integrations=SimpleNamespace(feishu_routing={
                "oc_group#ou_arch": SimpleNamespace(target="run_manager"),
                "oc_group#ou_pm": SimpleNamespace(target="kanban_agent"),
                "*#ou_arch": SimpleNamespace(target="run_manager"),
                "__zf_pm_chat_unset__": SimpleNamespace(target="kanban_agent"),
            }),
        ),
    )
    bridge = SimpleNamespace(
        _dispatch=lambda *args, **kwargs: None,
    )
    transport = SimpleNamespace(list_recent=lambda chat_id: [])

    _catchup_on_start(context, transport, bridge, "ou_arch", "cli_app")

    assert calls == ["oc_group"]


def _msg(text, mid="m1", chat="oc_x"):
    return {"text": text, "message_id": mid, "user_id": "ou_u", "chat_id": chat}


def test_debounced_messages_dispatch_once_with_merged_text():
    calls: list = []
    done = threading.Event()

    def fake_dispatch(event, *, context, transport=None):
        calls.append(event)
        fut: concurrent.futures.Future = concurrent.futures.Future()
        fut.set_result({"status": "replied"})
        done.set()
        return fut

    bridge = BridgeWatch(context=None, transport=None, debounce_ms=80,
                         dispatch=fake_dispatch)
    bridge.on_message(_msg("hello", "m1"))
    bridge.on_message(_msg("world", "m2"))
    assert done.wait(2.0)
    time.sleep(0.05)
    assert len(calls) == 1  # debounced into a single dispatch
    assert calls[0].payload.get("text") == "hello\nworld"


def test_run_serialized_per_chat_via_block_unblock():
    calls: list = []
    gate = threading.Event()  # controls when the first run "completes"
    second_dispatched = threading.Event()
    first_future: list = []

    def fake_dispatch(event, *, context, transport=None):
        calls.append(event)
        fut: concurrent.futures.Future = concurrent.futures.Future()
        if len(calls) == 1:
            first_future.append(fut)  # leave first run pending until gate set
        else:
            second_dispatched.set()
            fut.set_result({"status": "replied"})
        return fut

    bridge = BridgeWatch(context=None, transport=None, debounce_ms=60,
                         dispatch=fake_dispatch)
    bridge.on_message(_msg("turn1", "m1"))
    time.sleep(0.25)
    assert len(calls) == 1  # first run dispatched, still pending (blocked)

    # a message arriving mid-run must NOT start a second dispatch
    bridge.on_message(_msg("turn2", "m2"))
    time.sleep(0.25)
    assert len(calls) == 1

    # complete the first run → unblock → queued turn2 flushes as a new run
    first_future[0].set_result({"status": "replied"})
    assert second_dispatched.wait(2.0)
    assert len(calls) == 2
    assert calls[1].payload.get("text") == "turn2"


def test_drain_waits_for_in_flight_runs():
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    ran: list = []

    def fake_dispatch(event, *, context, transport=None):
        def work():
            time.sleep(0.15)
            ran.append(event)
            return {"status": "replied"}
        return pool.submit(work)

    bridge = BridgeWatch(context=None, transport=None, debounce_ms=40,
                         dispatch=fake_dispatch)
    bridge.on_message(_msg("x", "m1"))
    time.sleep(0.1)  # let the flush submit the work
    bridge.shutdown()  # drains → must block until the run recorded its result
    assert ran, "shutdown drained before the in-flight run finished"
    pool.shutdown(wait=True)


# --- W3: session continuity precondition (doc 99 §4.3) -----------------------
# Session resume lives in the channel HeadlessThreadStore, keyed by a stable
# channel_id + thread. The bridge's job is to yield a STABLE channel_id for the
# same Feishu chat across turns so that store can resume. Verify that invariant.

def test_same_chat_yields_stable_channel_across_turns(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}],
        "integrations": {"feishu_routing": {
            "oc_x": {"target": "agent", "backend": "fake",
                     "default_member": "dev-agent"}}}}))
    main(["init"])
    ctx = resolve_project_context()

    def _turn(mid):
        ev = MockFeishuTransport().parse_webhook({
            "type": "message", "payload": {"text": "@dev-agent hi", "message_id": mid},
            "user_id": "ou_u", "chat_id": "oc_x"})
        return bridge_inbound_message(ev, context=ctx)

    r1 = _turn("m1")
    r2 = _turn("m2")
    # stable channel_id across turns → HeadlessThreadStore thread_key is stable →
    # provider_session_id resumes (continuity). Derived from chat_id, not message.
    assert r1["channel_id"] == r2["channel_id"] == "agent-oc_x"


def test_run_manager_route_enters_agent_conversation(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "integrations": {"feishu_routing": {
            "oc_group#ou_arch": {
                "target": "run_manager",
                "backend": "fake",
                "default_member": "run-manager",
            },
            "oc_group": {"target": "kanban_agent"},
        }},
    }))
    main(["init"])
    ctx = resolve_project_context()
    transport = MockFeishuTransport()
    ev = transport.parse_webhook({
        "type": "message",
        "payload": {
            "text": "hi",
            "message_id": "m-rm",
            "bot_open_id": "ou_arch",
        },
        "user_id": "ou_u",
        "chat_id": "oc_group",
    })

    from zf.cli.feishu_consume import dispatch_inbound_async

    result = dispatch_inbound_async(ev, context=ctx, transport=transport).result(5)

    assert result["kind"] == "run_manager_conversation"
    channel = project_channel(ctx.state_dir, "feishu-run_manager-oc_group") or {}
    member = next(
        member for member in channel.get("members", [])
        if member.get("member_id") == "run-manager"
    )
    assert member["channel_role"] == "owner_delegate"
    assert member["permission_profile"] == "dangerous_full"
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    assert [event for event in events if event.type == "channel.message.posted"
            and event.payload.get("member_id") == "run-manager"]


def test_kanban_agent_route_enters_agent_conversation(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "integrations": {"feishu_routing": {
            "oc_group#ou_pm": {
                "target": "kanban_agent",
                "backend": "fake",
                "default_member": "zf-product-manager",
            },
        }},
    }))
    main(["init"])
    ctx = resolve_project_context()
    transport = MockFeishuTransport()
    ev = transport.parse_webhook({
        "type": "message",
        "payload": {
            "text": "状态",
            "message_id": "m-kanban",
            "bot_open_id": "ou_pm",
        },
        "user_id": "ou_u",
        "chat_id": "oc_group",
    })

    from zf.cli.feishu_consume import dispatch_inbound_async

    result = dispatch_inbound_async(ev, context=ctx, transport=transport).result(5)

    assert result["kind"] == "kanban_agent_conversation"
    channel = project_channel(ctx.state_dir, "feishu-kanban_agent-oc_group") or {}
    member = next(
        member for member in channel.get("members", [])
        if member.get("member_id") == "zf-product-manager"
    )
    assert member["channel_role"] == "owner_delegate"
    assert member["permission_profile"] == "dangerous_full"
    events = EventLog(ctx.state_dir / "events.jsonl").read_all()
    assert [event for event in events if event.type == "channel.message.posted"
            and event.payload.get("member_id") == "zf-product-manager"]


def test_bridge_event_json_with_state_dir_loads_feishu_yaml(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0",
        "project": {"name": "t", "state_dir": ".runtime"},
    }))
    (tmp_path / "feishu.yaml").write_text(yaml.dump({
        "feishu_routing": {
            "oc_group#ou_arch": {
                "target": "run_manager",
                "backend": "fake",
                "default_member": "run-manager",
            },
        },
    }))
    main(["init"])
    raw_event = {
        "type": "message",
        "payload": {
            "text": "状态",
            "message_id": "m-state-dir",
            "bot_open_id": "ou_arch",
        },
        "user_id": "ou_u",
        "chat_id": "oc_group",
    }

    rc = main([
        "feishu",
        "bridge",
        "--state-dir",
        ".runtime",
        "--event-json",
        json.dumps(raw_event),
    ])

    out = capsys.readouterr().out
    assert rc == 0
    result = json.loads(out.strip().splitlines()[-1])
    assert result["status"] == "replied"
    assert result["kind"] == "run_manager_conversation"
