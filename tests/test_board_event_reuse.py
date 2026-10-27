"""Board's event reuse keeps ledger decoding and matching semantics intact."""
import json
import threading
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.segments import iter_event_records, iter_event_records_from_cursor
from zf.core.security.signing import EventSigner
from zf.web.projections import event_index_rows, events, read_model
from zf.web.projections.common import _payload_search_text
from zf.web.projections.tasks import _group_task_events


def event_semantics(rows):
    # Malformed placeholders get new synthetic ids/timestamps on every decode.
    return [(seq, event.type, event.task_id, event.payload,
             None if event.type == "event.malformed" else event.id) for seq, event in rows]


@pytest.fixture(autouse=True)
def isolate_event_caches():
    events._EVENTS_WITH_SEQ_CACHE.clear()
    events._DERIVED_CACHE.clear()
    yield
    events._EVENTS_WITH_SEQ_CACHE.clear()
    events._DERIVED_CACHE.clear()


def test_cold_index_decodes_once_preserves_signatures_offsets_append_and_rotation(tmp_path, monkeypatch):
    import zf.core.events.factory as factory
    import zf.core.events.segments as segments

    archive = tmp_path / "events"
    archive.mkdir()
    log = EventLog(tmp_path / "events.jsonl", signer=EventSigner(b"fixture-only-secret"))
    archived = EventLog(archive / "2026-10-26.jsonl", signer=log.signer)
    archived.append(ZfEvent(id="archive", type="task.created", task_id="T1"))
    log.append(ZfEvent(id="valid", type="worker.progress", task_id="T1"))
    tampered = json.loads(log.path.read_text())
    tampered["event"]["type"] = "tampered"
    with log.path.open("a") as f:
        f.write(json.dumps(tampered) + "\nnot-json\n\n{\"unfinished\":")
    monkeypatch.setattr(factory, "event_log_from_project", lambda *a, **kw: log)
    monkeypatch.setattr(segments, "event_log_from_project", lambda *a, **kw: log)
    expected = [(r.seq, r.event) for r in iter_event_records(tmp_path)]
    result = read_model.rebuild(tmp_path)
    assert result["source_seq"] == len(expected)
    def forbidden(*args, **kwargs):
        raise AssertionError("full index generation was decoded a second time")
    with monkeypatch.context() as m:
        m.setattr(events, "iter_event_records", forbidden)
        assert event_semantics(events._events_with_seq(tmp_path)) == event_semantics(expected)
    cache = events._EVENTS_WITH_SEQ_CACHE[str(tmp_path)]
    assert cache["segment_ranges"] == [("events/2026-10-26.jsonl", 0, 1), ("events.jsonl", 1, len(expected))]
    assert cache["consumed"] < log.path.stat().st_size
    with log.path.open("a") as f:
        f.write('true}\n')
    before_epoch = cache["epoch"]
    folded, epoch, _ = events._events_state(tmp_path)
    assert epoch == before_epoch and event_semantics(folded) == event_semantics([(r.seq, r.event) for r in iter_event_records(tmp_path)])
    log.path.replace(archive / "2026-10-27.jsonl")
    log.append(ZfEvent(id="after-rotation", type="task.done", task_id="T1"))
    rotated, epoch, _ = events._events_state(tmp_path)
    assert epoch > before_epoch
    assert event_semantics(rotated) == event_semantics([(r.seq, r.event) for r in iter_event_records(tmp_path)])


def test_source_change_during_index_never_admits_a_mixed_generation(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(id="before", type="task.created"))
    original = event_index_rows.prepare_rows
    def append_during_index(records):
        result = original(records)
        log.append(ZfEvent(id="after", type="task.done"))
        return result
    monkeypatch.setattr(event_index_rows, "prepare_rows", append_during_index)
    read_model.rebuild(tmp_path)
    assert str(tmp_path) not in events._EVENTS_WITH_SEQ_CACHE
    assert [e.id for _, e in events._events_with_seq(tmp_path)] == ["before", "after"]


def test_unreadable_event_segment_never_admits_partial_index_or_cache(tmp_path, monkeypatch):
    from zf.web.projections.projection_failures import collect_projection_failures

    archive = tmp_path / "events" / "2026-10-26.jsonl"
    archive.parent.mkdir()
    EventLog(archive).append(ZfEvent(id="archive", type="task.created"))
    active = tmp_path / "events.jsonl"
    EventLog(active).append(ZfEvent(id="active", type="task.created"))
    original = type(active).open
    def unreadable(path, *args, **kwargs):
        if path == archive and (args[0] if args else kwargs.get("mode", "r")) == "rb":
            raise PermissionError("archive unavailable during read")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(active), "open", unreadable)
    assert [r.event.id for r in iter_event_records(tmp_path)] == ["active"]  # legacy default
    with pytest.raises(PermissionError):
        list(iter_event_records(tmp_path, strict_io=True))
    with pytest.raises(PermissionError):
        read_model.rebuild(tmp_path)
    assert str(tmp_path) not in events._EVENTS_WITH_SEQ_CACHE
    with collect_projection_failures() as failures:
        assert events._events_with_seq(tmp_path) == []
    assert failures == ["events"]
    assert str(tmp_path) not in events._EVENTS_WITH_SEQ_CACHE


def test_unreadable_cursor_segment_preserves_prior_index(tmp_path, monkeypatch):
    from zf.web.projections.sqlite_reader import connect_readonly

    active = tmp_path / "events.jsonl"
    log = EventLog(active)
    log.append(ZfEvent(id="before", type="task.created"))
    read_model.rebuild(tmp_path)
    log.append(ZfEvent(id="after", type="task.created"))
    original = type(active).open
    def unreadable(path, *args, **kwargs):
        if path == active and (args[0] if args else kwargs.get("mode", "r")) == "rb":
            raise PermissionError("active tail unavailable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(active), "open", unreadable)
    with pytest.raises(PermissionError):
        list(iter_event_records_from_cursor(tmp_path, segment="events.jsonl", strict_io=True))
    with pytest.raises(PermissionError):
        read_model.rebuild(tmp_path)
    with connect_readonly(read_model.db_path(tmp_path)) as conn:
        assert [row[0] for row in conn.execute("SELECT event_id FROM event_index ORDER BY seq")] == ["before"]


def test_unreadable_archive_directory_fails_strict_inventory(tmp_path, monkeypatch):
    import zf.core.events.segments as segments

    archive = tmp_path / "events"
    archive.mkdir()
    EventLog(tmp_path / "events.jsonl").append(ZfEvent(id="active", type="task.created"))
    original = segments.os.scandir
    def unreadable(path):
        if path == archive:
            raise PermissionError("archive listing unavailable")
        return original(path)
    monkeypatch.setattr(segments.os, "scandir", unreadable)
    with pytest.raises(PermissionError):
        list(iter_event_records(tmp_path, strict_io=True))
    with pytest.raises(PermissionError):
        read_model.rebuild(tmp_path)
    assert str(tmp_path) not in events._EVENTS_WITH_SEQ_CACHE


@pytest.mark.parametrize("kind", ["archive", "active", "archive-directory"])
def test_dangling_event_segments_fail_strict_index_without_cache(tmp_path, kind):
    if kind == "archive":
        path = tmp_path / "events" / "2026-10-26.jsonl"
        path.parent.mkdir()
    elif kind == "active":
        path = tmp_path / "events.jsonl"
    else:
        path = tmp_path / "events"
    path.symlink_to(tmp_path / "missing-target")
    with pytest.raises(FileNotFoundError):
        list(iter_event_records(tmp_path, strict_io=True))
    with pytest.raises(FileNotFoundError):
        read_model.rebuild(tmp_path)
    assert str(tmp_path) not in events._EVENTS_WITH_SEQ_CACHE


def test_failed_index_and_budget_limit_never_admit_decoded_events(tmp_path, monkeypatch):
    EventLog(tmp_path / "events.jsonl").append(ZfEvent(id="one", type="task.created"))
    original = event_index_rows.insert_rows
    def fail(*args, **kwargs):
        raise RuntimeError("failed database transaction")
    monkeypatch.setattr(event_index_rows, "insert_rows", fail)
    with pytest.raises(RuntimeError):
        read_model.rebuild(tmp_path)
    assert str(tmp_path) not in events._EVENTS_WITH_SEQ_CACHE
    monkeypatch.setattr(event_index_rows, "insert_rows", original)
    monkeypatch.setattr(events, "_CACHE_BUDGET_BYTES", 1)
    read_model.rebuild(tmp_path)
    assert str(tmp_path) not in events._EVENTS_WITH_SEQ_CACHE
    assert [e.id for _, e in events._events_with_seq(tmp_path)] == ["one"]


def test_resumed_index_does_not_treat_tail_as_full_ledger(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(id="before", type="task.created"))
    read_model.rebuild(tmp_path)
    events._EVENTS_WITH_SEQ_CACHE.clear()
    log.append(ZfEvent(id="after", type="task.done"))
    assert read_model.rebuild(tmp_path)["inserted"] == 1
    assert str(tmp_path) not in events._EVENTS_WITH_SEQ_CACHE
    assert [e.id for _, e in events._events_with_seq(tmp_path)] == ["before", "after"]


def test_new_full_generation_discards_derived_views_left_after_event_eviction(tmp_path):
    path = tmp_path / "events.jsonl"
    EventLog(path).append(ZfEvent(id="old", type="task.created", payload={"value": "old"}))
    read_model.rebuild(tmp_path)
    assert "old" in events.payload_search_texts(tmp_path)[0]
    events._EVENTS_WITH_SEQ_CACHE.pop(str(tmp_path))
    replacement = tmp_path / "replacement.jsonl"
    EventLog(replacement).append(ZfEvent(id="new", type="task.created", payload={"value": "new"}))
    replacement.replace(path)
    read_model.rebuild(tmp_path)
    assert "new" in events.payload_search_texts(tmp_path)[0]
    assert "old" not in events.payload_search_texts(tmp_path)[0]


def test_inflight_derived_view_cannot_fold_an_evicted_generation(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    EventLog(path).append(ZfEvent(id="old", type="task.created", payload={"value": "old"}))
    read_model.rebuild(tmp_path)
    events.payload_search_texts(tmp_path)
    events._EVENTS_WITH_SEQ_CACHE.pop(str(tmp_path))
    replacement = tmp_path / "replacement.jsonl"
    EventLog(replacement).append(ZfEvent(id="new", type="task.created", payload={"value": "new"}))
    replacement.replace(path)
    entered, resume = threading.Event(), threading.Event()
    original = events._events_state
    def delayed(*args, **kwargs):
        entered.set()
        assert resume.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(events, "_events_state", delayed)
    result = []
    reader = threading.Thread(target=lambda: result.extend(events.payload_search_texts(tmp_path)))
    reader.start()
    try:
        assert entered.wait(5)
        read_model.rebuild(tmp_path)
    finally:
        resume.set()
        reader.join(5)
    assert not reader.is_alive() and len(result) == 1
    assert "new" in result[0] and "old" not in result[0]


def test_full_cache_keeps_other_project_generation_while_indexing(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    EventLog(first / "events.jsonl").append(ZfEvent(id="one", type="task.created"))
    EventLog(second / "events.jsonl").append(ZfEvent(id="two", type="task.created"))
    monkeypatch.setattr(events, "_EVENTS_WITH_SEQ_CACHE_MAX", 1)
    first_events = events._events_with_seq(first)
    read_model.rebuild(second)
    assert str(first) in events._EVENTS_WITH_SEQ_CACHE
    assert str(second) not in events._EVENTS_WITH_SEQ_CACHE
    assert events._events_with_seq(first) is first_events


def test_batched_task_substring_matches_existing_semantics_and_order():
    ids = ["T1", "t12", "T123", "工单-1", "ABC", "abc", "", "prefix"]
    tasks = [SimpleNamespace(id=value) for value in ids]
    raw = [
        ZfEvent(id="one", type="worker.progress", task_id="T1", payload={"nested": {"T123": "text"}}),
        ZfEvent(id="two", type="worker.progress", task_id="t1", payload={"summary": "before-工单-1-after"}),
        ZfEvent(id="three", type="test.failed", payload={"abc-suffix": ["free text T12", {"other": "prefix-more"}]}),
        ZfEvent(id="four", type="task.done", task_id="", payload={"other": 1}),
        ZfEvent(id="five", type="task.done", task_id=["T1"], payload={"note": "none"}),
    ]
    rows = list(enumerate(raw, 1))
    texts = [_payload_search_text(event.payload) for event in raw]
    actual = _group_task_events(tasks, rows, texts)
    for task in tasks:
        needle = task.id.lower()
        expected = [(seq, event) for (seq, event), text in zip(rows, texts)
                    if event.task_id == task.id or (needle and needle in text)]
        assert actual[task.id] == expected


def test_large_match_sets_fall_back_without_changing_board_tasks(tmp_path, monkeypatch):
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.web.projections import event_task_index
    from zf.web.projections.tasks import _kanban

    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="T1", title="one"))
    store.add(Task(id="T12", title="two"))
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(id="both", type="worker.progress", payload={"text": "T12"}))
    expected = _kanban(tmp_path)
    monkeypatch.setattr(event_task_index, "_MAX_GROUPED_EVENT_REFS", 1)
    assert _kanban(tmp_path) == expected
