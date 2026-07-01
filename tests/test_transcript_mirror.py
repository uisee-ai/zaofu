"""Tests for Gap #2: Transcript mirroring from native session files.

ClaudeSessionReader gains `read_turns()` → list[ConversationTurn].
A new `mirror_transcript()` function writes turns into events.jsonl
as agent.turn.{user,assistant} events, deduped by line offset.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


# -- ConversationTurn schema tests --

class TestConversationTurnSchema:
    def test_turn_has_role_and_content(self):
        from zf.runtime.backend_session_reader import ConversationTurn
        turn = ConversationTurn(role="assistant", content="hello", line_offset=0)
        assert turn.role == "assistant"
        assert turn.content == "hello"
        assert turn.line_offset == 0

    def test_turn_has_timestamp(self):
        from zf.runtime.backend_session_reader import ConversationTurn
        turn = ConversationTurn(
            role="user", content="hi", line_offset=5, timestamp="2026-04-16T00:00:00Z",
        )
        assert turn.timestamp == "2026-04-16T00:00:00Z"


# -- ClaudeSessionReader.read_turns tests --

@pytest.fixture
def claude_session_file(tmp_path: Path) -> Path:
    """Fake Claude Code session JSONL with two turns."""
    lines = [
        json.dumps({
            "type": "human",
            "message": {"role": "user", "content": "Implement feature X"},
            "timestamp": "2026-04-16T01:00:00Z",
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "I'll implement feature X now."}],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
            "timestamp": "2026-04-16T01:01:00Z",
        }),
        json.dumps({
            "type": "human",
            "message": {"role": "user", "content": "Now add tests"},
            "timestamp": "2026-04-16T01:02:00Z",
        }),
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Adding tests for feature X."}],
                "usage": {"input_tokens": 200, "output_tokens": 80},
            },
            "timestamp": "2026-04-16T01:03:00Z",
        }),
    ]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


class TestClaudeReadTurns:
    def test_reads_all_turns(self, claude_session_file):
        from zf.runtime.backend_session_reader import ClaudeSessionReader
        reader = ClaudeSessionReader(projects_root=claude_session_file.parent)
        turns = reader.read_turns(claude_session_file)
        assert len(turns) == 4

    def test_user_turns_have_correct_content(self, claude_session_file):
        from zf.runtime.backend_session_reader import ClaudeSessionReader
        reader = ClaudeSessionReader()
        turns = reader.read_turns(claude_session_file)
        user_turns = [t for t in turns if t.role == "user"]
        assert len(user_turns) == 2
        assert user_turns[0].content == "Implement feature X"

    def test_assistant_turns_have_correct_content(self, claude_session_file):
        from zf.runtime.backend_session_reader import ClaudeSessionReader
        reader = ClaudeSessionReader()
        turns = reader.read_turns(claude_session_file)
        asst_turns = [t for t in turns if t.role == "assistant"]
        assert len(asst_turns) == 2
        assert asst_turns[0].content == "I'll implement feature X now."

    def test_turns_have_line_offsets(self, claude_session_file):
        from zf.runtime.backend_session_reader import ClaudeSessionReader
        reader = ClaudeSessionReader()
        turns = reader.read_turns(claude_session_file)
        offsets = [t.line_offset for t in turns]
        assert offsets == [0, 1, 2, 3]

    def test_turns_have_timestamps(self, claude_session_file):
        from zf.runtime.backend_session_reader import ClaudeSessionReader
        reader = ClaudeSessionReader()
        turns = reader.read_turns(claude_session_file)
        assert turns[0].timestamp == "2026-04-16T01:00:00Z"

    def test_read_turns_since_offset(self, claude_session_file):
        from zf.runtime.backend_session_reader import ClaudeSessionReader
        reader = ClaudeSessionReader()
        turns = reader.read_turns(claude_session_file, since_offset=2)
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "Now add tests"

    def test_read_turns_missing_file(self, tmp_path):
        from zf.runtime.backend_session_reader import ClaudeSessionReader
        reader = ClaudeSessionReader()
        turns = reader.read_turns(tmp_path / "nonexistent.jsonl")
        assert turns == []


# -- mirror_transcript tests --

class TestMirrorTranscript:
    def test_mirrors_turns_into_events(self, tmp_path, claude_session_file):
        from zf.runtime.backend_session_reader import (
            ClaudeSessionReader,
            mirror_transcript,
        )
        event_log = EventLog(tmp_path / "events.jsonl")
        reader = ClaudeSessionReader()
        count = mirror_transcript(
            reader=reader,
            session_path=claude_session_file,
            event_log=event_log,
            role="dev",
        )
        assert count == 4
        events = event_log.read_all()
        turn_events = [e for e in events if e.type.startswith("agent.turn.")]
        assert len(turn_events) == 4
        assert turn_events[0].type == "agent.turn.user"
        assert turn_events[0].actor == "dev"
        assert turn_events[1].type == "agent.turn.assistant"

    def test_dedupes_by_offset(self, tmp_path, claude_session_file):
        from zf.runtime.backend_session_reader import (
            ClaudeSessionReader,
            mirror_transcript,
        )
        event_log = EventLog(tmp_path / "events.jsonl")
        reader = ClaudeSessionReader()
        count1 = mirror_transcript(
            reader=reader,
            session_path=claude_session_file,
            event_log=event_log,
            role="dev",
        )
        count2 = mirror_transcript(
            reader=reader,
            session_path=claude_session_file,
            event_log=event_log,
            role="dev",
        )
        assert count1 == 4
        assert count2 == 0  # all deduped
        events = event_log.read_all()
        turn_events = [e for e in events if e.type.startswith("agent.turn.")]
        assert len(turn_events) == 4  # no duplicates

    def test_incremental_mirror(self, tmp_path, claude_session_file):
        from zf.runtime.backend_session_reader import (
            ClaudeSessionReader,
            mirror_transcript,
        )
        event_log = EventLog(tmp_path / "events.jsonl")
        reader = ClaudeSessionReader()
        # Mirror first 2 turns (lines 0,1)
        count1 = mirror_transcript(
            reader=reader,
            session_path=claude_session_file,
            event_log=event_log,
            role="dev",
        )
        assert count1 == 4

        # Append more lines to session file
        with open(claude_session_file, "a") as f:
            f.write(json.dumps({
                "type": "human",
                "message": {"role": "user", "content": "One more thing"},
                "timestamp": "2026-04-16T01:04:00Z",
            }) + "\n")

        count2 = mirror_transcript(
            reader=reader,
            session_path=claude_session_file,
            event_log=event_log,
            role="dev",
        )
        assert count2 == 1  # only the new turn
