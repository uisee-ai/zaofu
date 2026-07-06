"""Fan-out dispatch runs pending replies concurrently, not one turn at a time."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime import channel_adapter
from zf.runtime.channel_adapter import dispatch_pending_replies

CH = "ch-par"


def _seed(tmp_path: Path, members: int = 3) -> tuple[Path, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    for i in range(members):
        writer.emit(
            "channel.member.invited",
            actor="web",
            correlation_id=CH,
            payload={
                "channel_id": CH, "member_id": f"m-{i}",
                "member_type": "persona_agent", "provider": "persona",
                "backend": "persona",
                "permissions": ["read", "message", "summarize"],
            },
        )
    writer.emit(
        "channel.message.posted", actor="web", correlation_id=CH,
        payload={"channel_id": CH, "thread_id": "main", "message_id": "m-1",
                 "member_id": "operator", "role": "user", "text": "@all go"},
    )
    for i in range(members):
        writer.emit(
            "channel.agent.reply.requested", actor="web", correlation_id=CH,
            payload={"channel_id": CH, "thread_id": "main",
                     "request_id": f"req-{i}", "message_id": "m-1",
                     "target_member_id": f"m-{i}", "status": "pending",
                     "source": "web"},
        )
    return state_dir, writer


def test_pending_replies_dispatch_concurrently(tmp_path: Path, monkeypatch) -> None:
    state_dir, writer = _seed(tmp_path)
    active = 0
    peak = 0
    lock = threading.Lock()

    def slow_dispatch(**kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.2)
        with lock:
            active -= 1
        return channel_adapter.ChannelDispatchResult(
            dispatched=[str(kwargs.get("request_id"))])

    monkeypatch.setattr(channel_adapter, "dispatch_reply_request", slow_dispatch)
    start = time.monotonic()
    result = dispatch_pending_replies(
        state_dir=state_dir, writer=writer, channel_id=CH,
        actor="web", source="web")
    elapsed = time.monotonic() - start
    assert sorted(result.dispatched) == ["req-0", "req-1", "req-2"]
    assert peak >= 2, "dispatches must overlap, not run one turn at a time"
    assert elapsed < 0.5, f"3 x 0.2s dispatches took {elapsed:.2f}s — still serial"


def test_single_candidate_stays_inline(tmp_path: Path, monkeypatch) -> None:
    state_dir, writer = _seed(tmp_path, members=1)
    threads: list[str] = []

    def record_dispatch(**kwargs):
        threads.append(threading.current_thread().name)
        return channel_adapter.ChannelDispatchResult(
            dispatched=[str(kwargs.get("request_id"))])

    monkeypatch.setattr(channel_adapter, "dispatch_reply_request", record_dispatch)
    result = dispatch_pending_replies(
        state_dir=state_dir, writer=writer, channel_id=CH,
        actor="web", source="web")
    assert result.dispatched == ["req-0"]
    assert not threads[0].startswith("zf-channel-dispatch-"), \
        "single reply must not pay thread-pool overhead"
