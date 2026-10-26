"""Repeated-read reductions preserve task selection, history and safe parsing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.signing import EventSigner
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web.projections import events as ev
from zf.web.projections.agents import _role_sessions_data
from zf.web.projections.tasks import _kanban, _latest_task_fanout_runtime


@pytest.fixture(autouse=True)
def isolate_caches():
    ev._EVENTS_WITH_SEQ_CACHE.clear()
    ev._DERIVED_CACHE.clear()
    yield
    ev._EVENTS_WITH_SEQ_CACHE.clear()
    ev._DERIVED_CACHE.clear()


def test_day_windows_share_decode_and_keep_archive_filename_semantics(tmp_path, monkeypatch):
    state = tmp_path
    archive = state / "events"
    archive.mkdir()
    now = datetime.now(timezone.utc)
    for age in (0, 2, 8, 30):
        path = state / "events.jsonl" if age == 0 else archive / f"{(now - timedelta(days=age)):%Y-%m-%d}.jsonl"
        # Deliberately unrelated timestamps: read_days selects segment dates.
        path.write_text(ZfEvent(id=f"e{age}", type="sample", ts="2000-01-01T00:00:00Z").to_json() + "\nnot-json\n{\"type\": 3}\n")
    future = archive / "archive"
    future.mkdir()
    (future / "extra.jsonl").write_text(ZfEvent(id="extra", type="sample").to_json() + "\n")
    expected = {days: EventLog(state / "events.jsonl").read_days(days) for days in (1, 7, 14, 99)}
    shared = dict((e.id, e) for _, e in ev._events_with_seq(state) if e.type != "event.malformed")

    def no_decode(*args, **kwargs):
        raise AssertionError("time window re-decoded an already hydrated segment")

    monkeypatch.setattr(EventLog, "decode_line", no_decode)
    monkeypatch.setattr(EventLog, "_parse_file", no_decode)
    for days, baseline in expected.items():
        actual = ev.events_read_days(state, days)
        assert [e.id for e in actual if e.type != "event.malformed"] == [e.id for e in baseline if e.type != "event.malformed"]
        assert [e.type for e in actual] == [e.type for e in baseline]
        assert all(shared[e.id] is e for e in actual if e.type != "event.malformed")


def test_day_window_fold_and_rotation_match_canonical_reader(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="first"))
    for days in (1, 7, 14):
        ev.events_read_days(tmp_path, days)
    log.append(ZfEvent(type="second"))
    for days in (1, 7, 14):
        assert [e.id for e in ev.events_read_days(tmp_path, days)] == [e.id for e in log.read_days(days)]
    archive = tmp_path / "events"
    archive.mkdir()
    day = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    log.path.rename(archive / f"{day}.jsonl")
    log.append(ZfEvent(type="third"))
    for days in (1, 7, 14):
        assert [e.id for e in ev.events_read_days(tmp_path, days)] == [e.id for e in log.read_days(days)]


def test_day_windows_preserve_signature_rejection(tmp_path, monkeypatch):
    import zf.core.events.factory as factory

    log = EventLog(tmp_path / "events.jsonl", signer=EventSigner(b"fixture-secret"))
    log.append(ZfEvent(type="accepted"))
    tampered = json.loads(log.path.read_text())
    tampered["event"]["type"] = "tampered"
    with log.path.open("a") as f:
        f.write(json.dumps(tampered) + "\n")
        f.write(ZfEvent(type="unsigned").to_json() + "\n")
    monkeypatch.setattr(factory, "event_log_from_project", lambda *a, **kw: log)
    monkeypatch.setattr("zf.core.events.segments.event_log_from_project", lambda *a, **kw: log)
    expected = log.read_days(7)
    actual = ev.events_read_days(tmp_path, 7)
    assert [e.type for e in actual] == [e.type for e in expected] == ["accepted", "event.malformed"]
    assert actual[0].id == expected[0].id
    assert actual[1].payload == expected[1].payload


def test_board_reads_each_manifest_once_and_keeps_winner_detail(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "kanban.json")
    for task in ("T1", "T2", "T3"):
        store.add(Task(id=task, title=task, status="in_progress"))
    children = [
        {"task_id": "T1", "child_id": "first", "status": "failed"},
        {"task_id": "T1", "child_id": "last", "status": "running", "report": {"body": "complete detail", "api_token": "private-fixture"}},
        {"task_id": "T2", "child_id": "other", "status": "completed"},
    ]
    for name in ("a-new", "b-tie", "z-old"):
        path = tmp_path / "fanouts" / name / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"stage_id": "impl", "children": children}))
    # Sorting alone would choose z-old. Event order, then path/child tie order wins.
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(type="fanout.started", payload={"fanout_id": "z-old"}))
    log.append(ZfEvent(type="fanout.started", payload={"fanout_id": "a-new"}))
    import zf.web.projections.events
    monkeypatch.setattr(zf.web.projections.events, "fanout_seq_maps", lambda *a, **kw: ({}, {"z-old": 1, "a-new": 2, "b-tie": 2}))
    reads = []
    original = Path.read_text

    def measured(path, *args, **kwargs):
        if path.name == "manifest.json":
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", measured)
    cards = {row["id"]: row for row in _kanban(tmp_path)}
    assert len(reads) == len(set(reads)) == 3
    assert cards["T1"]["fanout"]["fanout_id"] == "b-tie"
    assert cards["T1"]["fanout"]["child_id"] == "last"
    assert cards["T1"]["fanout"]["current_child"]["report"]["body"] == "complete detail"
    assert "private-fixture" not in json.dumps(cards)
    assert cards["T2"]["fanout"]["child_id"] == "other"
    assert _latest_task_fanout_runtime(tmp_path, "T1", [])["child"] == cards["T1"]["fanout"]["current_child"]
    # No process-level manifest cache: replacements are visible on the next read.
    path = tmp_path / "fanouts" / "b-tie" / "manifest.json"
    path.write_text(json.dumps({"children": [{"task_id": "T1", "child_id": "changed", "status": "completed"}]}))
    assert _latest_task_fanout_runtime(tmp_path, "T1", [])["child_id"] == "changed"


@pytest.mark.parametrize("fallback", [False, True])
def test_role_session_yaml_stays_safe_and_preserves_values(tmp_path, monkeypatch, fallback):
    if fallback:
        monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    path = tmp_path / "role_sessions.yaml"
    path.write_text("roles: {dev: session-id}\ninstance_meta: {dev: {active: true, retries: 2}}\n")
    assert _role_sessions_data(tmp_path) == yaml.safe_load(path.read_text())
    path.write_text("!!python/object:builtins.object {}")
    assert _role_sessions_data(tmp_path) == {}


def test_fanout_list_progress_does_not_process_unused_child_reports(tmp_path, monkeypatch):
    from zf.web.projections import fanouts

    path = tmp_path / "fanouts" / "f" / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"fanout_id": "f", "children": [
        {"child_id": "c1", "status": "completed", "report": {"body": "large" * 1000}},
        {"child_id": "c2", "status": "running"},
    ]}))
    project_child = fanouts._fanout_child_projection
    with monkeypatch.context() as patch:
        patch.setattr(fanouts, "_fanout_child_projection", lambda child, **kw: project_child(child, **{**kw, "include_report": True}))
        baseline = fanouts._fanouts(tmp_path)

    class UnreadReport(dict):
        def items(self):
            raise AssertionError("fanout list processed an unused report")

    manifest = json.loads(path.read_text())
    manifest["children"][0]["report"] = UnreadReport({"body": "large"})
    read = fanouts._read_json_file
    monkeypatch.setattr(fanouts, "_read_json_file", lambda p: manifest if p == path else read(p))
    assert fanouts._fanouts(tmp_path) == baseline
    assert baseline[0]["progress"]["total"] == 2
