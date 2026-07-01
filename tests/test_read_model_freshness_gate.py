"""P0-B — read_model freshness gate is layout-based, not active-tail sensitive.

Before: projection_status compared the full manifest digest (which folds in the
active segment's size+mtime), so every append to a live project flipped the
projection to "stale" and forced an in-request full re-decode. Now a plain
append keeps it "ready" (with tail_behind=True) and only a rotation (archive
layout change) marks it "stale" for a seq-stable reindex.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.web.projections import read_model


def _write_line(path: Path, event: ZfEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(event.to_json() + "\n")


def test_status_missing_without_db(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="a", id="evt-a"))
    assert read_model.projection_status(state_dir)["projection_state"] == "missing"


def test_append_keeps_ready_and_flags_tail_behind(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="a", id="evt-a"))
    read_model.rebuild(state_dir)
    fresh = read_model.projection_status(state_dir)
    assert fresh["projection_state"] == "ready"
    assert fresh["tail_behind"] is False

    # plain append to the active segment (no rotation) must NOT flip to stale
    _write_line(state_dir / "events.jsonl", ZfEvent(type="b", id="evt-b"))
    after = read_model.projection_status(state_dir)
    assert after["projection_state"] == "ready"
    assert after["tail_behind"] is True


def test_rotation_marks_stale(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="a", id="evt-a"))
    read_model.rebuild(state_dir)
    assert read_model.projection_status(state_dir)["projection_state"] == "ready"

    # a new archive segment changes the segment layout -> stale (needs reindex)
    _write_line(state_dir / "events" / "2026-06-27.jsonl", ZfEvent(type="arch", id="evt-arch"))
    assert read_model.projection_status(state_dir)["projection_state"] == "stale"


def test_ensure_requested_catches_up_tail_for_read_your_writes(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    _write_line(state_dir / "events.jsonl", ZfEvent(type="a", id="evt-a"))
    read_model.rebuild(state_dir)
    _write_line(state_dir / "events.jsonl", ZfEvent(type="b", id="evt-b"))

    # ensure_requested must converge the tail synchronously so a following read
    # sees the just-appended event (read-your-writes).
    status = read_model.ensure_requested(state_dir)
    assert status["projection_state"] == "ready"
    assert status["tail_behind"] is False
    assert int(status["projected_seq"]) == 2
