"""Snapshot single-hydration perf fix.

The full snapshot called _events_with_seq from ~20 projections, each re-decoding
the whole append-only log; and _refs_from_events re-walked every payload once per
ref key. These collapse the redundant work without changing results:
- _events_with_seq memoizes by (size, mtime_ns) fingerprint
- _payload_collect resolves many keys in one DFS, equivalent to per-key _payload_ref
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.web.projections import events as ev
from zf.web.projections.common import _payload_collect, _payload_ref


def _write(path: Path, event: ZfEvent) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(event.to_json() + "\n")


def test_events_with_seq_memoizes_until_log_changes(tmp_path: Path) -> None:
    ev._EVENTS_WITH_SEQ_CACHE.clear()
    state_dir = tmp_path / ".zf"
    log = state_dir / "events.jsonl"
    _write(log, ZfEvent(type="fanout.started", id="evt-1", actor="orch", task_id="T-1"))

    first = ev._events_with_seq(state_dir)
    second = ev._events_with_seq(state_dir)
    # cache hit returns the same object (no re-decode)
    assert second is first
    assert [e.id for _, e in first] == ["evt-1"]

    # appending changes the (size, mtime_ns) fingerprint -> cache miss -> re-read
    _write(log, ZfEvent(type="dev.build.done", id="evt-2", actor="dev-1", task_id="T-1"))
    third = ev._events_with_seq(state_dir)
    assert third is not first
    assert [e.id for _, e in third] == ["evt-1", "evt-2"]


def test_payload_collect_matches_payload_ref() -> None:
    keys = {"commit", "branch", "run_id", "fanout_id", "missing"}
    payloads = [
        {"commit": "abc", "branch": "main"},
        {"outer": {"run_id": "r1"}, "branch": ""},          # nested dict
        {"items": [{"fanout_id": "fo-1"}, {"commit": "c2"}]},  # nested list
        {"commit": "", "deep": {"commit": "nested-nonempty"}},  # direct-empty wins -> stays ""
        {"a": {"branch": ""}, "b": {"branch": "from-b"}},     # first non-empty among siblings
        {"nothing": 1},
    ]
    for payload in payloads:
        collected = _payload_collect(payload, keys)
        for key in keys:
            ref = _payload_ref(payload, key)
            # _payload_collect only carries keys it resolved (ref is not None)
            if ref is None:
                assert key not in collected, (payload, key)
            else:
                assert collected.get(key) == ref, (payload, key, ref, collected.get(key))
