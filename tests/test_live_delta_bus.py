"""doc 106 B axis P0: token deltas ride the ephemeral LiveDeltaBus, never
events.jsonl. Committed truth keeps the aggregate text (run.completed
final_text / kanban.agent.reply answer / channel.message.posted body)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter
from zf.runtime.live_delta_bus import LiveDeltaBus, live_delta_bus_for_writer


class _Msg:
    def __init__(self, type_: str, content: str = "") -> None:
        self.type = type_
        self.content = content


def _emitter(tmp_path: Path) -> tuple[AgentSessionStreamEmitter, EventLog]:
    log = EventLog(tmp_path / "events.jsonl")
    emitter = AgentSessionStreamEmitter(
        writer=EventWriter(log),
        identity=AgentSessionIdentity(
            run_id="run-1", thread_id="th-1", source="test", actor="web",
        ),
        flush_interval_s=0.0,
    )
    return emitter, log


# ---------------------------------------------------------------- bus core


def test_bus_publish_read_sweep_roundtrip(tmp_path: Path):
    bus = LiveDeltaBus(tmp_path)
    bus.publish("agent.session.part.delta", {"delta": "hel"}, key="run-1")
    bus.publish("agent.session.part.delta", {"delta": "lo"}, key="run-1")

    rows, cursors = bus.read_since()
    assert [r.payload["delta"] for r in rows] == ["hel", "lo"]
    assert rows[0].type == "agent.session.part.delta"

    # cursor advances — nothing new on the second read
    again, cursors = bus.read_since(cursors)
    assert again == []
    bus.publish("agent.session.part.delta", {"delta": "!"}, key="run-1")
    fresh, _ = bus.read_since(cursors)
    assert [r.payload["delta"] for r in fresh] == ["!"]

    # TTL sweep removes idle files
    path = next((tmp_path / "live" / "deltas").glob("*.jsonl"))
    old = time.time() - 3600
    os.utime(path, (old, old))
    assert bus.sweep(ttl_seconds=900) == 1
    assert not list((tmp_path / "live" / "deltas").glob("*.jsonl"))


def test_bus_redacts_payloads(tmp_path: Path):
    bus = LiveDeltaBus(tmp_path)
    bus.publish("agent.session.part.delta", {"delta": "sk-abcdefghijklmnop1234"}, key="r")
    raw = next((tmp_path / "live" / "deltas").glob("*.jsonl")).read_text()
    assert "sk-abcdefghijklmnop1234" not in raw


# ------------------------------------------------- emitter routing


def test_text_deltas_go_to_bus_not_ledger(tmp_path: Path):
    emitter, log = _emitter(tmp_path)
    emitter.start()
    emitter.emit_message(_Msg("text", "hello "))
    emitter.emit_message(_Msg("text", "world"))
    emitter.flush()

    ledger_types = [e.type for e in log.read_all()]
    assert "agent.session.part.delta" not in ledger_types, (
        "token deltas must not enter events.jsonl (doc 106 B axis)"
    )
    rows, _ = LiveDeltaBus(tmp_path).read_since()
    assert any(r.type == "agent.session.part.delta" for r in rows)


def test_tool_parts_stay_in_ledger(tmp_path: Path):
    emitter, log = _emitter(tmp_path)
    emitter.start()
    emitter.emit_message(_Msg("tool_use", "pytest -q"))

    ledger_types = [e.type for e in log.read_all()]
    assert "agent.session.part.delta" in ledger_types, (
        "non-token parts remain interaction evidence in the ledger"
    )


def test_run_completed_carries_final_text(tmp_path: Path):
    emitter, log = _emitter(tmp_path)
    emitter.start()
    emitter.emit_message(_Msg("text", "hello "))
    emitter.emit_message(_Msg("text", "world"))
    completed = emitter.complete(status="completed")

    assert completed.payload.get("final_text") == "hello world" or str(
        completed.payload.get("final_text_ref") or completed.payload.get("final_text") or ""
    ), "aggregate text must land on run.completed once deltas are off-ledger"
    stored = [e for e in log.read_all() if e.type == "agent.session.run.completed"]
    assert stored and (
        stored[0].payload.get("final_text") == "hello world"
        or stored[0].payload.get("final_text_ref")
    )


# ------------------------------------------------- kanban headless emitter


def test_kanban_turn_deltas_bus_only_no_dual_emit(tmp_path: Path):
    from zf.core.events.model import ZfEvent
    from zf.web.server import _HeadlessDeltaEmitter
    from zf.web.headless_agent import HeadlessMessage

    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    started = writer.emit("kanban.agent.turn.started", actor="web", payload={})
    user = ZfEvent(type="user.message", actor="web", correlation_id="conv-1")
    emitter = _HeadlessDeltaEmitter(
        writer=writer, task_id=None, turn_started=started, user_message=user,
        turn_id="turn-1", thread_key="main", project_id="p", conversation_id="c",
        backend="codex", flush_interval_s=0.0,
    )
    emitter._emit_one(HeadlessMessage(type="text", content="hi"))

    ledger_types = [e.type for e in log.read_all()]
    assert "kanban.agent.turn.delta" not in ledger_types
    assert "kanban.agent.message.delta" not in ledger_types, (
        "dual message.delta emit must be gone entirely"
    )
    rows, _ = LiveDeltaBus(tmp_path).read_since()
    assert [r.type for r in rows] == ["kanban.agent.turn.delta"]
    assert rows[0].payload["turn_id"] == "turn-1"


def test_bus_for_writer_derives_state_dir(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    bus = live_delta_bus_for_writer(EventWriter(log))
    assert bus is not None
    bus.publish("agent.session.part.delta", {"delta": "x"}, key="k")
    assert list((tmp_path / "live" / "deltas").glob("*.jsonl"))


# ------------------------------------------------- SSE merge


def test_sse_tail_merges_live_rows_and_skips_backlog(tmp_path: Path):
    """2026-07-16 P1: a fresh SSE subscriber starts at the current end of the
    delta scratch — the backlog of already-finished turns must NOT replay
    (it resurrected live streaming UI on completed runs). Rows published
    AFTER the connect still ride the wire."""
    import asyncio

    from zf.web.projections.events import _tail_events

    log = EventLog(tmp_path / "events.jsonl")
    from zf.core.events.model import ZfEvent
    log.append(ZfEvent(type="loop.started", actor="zf-cli"))
    bus = LiveDeltaBus(tmp_path)
    bus.publish("kanban.agent.turn.delta", {"delta": "stale-backlog!"}, key="t0")

    class _Req:
        def __init__(self) -> None:
            self.calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            if self.calls == 2:
                bus.publish("kanban.agent.turn.delta", {"delta": "streaming!"}, key="t1")
            return self.calls > 4

    async def _collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in _tail_events(tmp_path, _Req()):
            chunks.append(chunk.decode("utf-8", errors="replace"))
        return chunks

    chunks = asyncio.run(_collect())
    joined = "".join(chunks)
    assert "loop.started" in joined, "committed events still flow"
    assert "stale-backlog!" not in joined, "pre-connect delta backlog must not replay"
    assert "streaming!" in joined, "post-connect live rows still ride the SSE wire"
    payloads = [c for c in chunks if "streaming!" in c]
    assert payloads and json.loads(
        payloads[0].split("data: ", 1)[1].split("\n")[0]
    )["payload"]["delta"] == "streaming!"


def test_bus_current_cursors_and_discard(tmp_path: Path):
    bus = LiveDeltaBus(tmp_path)
    bus.publish("agent.session.part.delta", {"delta": "old"}, key="run-1")
    cursors = bus.current_cursors()
    rows, cursors = bus.read_since(cursors)
    assert rows == [], "current_cursors starts past the existing backlog"
    bus.publish("agent.session.part.delta", {"delta": "new"}, key="run-1")
    rows, _ = bus.read_since(cursors)
    assert [r.payload["delta"] for r in rows] == ["new"]

    bus.discard("run-1")
    assert not list((tmp_path / "live" / "deltas").glob("*.jsonl"))
    fresh, _ = LiveDeltaBus(tmp_path).read_since()
    assert fresh == []


def test_complete_discards_live_scratch(tmp_path: Path):
    """Terminal runs drop their delta scratch so new subscribers can never
    replay a finished turn even within the TTL window."""
    emitter, _log = _emitter(tmp_path)
    emitter.start()
    emitter.emit_message(_Msg("text", "hello"))
    emitter.flush()
    assert list((tmp_path / "live" / "deltas").glob("*.jsonl"))
    emitter.complete(status="completed")
    assert not list((tmp_path / "live" / "deltas").glob("*.jsonl"))
