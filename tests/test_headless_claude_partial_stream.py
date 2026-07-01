"""B-STREAM-01: Claude headless token-level streaming via --include-partial-messages.

Fixtures mirror real `claude -p --output-format stream-json --verbose
--include-partial-messages` output (captured against claude CLI 2.1.178): the
partial `stream_event` frames carry token-level `text_delta`s, and the full
`assistant` block STILL arrives afterwards — the accumulator must dedupe the
final block against what it already streamed.
"""

from __future__ import annotations

import logging

from zf.web.headless_agent import ClaudeHeadlessBackend, _ClaudeStreamAccumulator


def _make_accumulator():
    emitted: list = []
    acc = _ClaudeStreamAccumulator(
        on_session_id=lambda _sid: None,
        on_message=emitted.append,
    )
    return acc, emitted


def _texts(emitted) -> list[str]:
    return [m.content for m in emitted if m.type == "text"]


def _thinkings(emitted) -> list:
    return [m for m in emitted if m.type == "thinking"]


# Real envelope shapes (captured) -------------------------------------------------

def _stream_text_delta(idx: int, text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": idx,
            "delta": {"type": "text_delta", "text": text},
        },
        "session_id": "sess-1",
    }


def _stream_thinking_delta(idx: int, text: str) -> dict:
    return {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": idx,
            "delta": {"type": "thinking_delta", "thinking": text},
        },
        "session_id": "sess-1",
    }


def _assistant(content: list[dict]) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


def _result(text: str) -> dict:
    return {"type": "result", "subtype": "success", "is_error": False, "result": text}


# Tests ---------------------------------------------------------------------------

def test_partial_deltas_emit_token_level_and_dedup_final_block():
    acc, emitted = _make_accumulator()
    acc.observe_message(_stream_text_delta(0, "h"))
    acc.observe_message(_stream_text_delta(0, "ello world"))
    # full block arrives after the partials — must NOT be re-emitted whole
    acc.observe_message(_assistant([{"type": "text", "text": "hello world"}]))
    acc.observe_message(_result("hello world"))

    assert _texts(emitted) == ["h", "ello world"]  # token-level, no duplicate
    assert acc.to_result().reply == "hello world"


def test_flag_off_block_level_unchanged():
    # No stream_event frames (partials disabled / older CLI) → behaviour is the
    # pre-B-STREAM-01 block-level emit, one HeadlessMessage per text block.
    acc, emitted = _make_accumulator()
    acc.observe_message(_assistant([{"type": "text", "text": "hello world"}]))
    acc.observe_message(_result("hello world"))

    assert _texts(emitted) == ["hello world"]
    assert acc.to_result().reply == "hello world"


def test_thinking_delta_redacted_and_no_double_signal():
    acc, emitted = _make_accumulator()
    # thinking block at index 0, text block at index 1 (typical order)
    acc.observe_message(_stream_thinking_delta(0, "secret chain of thought"))
    acc.observe_message(_stream_thinking_delta(0, "more secret reasoning"))
    acc.observe_message(_stream_text_delta(1, "hi"))
    acc.observe_message(
        _assistant(
            [
                {"type": "thinking", "thinking": "secret chain of thought more..."},
                {"type": "text", "text": "hi"},
            ]
        )
    )
    acc.observe_message(_result("hi"))

    # exactly one redacted thinking signal (deltas dedup + final block skipped)
    thinking = _thinkings(emitted)
    assert len(thinking) == 1
    assert thinking[0].content == "thinking"
    # raw chain-of-thought never surfaced in any emitted message
    blob = " ".join(str(m.content) for m in emitted)
    assert "secret" not in blob
    assert _texts(emitted) == ["hi"]


def test_dedup_mismatch_emits_full_and_logs(caplog):
    acc, emitted = _make_accumulator()
    acc.observe_message(_stream_text_delta(0, "abc"))
    with caplog.at_level(logging.WARNING):
        acc.observe_message(_assistant([{"type": "text", "text": "xyz totally different"}]))

    # streamed "abc", final block doesn't start with it → emit full + warn
    assert "xyz totally different" in _texts(emitted)
    assert any("prefix mismatch" in r.message for r in caplog.records)


def test_build_args_includes_partial_flag_by_default(monkeypatch):
    monkeypatch.delenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PARTIAL_MESSAGES", raising=False)
    args = ClaudeHeadlessBackend(command="claude").build_args(thread_id="t1")
    assert "--include-partial-messages" in args
    # still requires the stream-json / --verbose pair
    assert "stream-json" in args and "--verbose" in args


def test_build_args_partial_flag_disabled_by_env(monkeypatch):
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PARTIAL_MESSAGES", "0")
    args = ClaudeHeadlessBackend(command="claude").build_args(thread_id="t1")
    assert "--include-partial-messages" not in args
