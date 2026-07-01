"""Tests for G-XPORT-2: EventLog._decode validates payload + emits event.malformed.

Previously: malformed lines (missing type, wrong payload type, etc) were
silently swallowed by `except (TypeError, KeyError): return None` in
EventLog._decode. Now the log surfaces them as an `event.malformed`
event so humans (via Feishu) can see corruption.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def _append_raw(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


class TestValidEventPasses:
    def test_valid_event_decodes_and_reads(self, tmp_path: Path):
        log = EventLog(tmp_path / "events.jsonl")
        log.append(ZfEvent(type="task.done", actor="dev", task_id="T1"))
        events = log.read_all()
        types = [e.type for e in events]
        assert "task.done" in types
        assert "event.malformed" not in types


class TestMalformedJson:
    def test_malformed_json_line_does_not_return_ghost(self, tmp_path: Path):
        """A line that is not valid JSON at all should not become a ZfEvent."""
        path = tmp_path / "events.jsonl"
        _append_raw(path, "this is not json {{{")
        _append_raw(path, ZfEvent(type="ok").to_json())

        log = EventLog(path)
        events = log.read_all()
        # The ok event must still be there; the garbage line must not decode
        # as the ok event or corrupt its position.
        assert any(e.type == "ok" for e in events)


class TestSchemaValidation:
    def test_missing_type_field_emits_event_malformed(self, tmp_path: Path):
        """A JSON object without `type` field is structurally invalid."""
        path = tmp_path / "events.jsonl"
        # Valid JSON, but missing required `type`
        _append_raw(path, '{"actor":"dev","payload":{}}')

        log = EventLog(path)
        events = log.read_all()
        types = [e.type for e in events]
        assert "event.malformed" in types

    def test_wrong_payload_type_emits_event_malformed(self, tmp_path: Path):
        """payload must be a dict; a string payload is a schema violation."""
        path = tmp_path / "events.jsonl"
        _append_raw(path, '{"type":"x","payload":"not-a-dict"}')

        log = EventLog(path)
        events = log.read_all()
        assert any(e.type == "event.malformed" for e in events)

    def test_malformed_preserves_original_line_hint(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        _append_raw(path, '{"actor":"dev"}')  # missing type

        log = EventLog(path)
        events = log.read_all()
        mal = next((e for e in events if e.type == "event.malformed"), None)
        assert mal is not None
        line_hint = mal.payload.get("line", "")
        assert "actor" in line_hint  # some fragment of the original


class TestMalformedDoesNotRecurse:
    def test_malformed_emit_does_not_cascade_when_appending_fails(
        self, tmp_path: Path
    ):
        """If decoding produces a malformed event whose OWN append somehow
        triggers re-decoding, we must not infinite loop. Test: decode a bad
        line, then read_all again, and assert we don't explode or multiply
        malformed events unbounded."""
        path = tmp_path / "events.jsonl"
        _append_raw(path, '{"payload":{}}')  # missing type

        log = EventLog(path)
        first = log.read_all()
        second = log.read_all()
        # Both reads finish (no hang, no RecursionError).
        # Number of malformed events recorded is bounded (at most a handful).
        assert len([e for e in second if e.type == "event.malformed"]) < 20
