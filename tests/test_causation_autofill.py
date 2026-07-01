"""Tests for zf emit auto-filling causation_id (G-EVT-1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".zf").mkdir()
    return tmp_path


class TestCausationAutofill:
    def test_first_event_for_task_has_no_causation(self, project: Path):
        main(["emit", "dev.build.done", "--task", "T1", "--actor", "dev"])
        log = EventLog(project / ".zf" / "events.jsonl")
        events = log.read_all()
        assert len(events) == 1
        assert events[0].causation_id is None

    def test_second_event_for_task_chains_to_first(self, project: Path):
        main(["emit", "task.dispatched", "--task", "T1", "--actor", "orchestrator"])
        main(["emit", "dev.build.done", "--task", "T1", "--actor", "dev"])
        log = EventLog(project / ".zf" / "events.jsonl")
        events = log.read_all()
        assert len(events) == 2
        first, second = events
        assert first.causation_id is None
        assert second.causation_id == first.id

    def test_third_event_chains_to_second(self, project: Path):
        main(["emit", "task.dispatched", "--task", "T1", "--actor", "orchestrator"])
        main(["emit", "dev.build.done", "--task", "T1", "--actor", "dev"])
        main(["emit", "review.approved", "--task", "T1", "--actor", "review"])
        log = EventLog(project / ".zf" / "events.jsonl")
        events = log.read_all()
        assert len(events) == 3
        first, second, third = events
        assert first.causation_id is None
        assert second.causation_id == first.id
        assert third.causation_id == second.id

    def test_different_tasks_do_not_cross_chain(self, project: Path):
        main(["emit", "task.dispatched", "--task", "T1", "--actor", "orchestrator"])
        main(["emit", "task.dispatched", "--task", "T2", "--actor", "orchestrator"])
        main(["emit", "dev.build.done", "--task", "T1", "--actor", "dev"])
        log = EventLog(project / ".zf" / "events.jsonl")
        events = log.read_all()
        t1_events = [e for e in events if e.task_id == "T1"]
        t2_events = [e for e in events if e.task_id == "T2"]
        assert len(t1_events) == 2
        assert len(t2_events) == 1
        # T1's second event chains to T1's first, not T2's
        t1_first, t1_second = t1_events
        assert t1_second.causation_id == t1_first.id

    def test_event_without_task_id_has_no_causation(self, project: Path):
        """If the new event has no task_id, it doesn't get a causation_id."""
        main(["emit", "task.dispatched", "--task", "T1", "--actor", "orchestrator"])
        main(["emit", "loop.started", "--actor", "zf-cli"])  # no --task
        log = EventLog(project / ".zf" / "events.jsonl")
        events = log.read_all()
        loop_event = next(e for e in events if e.type == "loop.started")
        assert loop_event.causation_id is None
