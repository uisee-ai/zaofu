"""P0-A — `_line_count` streaming + (mtime,size) cache.

Same non-blank-line semantics as the old read_text().splitlines() version, but
without loading the whole 15MB+ events.jsonl into a string + list per call, and
cached so a single snapshot request (which counts the seq several times) does at
most one pass.
"""

from __future__ import annotations

from pathlib import Path

from zf.web.projections.common import _line_count


def test_line_count_missing_and_empty(tmp_path: Path) -> None:
    assert _line_count(tmp_path / "nope.jsonl") == 0
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert _line_count(empty) == 0


def test_line_count_with_and_without_trailing_newline(tmp_path: Path) -> None:
    with_nl = tmp_path / "a.jsonl"
    with_nl.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
    assert _line_count(with_nl) == 2
    # a final line without a trailing newline still counts (parity with old impl)
    no_nl = tmp_path / "b.jsonl"
    no_nl.write_text('{"a":1}\n{"b":2}', encoding="utf-8")
    assert _line_count(no_nl) == 2


def test_line_count_skips_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    p.write_text('{"a":1}\n\n   \n{"b":2}\n', encoding="utf-8")
    assert _line_count(p) == 2


def test_line_count_cache_invalidates_on_append(tmp_path: Path) -> None:
    p = tmp_path / "d.jsonl"
    p.write_text('{"a":1}\n', encoding="utf-8")
    assert _line_count(p) == 1
    # repeated call (same mtime+size) returns the same value
    assert _line_count(p) == 1
    # append grows the file -> size changes -> recount picks up the new line
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"b":2}\n')
    assert _line_count(p) == 2
