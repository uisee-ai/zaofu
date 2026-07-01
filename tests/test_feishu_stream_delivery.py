"""feishu-stream B3: throttled streaming delivery + events.jsonl-unchanged."""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.stream_card import sync_stream_card


def _w(sd: Path) -> EventWriter:
    return EventWriter(EventLog(sd / "events.jsonl"))


def _delta(rid: str, chunk: str) -> ZfEvent:
    return ZfEvent(type="agent.session.part.delta", actor="dev",
                   payload={"request_id": rid, "kind": "text", "delta": chunk})


def _sync(sd, ledger, sent, updated):
    return sync_stream_card(
        sd,
        send_card=lambda c: (sent.append(c), f"msg-{len(sent)}")[1],
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
