"""Tests for EventLog append-only store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


def test_append_creates_file_if_missing(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    ev = ZfEvent(type="test.create")
    log.append(ev)
    assert log.path.exists()


def test_append_and_read_all(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="a"))
    log.append(ZfEvent(type="b"))
    log.append(ZfEvent(type="c"))
    events = log.read_all()
    assert len(events) == 3
    assert [e.type for e in events] == ["a", "b", "c"]


def test_read_all_empty_file(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    path.touch()
    log = EventLog(path)
    assert log.read_all() == []


def test_read_all_skips_invalid_lines(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    ev = ZfEvent(type="valid")
    path.write_text(ev.to_json() + "\n" + "NOT JSON\n" + ev.to_json() + "\n")
    log = EventLog(path)
    events = log.read_all()
    assert len(events) == 2


def test_query_by_type(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done"))
    log.append(ZfEvent(type="test.passed"))
    log.append(ZfEvent(type="dev.build.done"))
    results = log.query(type="dev.build.done")
    assert len(results) == 2
    assert all(e.type == "dev.build.done" for e in results)


def test_query_by_event_type_task_and_actor(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(
        type="review.approved",
        actor="review",
        task_id="TASK-1",
    ))
    log.append(ZfEvent(
        type="review.approved",
        actor="review",
        task_id="TASK-2",
    ))
    log.append(ZfEvent(
        type="test.passed",
        actor="test",
        task_id="TASK-1",
    ))

    results = log.query(
        event_type="review.approved",
        task_id="TASK-1",
        actor="review",
    )

    assert len(results) == 1
    assert results[0].type == "review.approved"
    assert results[0].task_id == "TASK-1"
    assert results[0].actor == "review"


def test_events_for_task_uses_persisted_task_index(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(ZfEvent(type="task.created", actor="orchestrator", task_id="TASK-1"))
    log.append(ZfEvent(type="task.created", actor="orchestrator", task_id="TASK-2"))
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="TASK-1"))
    log.append(ZfEvent(type="review.approved", actor="review", task_id="TASK-1"))
    log.close()

    reloaded = EventLog(path)

    assert [event.type for event in reloaded.events_for_task("TASK-1")] == [
        "task.created",
        "dev.build.done",
        "review.approved",
    ]
    assert [event.type for event in reloaded.events_for_task("TASK-1", limit=1)] == [
        "review.approved",
    ]
    assert [event.task_id for event in reloaded.query(task_id="TASK-1", last=2)] == [
        "TASK-1",
        "TASK-1",
    ]


def test_query_type_alias_must_match_event_type(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="review.approved", task_id="TASK-1"))

    assert log.query(type="review.approved", event_type="review.approved")
    with pytest.raises(ValueError, match="type and event_type"):
        log.query(type="review.approved", event_type="test.passed")


def test_query_last_n(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    for i in range(10):
        log.append(ZfEvent(type=f"event.{i}"))
    results = log.query(last=3)
    assert len(results) == 3
    assert results[0].type == "event.7"


def test_read_all_preserves_insertion_order(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    types = ["first", "second", "third"]
    for t in types:
        log.append(ZfEvent(type=t))
    events = log.read_all()
    assert [e.type for e in events] == types


def test_count(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    assert log.count() == 0
    log.append(ZfEvent(type="a"))
    log.append(ZfEvent(type="b"))
    assert log.count() == 2


# -- A8: signed event tests --

def test_signed_log_writes_envelope(tmp_path: Path):
    from zf.core.security.signing import EventSigner
    log = EventLog(tmp_path / "events.jsonl", signer=EventSigner(b"secret"))
    log.append(ZfEvent(type="dev.build.done", actor="dev"))
    line = (tmp_path / "events.jsonl").read_text().strip()
    import json as _json
    obj = _json.loads(line)
    assert "event" in obj
    assert "sig" in obj
    assert isinstance(obj["sig"], str)
    assert len(obj["sig"]) == 64


def test_signed_log_round_trip(tmp_path: Path):
    from zf.core.security.signing import EventSigner
    signer = EventSigner(b"secret")
    log = EventLog(tmp_path / "events.jsonl", signer=signer)
    log.append(ZfEvent(type="dev.build.done", actor="dev"))
    log.append(ZfEvent(type="review.approved", actor="review"))
    events = log.read_all()
    assert [e.type for e in events] == ["dev.build.done", "review.approved"]


def test_signed_log_rejects_tampered_payload(tmp_path: Path):
    from zf.core.security.signing import EventSigner
    log = EventLog(tmp_path / "events.jsonl", signer=EventSigner(b"secret"))
    log.append(ZfEvent(type="dev.build.done", actor="dev"))
    # Tamper: rewrite the file with a modified event but the original sig
    import json as _json
    raw = (tmp_path / "events.jsonl").read_text().strip()
    obj = _json.loads(raw)
    obj["event"]["type"] = "review.approved"
    (tmp_path / "events.jsonl").write_text(_json.dumps(obj) + "\n")
    # Read should silently drop the tampered event (no crash) — count goes to 0
    events = log.read_all()
    assert events == []


def test_unsigned_log_still_works_for_backwards_compat(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev"))
    events = log.read_all()
    assert len(events) == 1
    assert events[0].type == "dev.build.done"


def test_signed_log_rejects_unsigned_legacy_lines_by_default(tmp_path: Path):
    """A signed log rejects plain (unsigned) lines by default — otherwise one
    injected line bypasses signing entirely (2026-06-10 review P0-2).
    Migration of a pre-signing events.jsonl opts in via allow_unsigned."""
    path = tmp_path / "events.jsonl"
    legacy = ZfEvent(type="legacy", actor="x")
    path.write_text(legacy.to_json() + "\n")
    from zf.core.security.signing import EventSigner
    log = EventLog(path, signer=EventSigner(b"secret"))
    log.append(ZfEvent(type="signed", actor="y"))
    events = log.read_all()
    assert [e.type for e in events] == ["event.malformed", "signed"]


def test_signed_log_reads_unsigned_legacy_lines_when_opted_in(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    legacy = ZfEvent(type="legacy", actor="x")
    path.write_text(legacy.to_json() + "\n")
    from zf.core.security.signing import EventSigner
    log = EventLog(path, signer=EventSigner(b"secret"), allow_unsigned=True)
    log.append(ZfEvent(type="signed", actor="y"))
    events = log.read_all()
    assert [e.type for e in events] == ["legacy", "signed"]


def test_signed_log_read_cache_is_isolated_by_signing_key(tmp_path: Path):
    from zf.core.security.signing import EventSigner

    path = tmp_path / "events.jsonl"
    good = EventSigner(b"good")
    bad = EventSigner(b"bad")
    EventLog(path, signer=good).append(ZfEvent(type="judge.passed", actor="judge"))

    assert [event.type for event in EventLog(path, signer=good).read_all()] == [
        "judge.passed",
    ]
    assert EventLog(path, signer=bad).read_all() == []


def test_persisted_event_index_payload_is_not_truth(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    real = ZfEvent(type="task.created", actor="orchestrator", task_id="TASK-1")
    log.append(real)
    log.close()

    index_path = tmp_path / "event_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    forged = ZfEvent(type="judge.passed", actor="evil", task_id="TASK-1")
    index["event_by_id"] = {forged.id: json.loads(forged.to_json())}
    index["event_ids_by_task_id"] = {"TASK-1": [forged.id]}
    index["latest_event_by_task_id"] = {"TASK-1": forged.id}
    index_path.write_text(json.dumps(index), encoding="utf-8")

    reloaded = EventLog(path)

    assert [event.type for event in reloaded.events_for_task("TASK-1")] == [
        "task.created",
    ]
    assert reloaded.index is not None
    assert reloaded.index.latest_event_for_task("TASK-1") is None
    latest = EventWriter(reloaded)._latest_task_event("TASK-1")
    assert latest is not None
    assert latest.id == real.id
