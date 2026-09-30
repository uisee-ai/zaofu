"""feishu-stream B3: throttled streaming delivery + events.jsonl-unchanged."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.stream_card import (
    push_stream_card_once,
    sync_stream_card,
)
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.runtime.channel_sidecar import channel_message_event_payload


def _w(sd: Path) -> EventWriter:
    return EventWriter(EventLog(sd / "events.jsonl"))


def _delta(rid: str, chunk: str, *, member_id: str = "") -> ZfEvent:
    return ZfEvent(type="agent.session.part.delta", actor="dev",
                   payload={"request_id": rid, "kind": "text", "delta": chunk,
                            "member_id": member_id})


def _sync(sd, ledger, sent, updated):
    return sync_stream_card(
        sd,
        send_card=lambda c, state: (sent.append(c), f"msg-{len(sent)}")[1],
        update_card=lambda mid, c, seq=0: updated.append((mid, seq)),
        ledger=ledger)


def test_send_once_then_update_in_place_with_monotonic_seq(tmp_path):
    sd = tmp_path / ".zf"; sd.mkdir()
    w = _w(sd)
    w.append(_delta("R1", "Hel"))
    ledger, sent, updated = {}, [], []
    r1 = _sync(sd, ledger, sent, updated)
    assert r1["sent"] == ["R1"] and not updated
    assert sent[0]["config"]["streaming_mode"] is True  # typewriter on while running

    w.append(_delta("R1", "lo world"))
    r2 = _sync(sd, ledger, sent, updated)
    assert r2["updated"] == ["R1"] and updated[-1][1] == 1  # seq 1

    w.append(ZfEvent(type="channel.agent.reply.completed", actor="d",
                     payload={"request_id": "R1"}))
    r3 = _sync(sd, ledger, sent, updated)
    assert r3["updated"] == ["R1"] and updated[-1][1] == 2  # seq 2 (terminal)


def test_push_stream_card_serializes_concurrent_projection_ticks(tmp_path):
    """Fast inbound ticker and Bridge loop must share one visible stream card."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _w(state_dir).append(_delta("R-lock", "working"))

    class BlockingTransport(MockFeishuTransport):
        def __init__(self) -> None:
            super().__init__()
            self.first_send = threading.Event()
            self.release_first_send = threading.Event()
            self.send_calls = 0
            self._calls_lock = threading.Lock()

        def send_card(self, message):
            with self._calls_lock:
                self.send_calls += 1
                is_first = self.send_calls == 1
            if is_first:
                self.first_send.set()
                assert self.release_first_send.wait(timeout=3)
            return super().send_card(message)

    transport = BlockingTransport()
    results: list[dict] = []
    errors: list[BaseException] = []

    def run_push() -> None:
        try:
            results.append(push_stream_card_once(
                state_dir,
                transport,
                receive_id="oc_owner",
            ))
        except BaseException as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    first = threading.Thread(target=run_push)
    first.start()
    assert transport.first_send.wait(timeout=3)
    second = threading.Thread(target=run_push)
    second.start()
    assert transport.send_calls == 1
    transport.release_first_send.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert not errors
    assert not first.is_alive() and not second.is_alive()
    assert transport.send_calls == 1
    assert len(transport.sent_messages) == 1
    assert sum(len(result["sent"]) for result in results) == 1


def test_scoped_and_empty_projection_share_canonical_member_ledger(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    _w(state_dir).append(
        _delta("R-member", "working", member_id="zf-product-manager")
    )
    transport = MockFeishuTransport()
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def run_push(member: str) -> None:
        barrier.wait(timeout=3)
        results.append(push_stream_card_once(
            state_dir,
            transport,
            receive_id="oc_owner",
            member=member,
        ))

    scoped = threading.Thread(target=run_push, args=("zf-product-manager",))
    inferred = threading.Thread(target=run_push, args=("",))
    scoped.start()
    inferred.start()
    scoped.join(timeout=3)
    inferred.join(timeout=3)

    assert not scoped.is_alive() and not inferred.is_alive()
    assert len(transport.sent_messages) == 1
    assert sum(len(result["sent"]) for result in results) == 1
    assert {result["member"] for result in results} == {"zf-product-manager"}
    ledger_dir = state_dir / "integrations" / "feishu"
    scoped_ledger = json.loads(
        (ledger_dir / "stream_ledger-zf-product-manager.json").read_text(
            encoding="utf-8"
        )
    )
    assert scoped_ledger["stream-R-member"]["message_id"]
    shared_path = ledger_dir / "stream_ledger.json"
    shared_ledger = (
        json.loads(shared_path.read_text(encoding="utf-8"))
        if shared_path.exists()
        else {}
    )
    assert "stream-R-member" not in shared_ledger


def test_high_frequency_deltas_collapse_to_one_render(tmp_path):
    sd = tmp_path / ".zf"; sd.mkdir()
    w = _w(sd)
    for i in range(100):
        w.append(_delta("R2", f"{i} "))
    ledger, sent, updated = {}, [], []
    r = _sync(sd, ledger, sent, updated)
    # 100 deltas folded into ONE send (no update yet); not 100 transport calls
    assert r["sent"] == ["R2"] and len(sent) == 1 and not updated


def test_no_card_for_reply_without_deltas(tmp_path):
    sd = tmp_path / ".zf"; sd.mkdir()
    # a non-streaming reply: only the terminal event, no deltas → no stream card
    _w(sd).append(ZfEvent(type="channel.agent.reply.completed", actor="d",
                          payload={"request_id": "R3"}))
    ledger, sent, updated = {}, [], []
    r = _sync(sd, ledger, sent, updated)
    assert not r["sent"] and not r["updated"]


def test_internal_plan_repair_stream_is_hidden_from_feishu_chat(tmp_path):
    """The repair turn remains auditable, but must not look like another user turn."""
    sd = tmp_path / ".zf"; sd.mkdir()
    writer = _w(sd)
    writer.append(ZfEvent(
        type="channel.message.posted",
        actor="feishu-plan-repair",
        payload={
            "message_id": "feishu-plan-repair-evt-1",
            "source": "feishu-plan-repair",
        },
    ))
    writer.append(ZfEvent(
        type="agent.session.part.delta",
        actor="agent",
        payload={
            "request_id": "repair-1",
            "message_id": "feishu-plan-repair-evt-1",
            "kind": "thinking",
            "delta": "repairing",
        },
    ))
    ledger, sent, updated = {}, [], []
    result = _sync(sd, ledger, sent, updated)
    assert result["suppressed"] == ["repair-1"]
    assert result["sent"] == []
    assert sent == []


def test_existing_internal_plan_repair_stream_is_replaced_in_place(tmp_path):
    sd = tmp_path / ".zf"; sd.mkdir()
    writer = _w(sd)
    writer.append(ZfEvent(
        type="channel.message.posted",
        actor="feishu-plan-repair",
        payload={
            "message_id": "feishu-plan-repair-evt-2",
            "source": "feishu-plan-repair",
        },
    ))
    writer.append(ZfEvent(
        type="agent.session.part.delta",
        actor="agent",
        payload={
            "request_id": "repair-2",
            "message_id": "feishu-plan-repair-evt-2",
            "kind": "text",
            "delta": "internal reply",
        },
    ))
    ledger = {"stream-repair-2": {"message_id": "om-repair", "seq": 4}}
    updated: list[tuple] = []
    result = sync_stream_card(
        sd,
        send_card=lambda *_: (_ for _ in ()).throw(AssertionError("must not send")),
        update_card=lambda *args: updated.append(args),
        ledger=ledger,
    )
    assert result["suppressed"] == ["repair-2"]
    assert result["updated"] == ["repair-2"]
    assert updated[0][0] == "om-repair"
    assert "计划已由系统自动修正" in str(updated[0][1])
    assert ledger["stream-repair-2"]["suppressed"] is True


def test_idempotent_no_resend(tmp_path):
    sd = tmp_path / ".zf"; sd.mkdir()
    _w(sd).append(_delta("R4", "hi"))
    ledger, sent, updated = {}, [], []
    _sync(sd, ledger, sent, updated)
    r2 = _sync(sd, ledger, sent, updated)  # no new deltas
    assert not r2["sent"] and not r2["updated"]


def test_events_jsonl_unchanged_invariant(tmp_path):
    # The crown invariant (§5.1): streaming drives the card, never events.jsonl.
    sd = tmp_path / ".zf"; sd.mkdir()
    w = _w(sd)
    for i in range(20):
        w.append(_delta("R5", f"{i}"))
    before = len(EventLog(sd / "events.jsonl").read_all())
    ledger, sent, updated = {}, [], []
    _sync(sd, ledger, sent, updated)
    _sync(sd, ledger, sent, updated)
    after = len(EventLog(sd / "events.jsonl").read_all())
    assert after == before  # sync wrote ZERO events


def test_stream_card_wired_into_push_tick(tmp_path, monkeypatch, capsys):
    # P0-1: zf feishu push folds a reply's deltas into a streaming card.
    import yaml
    from zf.cli.main import main
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(yaml.dump({
        "version": "1.0", "project": {"name": "t", "state_dir": ".zf"},
        "roles": [{"name": "dev", "backend": "mock"}]}))
    main(["init"])
    w = _w(tmp_path / ".zf")
    w.append(_delta("R1", "hi"))
    main(["feishu", "push", "--transport", "mock", "--to", "oc_x",
          "--state-dir", str(tmp_path / ".zf")])
    assert "stream_cards_sent=1" in capsys.readouterr().out


def test_stream_card_uses_the_committed_reply_feishu_thread(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = _w(state_dir)
    writer.append(_delta("R-origin", "partial"))
    payload = channel_message_event_payload(
        state_dir,
        {
            "channel_id": "ch-origin",
            "thread_id": "om-root",
            "message_id": "msg-R-origin-reply",
            "member_id": "codex",
            "role": "assistant",
            "source": "test",
            "text": "最终可读回复。",
            "refs": {
                "request_id": "R-origin",
                "feishu": {
                    "chat_id": "oc-origin",
                    "message_id": "om-user",
                    "thread_id": "omt-thread",
                    "root_message_id": "om-root",
                },
            },
        },
        created_by="test",
    )
    writer.append(ZfEvent(
        type="channel.message.posted",
        actor="codex",
        correlation_id="ch-origin",
        payload=payload,
    ))
    writer.append(ZfEvent(
        type="channel.agent.reply.completed",
        actor="codex",
        payload={"request_id": "R-origin"},
    ))

    transport = MockFeishuTransport()
    result = push_stream_card_once(
        state_dir,
        transport,
        receive_id="oc-fallback",
    )

    assert result["sent"] == ["R-origin"]
    assert result["visible_request_ids"] == ["R-origin"]
    assert len(transport.sent_messages) == 1
    message = transport.sent_messages[0]
    assert (message.chat_id, message.thread_id) == ("oc-origin", "om-root")
    card = json.loads(message.content)
    assert "最终可读回复。" in json.dumps(card["body"]["elements"][0], ensure_ascii=False)
    assert card["body"]["elements"][0]["tag"] == "collapsible_panel"
    assert card["body"]["elements"][0]["expanded"] is False


def test_member_scoped_stream_ledger_seeds_from_legacy_without_replay(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = _w(state_dir)
    writer.append(ZfEvent(
        type="agent.session.part.delta",
        actor="agent",
        payload={
            "request_id": "R-legacy",
            "member_id": "zf-product-manager",
            "kind": "text",
            "delta": "already visible",
        },
    ))

    legacy_ledger: dict = {}
    sync_stream_card(
        state_dir,
        send_card=lambda _card, _state: "om-existing",
        update_card=lambda *_args: True,
        ledger=legacy_ledger,
        member="zf-product-manager",
    )
    ledger_dir = state_dir / "integrations" / "feishu"
    ledger_dir.mkdir(parents=True)
    (ledger_dir / "stream_ledger.json").write_text(
        json.dumps(legacy_ledger), encoding="utf-8"
    )

    transport = MockFeishuTransport()
    result = push_stream_card_once(
        state_dir,
        transport,
        receive_id="oc-origin",
        member="zf-product-manager",
    )

    assert result["sent"] == []
    assert result["updated"] == []
    assert transport.sent_messages == []
    scoped = json.loads(
        (ledger_dir / "stream_ledger-zf-product-manager.json").read_text(
            encoding="utf-8"
        )
    )
    assert scoped == legacy_ledger
