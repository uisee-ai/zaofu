"""ZF-PWF-CATCHUP-001 — backend_session_reader narrative-diff tests.

Verifies ClaudeSessionReader.scan_narrative_since extracts user
messages / assistant excerpts / tool uses / edits / errors from a
JSONL transcript while filtering by ``since_timestamp``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.runtime.backend_session_reader import (
    BackendSessionReader,
    ClaudeSessionReader,
    CodexSessionReader,
    TranscriptCatchup,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


def test_transcript_catchup_is_frozen() -> None:
    cu = TranscriptCatchup(instance_id="dev-1", since_timestamp="2026-05-18T00:00:00Z")
    with pytest.raises((AttributeError, TypeError)):
        cu.new_user_messages = ()  # type: ignore[misc]


def test_default_implementation_returns_none() -> None:
    """ABC default: backends without a transcript opt out by
    returning None."""

    class Stub(BackendSessionReader):
        def session_path(self, project_root, session_id, *, cached_path=None):
            return None

        def read_latest_usage(self, session_path, *, fallback_window=None):
            return None

    stub = Stub()
    assert stub.scan_narrative_since(
        Path("/nonexistent"),
        since_timestamp="2026-05-18T00:00:00Z",
    ) is None


# ---------------------------------------------------------------------------
# ClaudeSessionReader.scan_narrative_since
# ---------------------------------------------------------------------------


def _claude_reader() -> ClaudeSessionReader:
    return ClaudeSessionReader(projects_root=Path("/unused"))


def test_missing_file_returns_none(tmp_path: Path) -> None:
    reader = _claude_reader()
    assert reader.scan_narrative_since(
        tmp_path / "missing.jsonl",
        since_timestamp="2026-05-18T00:00:00Z",
    ) is None


def test_empty_file_returns_empty_catchup(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    p.write_text("", encoding="utf-8")
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="2026-05-18T00:00:00Z")
    assert cu is not None
    assert cu.new_user_messages == ()
    assert cu.new_assistant_excerpts == ()
    assert cu.new_tool_uses == ()
    assert cu.transcript_size_bytes == 0
    assert cu.backend == "claude-code"


def test_user_message_extracted(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {
            "type": "human",
            "timestamp": "2026-05-18T10:00:00Z",
            "message": {"content": "fix the bug"},
        },
    ])
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="2026-05-18T00:00:00Z")
    assert cu is not None
    assert cu.new_user_messages == ("fix the bug",)


def test_timestamp_filter_excludes_old(tmp_path: Path) -> None:
    """Records with timestamp ≤ since_timestamp must be skipped."""
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {
            "type": "human",
            "timestamp": "2026-05-18T08:00:00Z",
            "message": {"content": "old message"},
        },
        {
            "type": "human",
            "timestamp": "2026-05-18T12:00:00Z",
            "message": {"content": "new message"},
        },
    ])
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="2026-05-18T10:00:00Z")
    assert cu is not None
    assert cu.new_user_messages == ("new message",)


def test_assistant_text_extracted_with_excerpt(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    long_text = "x" * 500
    _write_jsonl(p, [
        {
            "type": "assistant",
            "timestamp": "2026-05-18T10:00:00Z",
            "message": {
                "content": [{"type": "text", "text": long_text}],
            },
        },
    ])
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="2026-05-18T00:00:00Z")
    assert cu is not None
    assert len(cu.new_assistant_excerpts) == 1
    # Excerpt truncated to 200 chars
    assert len(cu.new_assistant_excerpts[0]) == 200


def test_tool_use_extracted(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {
            "type": "assistant",
            "timestamp": "2026-05-18T10:00:00Z",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
                ],
            },
        },
    ])
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="2026-05-18T00:00:00Z")
    assert cu is not None
    assert "Bash(...)" in cu.new_tool_uses
    assert "Read(...)" in cu.new_tool_uses


def test_edit_tool_use_extracts_file_path(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {
            "type": "assistant",
            "timestamp": "2026-05-18T10:00:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/home/x.py"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": "/home/y.md"},
                    },
                ],
            },
        },
    ])
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="2026-05-18T00:00:00Z")
    assert cu is not None
    assert "/home/x.py" in cu.new_edits
    assert "/home/y.md" in cu.new_edits


def test_error_record_extracted(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {
            "type": "error",
            "timestamp": "2026-05-18T10:00:00Z",
            "message": {"content": "command failed"},
        },
    ])
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="2026-05-18T00:00:00Z")
    assert cu is not None
    assert any("command failed" in e for e in cu.new_errors)


def test_tool_result_is_error_extracted(tmp_path: Path) -> None:
    """user-typed tool_result with is_error=True should be captured."""
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {
            "type": "user",
            "timestamp": "2026-05-18T10:00:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "is_error": True,
                        "content": "exit 1: file not found",
                    },
                ],
            },
        },
    ])
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="2026-05-18T00:00:00Z")
    assert cu is not None
    assert any("exit 1" in e for e in cu.new_errors)


def test_malformed_json_lines_skipped(tmp_path: Path) -> None:
    """Bad lines must not crash the scan."""
    p = tmp_path / "session.jsonl"
    p.write_text(
        "{not valid json\n"
        + json.dumps({
            "type": "human",
            "timestamp": "2026-05-18T10:00:00Z",
            "message": {"content": "still here"},
        }) + "\n",
        encoding="utf-8",
    )
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="2026-05-18T00:00:00Z")
    assert cu is not None
    assert cu.new_user_messages == ("still here",)


def test_empty_since_timestamp_returns_everything(tmp_path: Path) -> None:
    """Calling with empty since_timestamp = "no filter" — useful for
    'give me the whole catchup' calls."""
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [
        {
            "type": "human",
            "timestamp": "2026-05-18T08:00:00Z",
            "message": {"content": "a"},
        },
        {
            "type": "human",
            "timestamp": "2026-05-18T12:00:00Z",
            "message": {"content": "b"},
        },
    ])
    reader = _claude_reader()
    cu = reader.scan_narrative_since(p, since_timestamp="")
    assert cu is not None
    assert cu.new_user_messages == ("a", "b")


def test_instance_id_propagated(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    _write_jsonl(p, [])
    reader = _claude_reader()
    cu = reader.scan_narrative_since(
        p,
        since_timestamp="2026-05-18T00:00:00Z",
        instance_id="dev-7",
    )
    assert cu is not None
    assert cu.instance_id == "dev-7"


def test_codex_reader_uses_default_none_impl(tmp_path: Path) -> None:
    """CodexSessionReader inherits ABC default — opts out of catchup
    (Codex rollout files have a different shape that needs its own
    parser; that's a separate sprint)."""
    reader = CodexSessionReader()
    # Even with a real path, default impl returns None
    p = tmp_path / "codex.jsonl"
    p.write_text("", encoding="utf-8")
    assert reader.scan_narrative_since(
        p,
        since_timestamp="2026-05-18T00:00:00Z",
    ) is None
