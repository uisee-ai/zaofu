"""Tests for EventLog.get_causation_chain + zf events trace CLI (G-EVT-4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


class TestGetCausationChain:
    def test_returns_empty_for_unknown_id(self, tmp_path: Path):
        log = EventLog(tmp_path / "events.jsonl")
        log.append(ZfEvent(type="t"))
        assert log.get_causation_chain("evt-does-not-exist") == []

    def test_single_event_no_causation(self, tmp_path: Path):
        log = EventLog(tmp_path / "events.jsonl")
        log.append(ZfEvent(type="root"))
        root = log.read_all()[0]
        chain = log.get_causation_chain(root.id)
        assert len(chain) == 1
        assert chain[0].id == root.id

    def test_three_level_chain(self, tmp_path: Path):
        log = EventLog(tmp_path / "events.jsonl")
        a = ZfEvent(type="a")
        log.append(a)
        b = ZfEvent(type="b", causation_id=a.id)
        log.append(b)
        c = ZfEvent(type="c", causation_id=b.id)
        log.append(c)
        chain = log.get_causation_chain(c.id)
        assert [e.type for e in chain] == ["a", "b", "c"]

    def test_returns_chronological_order(self, tmp_path: Path):
        log = EventLog(tmp_path / "events.jsonl")
        a = ZfEvent(type="first")
        log.append(a)
        b = ZfEvent(type="second", causation_id=a.id)
        log.append(b)
        chain = log.get_causation_chain(b.id)
        assert chain[0].type == "first"
        assert chain[1].type == "second"

    def test_cycle_guard(self, tmp_path: Path):
        """Pathological: a self-referencing causation_id. Must not loop."""
        log = EventLog(tmp_path / "events.jsonl")
        # Create an event that cites itself (shouldn't happen naturally
        # but log corruption could produce it)
        evil = ZfEvent(type="evil", id="evt-self", causation_id="evt-self")
        log.append(evil)
        chain = log.get_causation_chain("evt-self")
        assert len(chain) == 1  # just the event itself, no infinite loop


class TestZfEventsTrace:
    def test_trace_cli_outputs_chain(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".zf"
        state_dir.mkdir()
        log = EventLog(state_dir / "events.jsonl")
        a = ZfEvent(type="task.dispatched", task_id="T1")
        log.append(a)
        b = ZfEvent(type="dev.build.done", task_id="T1", causation_id=a.id)
        log.append(b)

        result = main(["events", "trace", b.id])
        assert result == 0
        out = capsys.readouterr().out
        assert "task.dispatched" in out
        assert "dev.build.done" in out
        # Chronological display: task.dispatched should appear before dev.build.done
        assert out.index("task.dispatched") < out.index("dev.build.done")

    def test_trace_missing_event(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".zf"
        state_dir.mkdir()
        EventLog(state_dir / "events.jsonl").append(ZfEvent(type="t"))
        result = main(["events", "trace", "evt-not-real"])
        assert result != 0
