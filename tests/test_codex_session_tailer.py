"""Phase 3 — CodexSessionTailer emits agent.* events from rollout jsonl."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.runtime.session_tailer import (
    CodexSessionTailer,
    codex_session_path,
)


@pytest.fixture
def event_log(tmp_path: Path):
    sd = tmp_path / ".zf"
    sd.mkdir()
    return EventLog(sd / "events.jsonl")


def _wait_for(predicate, timeout=2.0):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _lines(path: Path, lines: list[dict]):
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


class TestCodexResponseItemParsing:
    def test_reasoning_emits_thinking(self, tmp_path, event_log):
        f = tmp_path / "rollout.jsonl"
        tailer = CodexSessionTailer(event_log)
        tailer.tail("dev-1", f)
        try:
            _lines(f, [{
                "type": "response_item",
                "payload": {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "Let me plan."}],
                },
            }])
            assert _wait_for(
                lambda: any(
                    e.type == "agent.thinking"
                    and e.actor == "dev-1"
                    and "plan" in e.payload.get("text", "")
                    for e in event_log.read_all()
                )
            )
        finally:
            tailer.stop()

    def test_function_call_emits_tool_use(self, tmp_path, event_log):
        f = tmp_path / "rollout.jsonl"
        tailer = CodexSessionTailer(event_log)
        tailer.tail("dev-1", f)
        try:
            _lines(f, [{
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "call_id": "call_xyz",
                    "arguments": '{"command":"ls"}',
                },
            }])
            assert _wait_for(
                lambda: any(
                    e.type == "agent.tool.use"
                    and e.payload.get("tool") == "shell"
                    and e.payload.get("tool_use_id") == "call_xyz"
                    for e in event_log.read_all()
                )
            )
        finally:
            tailer.stop()

    def test_function_call_output_emits_tool_result(self, tmp_path, event_log):
        f = tmp_path / "rollout.jsonl"
        tailer = CodexSessionTailer(event_log)
        tailer.tail("dev-1", f)
        try:
            _lines(f, [{
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_xyz",
                    "output": "file1\nfile2",
                },
            }])
            assert _wait_for(
                lambda: any(
                    e.type == "agent.tool.result"
                    and "file1" in e.payload.get("content", "")
                    for e in event_log.read_all()
                )
            )
        finally:
            tailer.stop()

    def test_assistant_message_emits_agent_text(self, tmp_path, event_log):
        f = tmp_path / "rollout.jsonl"
        tailer = CodexSessionTailer(event_log)
        tailer.tail("dev-1", f)
        try:
            _lines(f, [{
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done."}],
                },
            }])
            assert _wait_for(
                lambda: any(
                    e.type == "agent.text"
                    and "Done" in e.payload.get("text", "")
                    for e in event_log.read_all()
                )
            )
        finally:
            tailer.stop()

    def test_user_message_skipped(self, tmp_path, event_log):
        f = tmp_path / "rollout.jsonl"
        tailer = CodexSessionTailer(event_log)
        tailer.tail("dev-1", f)
        try:
            _lines(f, [{
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "a prompt"}],
                },
            }])
            time.sleep(0.8)
            assert not any(
                e.type.startswith("agent.")
                for e in event_log.read_all()
            )
        finally:
            tailer.stop()


class TestCodexEventMsgIgnored:
    def test_task_complete_emits_provider_turn_closed(
        self, tmp_path, event_log,
    ):
        f = tmp_path / "rollout.jsonl"
        tailer = CodexSessionTailer(event_log)
        tailer.tail("dev-1", f)
        try:
            _lines(f, [{
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-123",
                    "completed_at": 1785210165,
                    "duration_ms": 225000,
                    "last_agent_message": "large provider-owned body",
                },
            }])
            assert _wait_for(
                lambda: any(
                    e.type == "provider.turn.closed"
                    and e.actor == "dev-1"
                    and e.payload.get("turn_id") == "turn-123"
                    and e.payload.get("backend") == "codex"
                    and "last_agent_message" not in e.payload
                    for e in event_log.read_all()
                )
            )
        finally:
            tailer.stop()

    def test_token_count_not_emitted_as_agent_event(
        self, tmp_path, event_log,
    ):
        # CodexSessionReader handles token_count for cost tracking;
        # tailer must not emit it as telemetry.
        f = tmp_path / "rollout.jsonl"
        tailer = CodexSessionTailer(event_log)
        tailer.tail("dev-1", f)
        try:
            _lines(f, [{
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"model_context_window": 200000,
                             "last_token_usage": {"input_tokens": 100}},
                },
            }])
            time.sleep(0.8)
            assert not any(
                e.type.startswith("agent.")
                for e in event_log.read_all()
            )
        finally:
            tailer.stop()

    def test_session_meta_not_emitted(self, tmp_path, event_log):
        f = tmp_path / "rollout.jsonl"
        tailer = CodexSessionTailer(event_log)
        tailer.tail("dev-1", f)
        try:
            _lines(f, [{"type": "session_meta",
                        "payload": {"id": "uuid", "cwd": "/x"}}])
            time.sleep(0.8)
            assert not any(
                e.type.startswith("agent.")
                for e in event_log.read_all()
            )
        finally:
            tailer.stop()


class TestCodexSessionPath:
    def test_returns_none_when_no_match(self, monkeypatch):
        assert codex_session_path("ffffffff-ffff-ffff-ffff-ffffffffffff") is None


@pytest.mark.parametrize("newer_turn", [False, True])
def test_interrupted_turn_releases_only_its_liveness_fence(event_log, newer_turn):
    from zf.core.events.model import ZfEvent
    from zf.runtime.provider_turn_liveness import active_codex_turn_from_events

    event_log.append(ZfEvent(type="codex.hook.user_prompt_submit", actor="critic",
        payload={"session_id": "session", "turn_id": "old-turn"}))
    if newer_turn:
        event_log.append(ZfEvent(type="codex.hook.user_prompt_submit", actor="critic",
            payload={"session_id": "session", "turn_id": "new-turn"}))
    CodexSessionTailer(event_log)._emit_from_line("critic", json.dumps({
        "type": "event_msg", "timestamp": "2026-11-09T15:19:09.415Z",
        "payload": {"type": "turn_aborted", "turn_id": "old-turn",
                    "reason": "interrupted", "last_agent_message": "private-body"},
    }))
    events = event_log.read_all()
    closed = [e for e in events if e.type == "provider.turn.closed"]
    assert len(closed) == 1
    assert closed[0].payload["provider_status"] == "cancelled"
    assert closed[0].payload["provider_event"] == "turn_aborted"
    assert "private-body" not in json.dumps(closed[0].payload)
    active = active_codex_turn_from_events(events, "critic")
    assert (active["turn_id"] if active else None) == ("new-turn" if newer_turn else None)
    assert not any(e.type in {"task.done", "fanout.synth.completed"} for e in events)


def test_interruption_without_turn_id_cannot_release_liveness(event_log):
    CodexSessionTailer(event_log)._emit_from_line("critic", json.dumps({
        "type": "event_msg", "payload": {"type": "turn_aborted", "reason": "interrupted"},
    }))
    assert not event_log.read_all()
