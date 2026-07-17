"""Tests for F-WEB-MVP-01: SSE streaming endpoint.

We don't use TestClient.stream here because the SSE generator runs
forever (`while True`) and `iter_raw` blocks indefinitely. Instead
we drive `_tail_events` directly with a mock Request that flips
`is_disconnected` after the data we want to verify has been emitted.
This is faster, deterministic, and exercises the same code path the
HTTP layer would.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner
from zf.web.server import _tail_events


class _FakeRequest:
    """Mock fastapi.Request — only need is_disconnected()."""
    def __init__(self, disconnect_after: int = 4) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._disconnect_after


async def _collect(
    state_dir: Path,
    *,
    disconnect_after: int = 4,
    event_log: EventLog | None = None,
    cursor: int | None = None,
) -> bytes:
    req = _FakeRequest(disconnect_after=disconnect_after)
    chunks: list[bytes] = []
    async for chunk in _tail_events(
        state_dir,
        req,
        event_log=event_log,
        cursor=cursor,
    ):
        chunks.append(chunk)
        if len(chunks) > 50:  # safety brake
            break
    return b"".join(chunks)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]")
    return sd


@pytest.mark.asyncio
async def test_initial_connect_comment(state_dir):
    """No events.jsonl, but stream still emits ': connected' opener."""
    out = await _collect(state_dir, disconnect_after=2)
    assert b": connected" in out


@pytest.mark.asyncio
async def test_existing_events_forwarded(state_dir):
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task.created", actor="zf-cli",
                       task_id="T1", payload={}))
    log.append(ZfEvent(type="task.dispatched", actor="orch",
                       task_id="T1", payload={"role": "dev"}))

    out = await _collect(state_dir, disconnect_after=4)
    text = out.decode("utf-8", errors="replace")
    data_lines = [
        l[len("data: "):]
        for l in text.splitlines()
        if l.startswith("data: ")
    ]
    types = [json.loads(l)["type"] for l in data_lines]
    assert "task.created" in types
    assert "task.dispatched" in types
    assert "id: 1" in text
    assert "id: 2" in text


@pytest.mark.asyncio
async def test_cursor_replays_missed_events(state_dir):
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task.created", task_id="T1"))
    log.append(ZfEvent(type="task.dispatched", task_id="T1"))
    log.append(ZfEvent(type="dev.build.done", task_id="T1"))

    out = await _collect(state_dir, disconnect_after=2, cursor=1)

    text = out.decode("utf-8", errors="replace")
    data_lines = [
        l[len("data: "):]
        for l in text.splitlines()
        if l.startswith("data: ")
    ]
    types = [json.loads(l)["type"] for l in data_lines]
    assert types == ["task.dispatched", "dev.build.done"]
    assert "id: 2" in text
    assert "id: 3" in text


@pytest.mark.asyncio
async def test_cursor_gap_emits_degraded_signal(state_dir):
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="task.created", task_id="T1")
    )

    out = await _collect(state_dir, disconnect_after=2, cursor=99)

    text = out.decode("utf-8", errors="replace")
    assert "event: stream.gap" in text
    assert "cursor is outside active replay window" in text


@pytest.mark.asyncio
async def test_cursor_replay_uses_global_sequence_across_archive(state_dir):
    path = state_dir / "events.jsonl"
    log = EventLog(path)
    log.append(ZfEvent(type="task.created", task_id="T1"))
    log.append(ZfEvent(type="task.dispatched", task_id="T1"))
    archive_dir = state_dir / "events"
    archive_dir.mkdir()
    path.rename(archive_dir / "2026-07-12.jsonl")
    EventLog(path).append(ZfEvent(type="dev.build.done", task_id="T1"))
    EventLog(path).append(ZfEvent(type="test.passed", task_id="T1"))

    out = await _collect(state_dir, disconnect_after=2, cursor=2)

    text = out.decode("utf-8", errors="replace")
    assert "event: stream.gap" not in text
    assert "id: 3" in text
    assert "id: 4" in text
    assert '"type":"dev.build.done"' in text
    assert '"type":"test.passed"' in text


@pytest.mark.asyncio
async def test_segmented_log_current_global_cursor_does_not_degrade(state_dir):
    archive_dir = state_dir / "events"
    archive_dir.mkdir()
    EventLog(archive_dir / "2026-07-12.jsonl").append(
        ZfEvent(type="task.created", task_id="T1")
    )
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="task.dispatched", task_id="T1")
    )

    out = await _collect(state_dir, disconnect_after=2, cursor=2)

    text = out.decode("utf-8", errors="replace")
    assert "event: stream.gap" not in text


@pytest.mark.asyncio
async def test_cursor_before_active_window_emits_gap(state_dir):
    archive_dir = state_dir / "events"
    archive_dir.mkdir()
    archive = EventLog(archive_dir / "2026-07-12.jsonl")
    archive.append(ZfEvent(type="task.created", task_id="T1"))
    archive.append(ZfEvent(type="task.dispatched", task_id="T1"))
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="dev.build.done", task_id="T1")
    )

    out = await _collect(state_dir, disconnect_after=2, cursor=1)

    text = out.decode("utf-8", errors="replace")
    assert "event: stream.gap" in text
    assert '"current": 3' in text


@pytest.mark.asyncio
async def test_cursor_gap_reports_global_current_after_archive(state_dir):
    path = state_dir / "events.jsonl"
    EventLog(path).append(ZfEvent(type="task.created", task_id="T1"))
    archive_dir = state_dir / "events"
    archive_dir.mkdir()
    path.rename(archive_dir / "2026-07-12.jsonl")
    EventLog(path).append(ZfEvent(type="dev.build.done", task_id="T1"))

    out = await _collect(state_dir, disconnect_after=2, cursor=99)

    text = out.decode("utf-8", errors="replace")
    assert "event: stream.gap" in text
    assert '"current": 2' in text


@pytest.mark.asyncio
async def test_live_sequence_does_not_reset_when_active_log_rotates(state_dir):
    path = state_dir / "events.jsonl"
    EventLog(path).append(ZfEvent(type="task.created", task_id="T1"))
    request = _FakeRequest(disconnect_after=20)
    stream = _tail_events(state_dir, request, cursor=1)
    try:
        assert b": connected" in await anext(stream)
        assert b": ping" in await anext(stream)

        archive_dir = state_dir / "events"
        archive_dir.mkdir()
        path.rename(archive_dir / "2026-07-12.jsonl")
        EventLog(path).append(ZfEvent(type="dev.build.done", task_id="T1"))

        chunk = await asyncio.wait_for(anext(stream), timeout=2)
        text = chunk.decode("utf-8", errors="replace")
        assert "id: 2" in text
        assert '"type":"dev.build.done"' in text
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_signed_events_forwarded_as_plain_events(state_dir):
    signer = EventSigner(b"secret")
    log = EventLog(state_dir / "events.jsonl", signer=signer)
    log.append(ZfEvent(type="task.created", actor="zf-cli", task_id="T1"))

    out = await _collect(
        state_dir,
        disconnect_after=4,
        event_log=EventLog(state_dir / "events.jsonl", signer=signer),
    )

    text = out.decode("utf-8", errors="replace")
    data_lines = [
        line[len("data: "):]
        for line in text.splitlines()
        if line.startswith("data: ")
    ]
    assert [json.loads(line)["type"] for line in data_lines] == ["task.created"]
    assert all("sig" not in json.loads(line) for line in data_lines)


@pytest.mark.asyncio
async def test_sse_redacts_obvious_secrets(state_dir):
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="agent.tool.result",
        actor="dev",
        payload={"output": "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz"},
    ))

    out = await _collect(state_dir, disconnect_after=4)

    text = out.decode("utf-8", errors="replace")
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in text
    assert "[REDACTED" in text


@pytest.mark.asyncio
async def test_tampered_signed_event_skipped(state_dir):
    signer = EventSigner(b"secret")
    log = EventLog(state_dir / "events.jsonl", signer=signer)
    log.append(ZfEvent(type="task.created", actor="zf-cli", task_id="T1"))
    raw = (state_dir / "events.jsonl").read_text()
    (state_dir / "events.jsonl").write_text(raw.replace("task.created", "task.done"))

    out = await _collect(
        state_dir,
        disconnect_after=4,
        event_log=EventLog(state_dir / "events.jsonl", signer=signer),
    )

    text = out.decode("utf-8", errors="replace")
    assert "data: " not in text


@pytest.mark.asyncio
async def test_malformed_jsonl_line_skipped(state_dir):
    path = state_dir / "events.jsonl"
    path.write_text(
        '{"type":"task.created","id":"e1","ts":"2026-04-27T00:00:00",'
        '"actor":"x","task_id":"T1","payload":{},'
        '"causation_id":null,"correlation_id":null}\n'
        'garbage line not json\n'
        '{"type":"task.dispatched","id":"e2","ts":"2026-04-27T00:00:01",'
        '"actor":"x","task_id":"T1","payload":{},'
        '"causation_id":null,"correlation_id":null}\n'
    )
    out = await _collect(state_dir, disconnect_after=4)
    text = out.decode("utf-8", errors="replace")
    data_lines = [
        l[len("data: "):]
        for l in text.splitlines()
        if l.startswith("data: ")
    ]
    # Garbage line silently dropped, the two valid ones forwarded
    assert len(data_lines) == 2
    for l in data_lines:
        json.loads(l)  # parse must succeed


@pytest.mark.asyncio
async def test_missing_events_file_does_not_500(state_dir):
    """No events.jsonl at all — generator must not raise."""
    out = await _collect(state_dir, disconnect_after=2)
    # We at least got the connect comment without any exception
    assert b": connected" in out


@pytest.mark.asyncio
async def test_disconnect_terminates_stream(state_dir):
    """Verify the generator actually exits when client disconnects."""
    req = _FakeRequest(disconnect_after=0)  # disconnect immediately
    chunks = []
    async for chunk in _tail_events(state_dir, req):
        chunks.append(chunk)
        if len(chunks) > 5:
            pytest.fail("generator did not terminate on disconnect")
    # Generator returned cleanly
    assert True
