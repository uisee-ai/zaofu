"""Phase 2 — ClaudeSessionTailer emits agent.* events from session jsonl."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.runtime.session_tailer import ClaudeSessionTailer, claude_session_path


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


class TestPathConstruction:
    def test_claude_session_path_escapes_cwd(self):
        p = claude_session_path("/tmp/my-proj", "abc-def")
        assert str(p).endswith("/-tmp-my-proj/abc-def.jsonl")


class TestAssistantMessageEvents:
    def _write(self, path: Path, lines: list[dict]):
        path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    def test_thinking_block_emits_agent_thinking(
        self, tmp_path, event_log,
    ):
        session_file = tmp_path / "sess.jsonl"
        tailer = ClaudeSessionTailer(event_log)
        tailer.tail("orchestrator", session_file)
        try:
            self._write(session_file, [
                {"type": "assistant", "message": {
                    "role": "assistant", "stop_reason": "tool_use",
                    "content": [{"type": "thinking", "thinking": "Let me plan."}],
                }},
            ])
            assert _wait_for(
                lambda: any(
                    e.type == "agent.thinking" and e.actor == "orchestrator"
                    for e in event_log.read_all()
                )
            )
        finally:
            tailer.stop()

    def test_tool_use_emits_event_with_name_and_input(
        self, tmp_path, event_log,
    ):
        session_file = tmp_path / "sess.jsonl"
        tailer = ClaudeSessionTailer(event_log)
        tailer.tail("dev-1", session_file)
        try:
            self._write(session_file, [
                {"type": "assistant", "message": {
                    "role": "assistant", "stop_reason": "tool_use",
                    "content": [{
                        "type": "tool_use", "name": "Bash",
                        "id": "toolu_123",
                        "input": {"command": "ls"},
                    }],
                }},
            ])
            assert _wait_for(
                lambda: any(
                    e.type == "agent.tool.use"
                    and e.payload.get("tool") == "Bash"
                    and e.actor == "dev-1"
                    for e in event_log.read_all()
                )
            )
        finally:
            tailer.stop()

    def test_tool_result_from_user_message(
        self, tmp_path, event_log,
    ):
        session_file = tmp_path / "sess.jsonl"
        tailer = ClaudeSessionTailer(event_log)
        tailer.tail("dev-1", session_file)
        try:
            self._write(session_file, [
                {"type": "user", "message": {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "file listing",
                    }],
                }},
            ])
            assert _wait_for(
                lambda: any(
                    e.type == "agent.tool.result"
                    and "file listing" in e.payload.get("content", "")
                    for e in event_log.read_all()
                )
            )
        finally:
            tailer.stop()

    def test_final_text_block_emits_agent_text(
        self, tmp_path, event_log,
    ):
        session_file = tmp_path / "sess.jsonl"
        tailer = ClaudeSessionTailer(event_log)
        tailer.tail("orchestrator", session_file)
        try:
            self._write(session_file, [
                {"type": "assistant", "message": {
                    "role": "assistant", "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "Done."}],
                }},
            ])
            assert _wait_for(
                lambda: any(
                    e.type == "agent.text"
                    and e.payload.get("stop_reason") == "end_turn"
                    for e in event_log.read_all()
                )
            )
        finally:
            tailer.stop()


class TestSkipsCliInternal:
    def test_permission_mode_ignored(self, tmp_path, event_log):
        session_file = tmp_path / "sess.jsonl"
        tailer = ClaudeSessionTailer(event_log)
        tailer.tail("orchestrator", session_file)
        try:
            session_file.write_text(json.dumps({
                "type": "permission-mode", "mode": "bypass",
            }) + "\n")
            # Give tailer a tick
            time.sleep(0.8)
            events = event_log.read_all()
            # Only loop.started / similar; no agent.* from the skipped line
            assert not any(e.type.startswith("agent.") for e in events)
        finally:
            tailer.stop()

    def test_malformed_json_line_ignored(self, tmp_path, event_log):
        session_file = tmp_path / "sess.jsonl"
        tailer = ClaudeSessionTailer(event_log)
        tailer.tail("orchestrator", session_file)
        try:
            session_file.write_text("not-json\n{\"type\":\"permission-mode\"}\n")
            time.sleep(0.8)
            events = event_log.read_all()
            assert not any(e.type.startswith("agent.") for e in events)
        finally:
            tailer.stop()


class TestAppendedLinesPickedUp:
    def test_tail_picks_up_lines_added_after_start(
        self, tmp_path, event_log,
    ):
        session_file = tmp_path / "sess.jsonl"
        session_file.write_text("")  # exists but empty
        tailer = ClaudeSessionTailer(event_log)
        tailer.tail("orchestrator", session_file)
        try:
            time.sleep(0.2)  # let tailer reach poll loop
            with session_file.open("a") as f:
                f.write(json.dumps({
                    "type": "assistant", "message": {
                        "role": "assistant", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": "hello"}],
                    },
                }) + "\n")
            assert _wait_for(
                lambda: any(e.type == "agent.text" for e in event_log.read_all())
            )
        finally:
            tailer.stop()

    def test_preexisting_content_skipped(self, tmp_path, event_log):
        """A session file that already has content when tailer starts
        shouldn't be re-emitted (prevents restart double-emit)."""
        session_file = tmp_path / "sess.jsonl"
        session_file.write_text(json.dumps({
            "type": "assistant", "message": {
                "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "old"}],
            },
        }) + "\n")
        tailer = ClaudeSessionTailer(event_log)
        tailer.tail("orchestrator", session_file)
        try:
            time.sleep(0.8)
            # No agent.* emitted because file was already at EOF on start
            agent_events = [
                e for e in event_log.read_all()
                if e.type.startswith("agent.")
            ]
            assert agent_events == []
        finally:
            tailer.stop()
