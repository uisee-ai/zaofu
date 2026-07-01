"""2026-06-10 review: long-horizon bounded growth.

_processed_event_ids / _promoted_causations and the event_index.json disk
union previously grew one entry per event forever.
"""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.dedupe import BoundedIdSet
from zf.core.events.index import EventIndex
from zf.core.events.model import ZfEvent


class TestBoundedIdSet:
    def test_membership_and_add(self):
        s = BoundedIdSet(max_size=10)
        s.add("a")
        assert "a" in s
        assert "b" not in s

    def test_evicts_oldest_beyond_max(self):
        s = BoundedIdSet(max_size=3)
        for item in ("a", "b", "c", "d"):
            s.add(item)
        assert len(s) == 3
        assert "a" not in s  # oldest evicted
        assert "d" in s and "c" in s and "b" in s

    def test_duplicate_add_does_not_grow_or_reorder(self):
        s = BoundedIdSet(max_size=2)
        s.add("a")
        s.add("a")
        s.add("b")
        assert len(s) == 2
        s.add("c")  # evicts "a" (oldest), not "b"
        assert "a" not in s
        assert "b" in s and "c" in s

    def test_recent_dedupe_still_correct_past_bound(self):
        s = BoundedIdSet(max_size=100)
        for i in range(500):
            s.add(f"ev-{i}")
        assert len(s) == 100
        # The most recent window still dedupes correctly
        for i in range(400, 500):
            assert f"ev-{i}" in s

    def test_orchestrator_uses_bounded_sets(self):
        import inspect
        from zf.runtime import orchestrator
        src = inspect.getsource(orchestrator)
        assert "BoundedIdSet" in src


class TestEventIndexDiskCap:
    def _event(self, i: int) -> ZfEvent:
        return ZfEvent(
            type="test.event",
            actor="zf-cli",
            payload={"i": i},
        )

    def test_flush_caps_disk_union(self, tmp_path: Path):
        path = tmp_path / "event_index.json"
        cap = 50
        # Two index instances flushing in turn — the disk union must stay
        # capped even though each in-memory map is under the cap.
        for round_no in range(4):
            idx = EventIndex(path=path)
            idx._max_entries = cap
            idx.load()
            for i in range(30):
                idx.observe(self._event(round_no * 30 + i))
            idx.flush()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["event_by_id"]) <= cap

    def test_flush_keeps_newest_entries(self, tmp_path: Path):
        path = tmp_path / "event_index.json"
        idx = EventIndex(path=path)
        idx._max_entries = 10
        events = [self._event(i) for i in range(25)]
        for event in events:
            idx.observe(event)
        idx.flush()
        data = json.loads(path.read_text(encoding="utf-8"))
        kept = set(data["event_by_id"])
        newest_ids = {e.id for e in events[-10:]}
        assert kept == newest_ids
