"""feishu W1: in-process WS bridge core (doc 99 §4.1) + W3 continuity precondition.

BridgeWatch is tested with an injected dispatch so no live WS / backend is needed.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from zf.cli.feishu_consume import bridge_inbound_message
from zf.cli.main import main
from zf.core.config.project_context import resolve_project_context
from zf.core.events.log import EventLog
from zf.integrations.feishu.bridge_watch import (
    BridgeProjectionLoop,
    BridgeWatch,
    _catchup_chat_id,
    _catchup_on_start,
    _configured_projection_targets,
    merge_batch,
    push_bridge_projections_once,
    _safe_card_action_label,
    _card_action_toast,
    _submit_card_action,
    sdk_log_level,
    should_ignore_inbound_message,
    workspace_message_is_addressed,
)


def test_card_action_ack_message_is_bounded_for_slow_background_work() -> None:
    # Keep this assertion next to the bridge callback contract: the immediate
    # toast is deliberately short and does not contain a provider/report body.
    assert _card_action_toast({"ok": True, "status": "accepted"}) == (
        "success",
        "已处理，请查看更新后的卡片",
    )


def test_card_action_submission_does_not_wait_for_slow_action() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_action() -> str:
        started.set()
        release.wait(2)
        return "completed"

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = _submit_card_action(executor, slow_action)
        assert started.wait(1)
        assert not future.done()
        release.set()
        assert future.result(timeout=1) == "completed"
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


def test_merge_batch_preserves_and_deduplicates_attachments():
    raw = merge_batch([
        {
            "text": "请看附件",
            "attachments": [
                {"resource_key": "file-1", "resource_type": "file", "file_name": "a.txt"},
            ],
        },
        {
            "text": "补充",
            "attachments": [
                {"resource_key": "file-1", "resource_type": "file", "file_name": "a.txt"},
                {"resource_key": "img-1", "resource_type": "image", "file_name": "a.png"},
            ],
        },
    ])
    assert raw["payload"]["attachments"] == [
        {"resource_key": "file-1", "resource_type": "file", "file_name": "a.txt", "content_type": ""},
        {"resource_key": "img-1", "resource_type": "image", "file_name": "a.png", "content_type": ""},
    ]


def test_sdk_log_level_avoids_info_urls():
    class FakeLogLevel:
        INFO = "info"
        WARNING = "warning"
        ERROR = "error"

    class FakeLark:
        LogLevel = FakeLogLevel

    assert sdk_log_level(FakeLark) == "warning"


def test_bridge_ignores_app_cards_but_keeps_user_content() -> None:
    assert should_ignore_inbound_message(
        text="",
        message_type="interactive",
        sender_type="app",
    )
    assert should_ignore_inbound_message(
        text="",
        message_type="system",
        sender_type="user",
    )
    assert not should_ignore_inbound_message(
        text="附件说明",
        message_type="post",
        sender_type="user",
    )


def test_card_action_log_label_never_contains_signed_token() -> None:
    value = {"action": "issue-triage-start:1", "t": "zf1.secret-token"}
    label = _safe_card_action_label(value)
    assert label == "issue-triage-start:1"
    assert "zf1.secret-token" not in label


def test_card_callback_returns_short_ack_instead_of_report_json() -> None:
    report = json.dumps({"report": {"analysis": "证据" * 5000}}, ensure_ascii=False)
    assert _card_action_toast({"ok": True, "status": "completed", "message": report}) == (
        "success", "已处理，请查看更新后的卡片",
    )
    kind, message = _card_action_toast({"ok": False, "message": "TOKEN=secret " + "错误" * 1000})
    assert kind == "error" and len(message) <= 80 and "secret" not in message


def test_workspace_primary_responder_accepts_unmentioned_group_message() -> None:
    resolution = SimpleNamespace(
        binding=SimpleNamespace(primary_responder="kanban_agent"),
        index_route=SimpleNamespace(purpose="kanban_agent"),
    )
    secondary = SimpleNamespace(
        binding=SimpleNamespace(primary_responder="kanban_agent"),
        index_route=SimpleNamespace(purpose="run_manager"),
    )

    assert workspace_message_is_addressed(
        mention_ids=[],
        bot_open_id="ou_kanban",
        chat_type="group",
        resolution=resolution,
    ) is True
    assert workspace_message_is_addressed(
        mention_ids=[],
        bot_open_id="ou_runm",
        chat_type="group",
        resolution=secondary,
    ) is False
    assert workspace_message_is_addressed(
        mention_ids=["ou_runm"],
        bot_open_id="ou_runm",
        chat_type="group",
        resolution=secondary,
    ) is True


def test_issue_only_connection_accepts_plain_command_without_mention() -> None:
    assert workspace_message_is_addressed(
        mention_ids=[], bot_open_id="ou_bot", chat_type="group", resolution=None,
        issue_only=True,
    ) is True


def test_bridge_projection_loop_is_wakeable_and_stoppable():
    calls: list[str] = []
    loop = BridgeProjectionLoop(calls.append, interval_seconds=10)
    assert loop.tick_once() is False
    loop.add_target("oc_owner")
    assert loop.tick_once() is True
    assert calls == ["oc_owner"]
    loop.start()
    loop.stop()


def test_bridge_projection_loop_can_start_urgent_projection():
    calls: list[str] = []
    done = threading.Event()

    def tick(target: str) -> None:
        calls.append(target)
        done.set()

    loop = BridgeProjectionLoop(tick, interval_seconds=10)
    loop.project_now("oc_urgent")
    assert done.wait(2)
    assert calls == ["oc_urgent"]


def test_bridge_projection_loop_round_robins_multiple_targets():
    calls: list[str] = []
    loop = BridgeProjectionLoop(calls.append, interval_seconds=10)
    loop.add_target("oc_second")
    loop.add_target("oc_first")

    assert loop.tick_once() is True
    assert loop.tick_once() is True
    assert loop.tick_once() is True
    assert calls == ["oc_first", "oc_second", "oc_first"]


def test_configured_projection_targets_respect_app_scope():
    context = SimpleNamespace(config=SimpleNamespace(
        integrations=SimpleNamespace(feishu_routing={
            "cli_this:oc_owner": SimpleNamespace(target="kanban_agent"),
            "cli_other:oc_other": SimpleNamespace(target="kanban_agent"),
            "oc_shared#ou_bot": SimpleNamespace(target="channel"),
            "*": SimpleNamespace(target="channel"),
        }),
    ))
    assert _configured_projection_targets(context, "cli_this") == [
        "oc_owner",
        "oc_shared",
    ]


def test_bridge_projection_pump_includes_control_loop_cards(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []
    delivery_skips: list[set[str]] = []

    def fake(name):
        def run(state_dir, transport, **kwargs):
            calls.append((name, str(kwargs.get("receive_id") or "exact")))
            if name == "delivery":
                delivery_skips.append(set(kwargs.get("skip_request_ids") or ()))
            result = {"sent": [name], "updated": []}
            if name == "stream":
                result["visible_request_ids"] = ["reply-stream"]
            return result
        return run

    monkeypatch.setattr(
        "zf.integrations.feishu.kanban_plan_card.push_kanban_plan_cards_once",
        fake("plan"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.kanban_proposal_card.push_kanban_proposal_cards_once",
        fake("proposal"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_question_card.push_channel_question_cards_once",
        fake("question"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_progress_card.push_channel_progress_cards_once",
        fake("progress"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_result_card.push_channel_result_cards_once",
        fake("result"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.delivery_card.push_delivery_cards_once",
        fake("delivery"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.stream_card.push_stream_card_once",
        fake("stream"),
    )
    context = SimpleNamespace(
        state_dir=tmp_path / ".zf",
        config=SimpleNamespace(integrations=SimpleNamespace(feishu_identity=None)),
    )

    counts = push_bridge_projections_once(context, object(), "oc_owner")

    assert counts == {
        "plans": 1,
        "proposals": 1,
        "questions": 1,
        "progress": 1,
        "results": 1,
        "delivery": 1,
        "stream": 1,
    }
    assert calls == [
        ("plan", "oc_owner"),
        ("proposal", "oc_owner"),
        ("question", "oc_owner"),
        ("progress", "exact"),
        ("result", "exact"),
        ("stream", "oc_owner"),
        ("delivery", "oc_owner"),
    ]
    assert delivery_skips == [{"reply-stream"}]


def test_bridge_projection_failure_does_not_starve_later_cards(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "zf.integrations.feishu.kanban_plan_card.push_kanban_plan_cards_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("plan offline")),
    )

    def succeeding(name):
        def run(*args, **kwargs):
            calls.append(name)
            return {"sent": [], "updated": []}
        return run

    monkeypatch.setattr(
        "zf.integrations.feishu.kanban_proposal_card.push_kanban_proposal_cards_once",
        succeeding("proposal"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_question_card.push_channel_question_cards_once",
        succeeding("question"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_progress_card.push_channel_progress_cards_once",
        succeeding("progress"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_result_card.push_channel_result_cards_once",
        succeeding("result"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.delivery_card.push_delivery_cards_once",
        succeeding("delivery"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.stream_card.push_stream_card_once",
        succeeding("stream"),
    )
    context = SimpleNamespace(
        state_dir=tmp_path / ".zf",
        config=SimpleNamespace(integrations=SimpleNamespace(feishu_identity=None)),
    )

    counts = push_bridge_projections_once(context, object(), "oc_owner")

    assert counts["plans"] == 0
    assert calls == [
        "proposal",
        "question",
        "progress",
        "result",
        "stream",
        "delivery",
    ]


def test_bridge_projection_skips_kanban_controls_when_disabled(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake(name):
        def run(*args, **kwargs):
            calls.append(name)
            return {"sent": [], "updated": []}
        return run

    monkeypatch.setattr(
        "zf.integrations.feishu.kanban_plan_card.push_kanban_plan_cards_once",
        fake("plan"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.kanban_proposal_card.push_kanban_proposal_cards_once",
        fake("proposal"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_question_card.push_channel_question_cards_once",
        fake("question"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_progress_card.push_channel_progress_cards_once",
        fake("progress"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_result_card.push_channel_result_cards_once",
        fake("result"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.delivery_card.push_delivery_cards_once",
        fake("delivery"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.stream_card.push_stream_card_once",
        fake("stream"),
    )
    context = SimpleNamespace(
        state_dir=tmp_path / ".zf",
        config=SimpleNamespace(integrations=SimpleNamespace(feishu_identity=None)),
    )

    counts = push_bridge_projections_once(
        context,
        object(),
        "oc_owner",
        include_kanban_controls=False,
    )

    assert counts["plans"] == 0
    assert counts["proposals"] == 0
    assert calls == ["question", "progress", "result", "stream", "delivery"]


def test_bridge_projection_skips_channel_controls_when_disabled(tmp_path, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake(name):
        def run(*args, **kwargs):
            calls.append((name, kwargs))
            return {"sent": [], "updated": []}
        return run

    monkeypatch.setattr(
        "zf.integrations.feishu.kanban_plan_card.push_kanban_plan_cards_once",
        fake("plan"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.kanban_proposal_card.push_kanban_proposal_cards_once",
        fake("proposal"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_question_card.push_channel_question_cards_once",
        fake("question"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_progress_card.push_channel_progress_cards_once",
        fake("progress"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.channel_result_card.push_channel_result_cards_once",
        fake("result"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.delivery_card.push_delivery_cards_once",
        fake("delivery"),
    )
    monkeypatch.setattr(
        "zf.integrations.feishu.stream_card.push_stream_card_once",
        fake("stream"),
    )
    context = SimpleNamespace(
        state_dir=tmp_path / ".zf",
        config=SimpleNamespace(integrations=SimpleNamespace(feishu_identity=None)),
    )

    counts = push_bridge_projections_once(
        context,
        object(),
        "oc_owner",
        include_kanban_controls=False,
        include_channel_controls=False,
        member_id="run-manager",
    )

    assert counts == {
        "plans": 0,
        "proposals": 0,
        "questions": 0,
        "progress": 0,
        "results": 0,
        "stream": 0,
        "delivery": 0,
    }
    assert [name for name, _kwargs in calls] == ["stream", "delivery"]
    assert all(kwargs.get("member") == "run-manager" for _name, kwargs in calls)


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


def test_parallel_feishu_threads_use_independent_debounce_scopes():
    calls: list = []
    done = threading.Event()

    def fake_dispatch(event, *, context, transport=None):
        calls.append(event)
        fut: concurrent.futures.Future = concurrent.futures.Future()
        fut.set_result({"status": "replied"})
        if len(calls) == 2:
            done.set()
        return fut

    bridge = BridgeWatch(
        context=None,
        transport=None,
        debounce_ms=40,
        dispatch=fake_dispatch,
    )
    bridge.on_message({
        **_msg("alpha", "m-a"),
        "root_message_id": "root-a",
    })
    bridge.on_message({
        **_msg("beta", "m-b"),
        "root_message_id": "root-b",
    })

    assert done.wait(2.0)
    bridge.shutdown()
    assert sorted(event.payload["text"] for event in calls) == ["alpha", "beta"]
    assert sorted(event.payload["root_message_id"] for event in calls) == [
        "root-a",
        "root-b",
    ]


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
    assert member["permission_profile"] == "read_only"
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

    assert result["kind"] == "kanban_agent_canonical_status"
    channel = project_channel(ctx.state_dir, "feishu-kanban_agent-oc_group") or {}
    member = next(
        member for member in channel.get("members", [])
        if member.get("member_id") == "zf-product-manager"
    )
    assert member["channel_role"] == "owner_delegate"
    assert member["permission_profile"] == "read_only"
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
