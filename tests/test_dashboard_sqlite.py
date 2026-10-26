from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web import dashboard_projection as dashboard
from zf.web.projections import dashboard_store as store, read_model
from zf.web.projections.sqlite_reader import connect_readonly
from tests.dashboard_projection_helpers import ready_get


def seed(path):
    path.mkdir(exist_ok=True)
    (path / "kanban.json").write_text("[]")
    EventLog(path / "events.jsonl").append(ZfEvent(id="one", type="run.started"))


def test_byte_cursor_reads_only_append_and_recovers_replacement(tmp_path, monkeypatch):
    seed(tmp_path)
    archive = tmp_path / "events" / "2026-01-01.jsonl"
    archive.parent.mkdir(exist_ok=True)
    archive.write_text(ZfEvent(id="old", type="task.created").to_json() + "\n")
    read_model.rebuild(tmp_path)
    path = tmp_path / "events.jsonl"
    with path.open("a") as f:
        f.write(ZfEvent(id="two", type="task.done").to_json() + '\n{"type":"partial"')
    opened = []
    original = Path.open
    def watch(path, *args, **kwargs):
        opened.append(path)
        return original(path, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(Path, "open", watch)
        assert read_model.rebuild(tmp_path)["inserted"] == 1
    assert archive not in opened
    with path.open("a") as f: f.write('}\n')
    assert read_model.rebuild(tmp_path)["inserted"] == 1
    assert read_model.rebuild(tmp_path)["inserted"] == 0
    replacement = tmp_path / "replacement"
    replacement.write_text(ZfEvent(id="new", type="run.started").to_json() + "\n")
    replacement.replace(path)
    read_model.rebuild(tmp_path)
    with connect_readonly(read_model.db_path(tmp_path)) as conn:
        assert [r[0] for r in conn.execute("SELECT event_id FROM event_index ORDER BY seq")] == ["old", "new"]


def test_read_queries_never_execute_schema_or_checkpoint(tmp_path, monkeypatch):
    seed(tmp_path)
    read_model.rebuild(tmp_path)
    def no_writer(*args, **kwargs): raise AssertionError("read attempted a write connection/schema")
    monkeypatch.setattr(read_model, "_connect", no_writer)
    monkeypatch.setattr(read_model, "_ensure_schema", no_writer)
    assert read_model.projection_status(tmp_path)["projection_state"] == "ready"
    assert read_model.events_page(tmp_path, limit=10)["items"]
    assert read_model.hydrate_event_by_seq(tmp_path, 1).id == "one"
    assert read_model.hydrate_event_by_id(tmp_path, "one")[1].id == "one"
    with connect_readonly(read_model.db_path(tmp_path)) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError): conn.execute("DELETE FROM event_index")


def test_rows_survive_new_reader_and_failed_generation(tmp_path):
    seed(tmp_path)
    payload = {"tasks": [{"id": "T1", "status": "done"}, {"id": "T2", "status": "blocked"}],
               "metrics_snapshot": {"ratio": 1.234567891234, "tokens": 987654321, "cost": None}, "empty": []}
    dashboard.rebuild_group(tmp_path, tmp_path.parent, None, "board", lambda: payload)
    read, meta = store.read_group(tmp_path, "board")
    assert read == payload and json.loads(meta["cursor"])["source_seq"] == 1
    with connect_readonly(read_model.db_path(tmp_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM dashboard_resource WHERE resource='tasks'").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM event_index").fetchone()[0] == 1
    with pytest.raises(ValueError):
        store.write_group(tmp_path, "board", {"metrics": float("nan")}, fingerprint="bad", cursor={}, source_changed=False)
    assert store.read_group(tmp_path, "board")[0] == payload
    assert dashboard.read_dashboard(tmp_path, tmp_path.parent, None, "board", lambda: pytest.fail("warm read rebuilt"))["metrics_snapshot"] == payload["metrics_snapshot"]


def test_non_event_changes_and_cross_project_identity_invalidate(tmp_path):
    seed(tmp_path)
    baseline = store.source_fingerprint(tmp_path, tmp_path.parent, None)
    path = tmp_path / "fanouts" / "f" / "manifest.json"
    path.parent.mkdir(parents=True); path.write_text('{"status":"running"}')
    assert store.source_fingerprint(tmp_path, tmp_path.parent, None) != baseline
    current = store.source_fingerprint(tmp_path, tmp_path.parent, None)
    assert store.source_fingerprint(tmp_path, tmp_path.parent / "different", None) != current
    path.unlink()
    assert store.source_fingerprint(tmp_path, tmp_path.parent, None) == baseline


def test_request_checks_only_fast_sources_and_defers_sidecar_scan(tmp_path, monkeypatch):
    seed(tmp_path)
    payload = {"tasks": [{"id": "T", "status": "ready"}]}
    dashboard.rebuild_group(tmp_path, tmp_path.parent, None, "board", lambda: payload)
    initial = store.quick_fingerprint(tmp_path, tmp_path.parent, None)
    sidecar = tmp_path / "artifacts" / "large-report.json"
    sidecar.parent.mkdir(); sidecar.write_text('{"new":true}')
    assert store.quick_fingerprint(tmp_path, tmp_path.parent, None) == initial
    def no_recursive_scan(*args): pytest.fail("request recursively scanned state")
    monkeypatch.setattr(store, "source_fingerprint", no_recursive_scan)
    assert dashboard.read_dashboard(tmp_path, tmp_path.parent, None, "board", lambda: payload)["read_projection"]["state"] == "ready"
    (tmp_path / "kanban.json").write_text('[{"id":"T","status":"done"}]')
    assert store.quick_fingerprint(tmp_path, tmp_path.parent, None) != initial
    fanout = tmp_path / "fanouts" / "f" / "manifest.json"
    fanout.parent.mkdir(parents=True)
    initial = store.quick_fingerprint(tmp_path, tmp_path.parent, None)
    fanout.write_text('{"status":"running"}')
    assert store.quick_fingerprint(tmp_path, tmp_path.parent, None) != initial


def test_pending_is_not_empty_and_concurrent_reads_share_worker(tmp_path, monkeypatch):
    from zf.web import server
    seed(tmp_path)
    started, release = threading.Event(), threading.Event()
    calls = []
    original = server._snapshot_slice
    def slow(*args, **kwargs):
        calls.append(True); started.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(server, "_snapshot_slice", slow)
    client = TestClient(server.create_app(tmp_path))
    path = "/api/projects/default/snapshot/board"
    try:
        response = client.get(path)
        assert response.status_code == 202 and "tasks" not in response.json()
        assert started.wait(5)
        assert client.get(path).status_code == 202
        assert calls == [True]
    finally:
        release.set()
    response = ready_get(client, path)
    assert response.status_code == 200
    assert response.json()["read_projection"]["state"] == "ready"
    # A fresh application must use SQLite, not the previous app's memory.
    monkeypatch.setattr(server, "_snapshot_slice", lambda *a, **kw: pytest.fail("restart read rebuilt"))
    again = TestClient(server.create_app(tmp_path)).get(path)
    assert again.status_code == 200
    assert again.json() == response.json()


def test_index_prepare_has_no_write_lock_and_failure_keeps_old_generation(tmp_path, monkeypatch):
    from zf.web.projections import event_index_rows
    seed(tmp_path)
    read_model.rebuild(tmp_path)
    with (tmp_path / "events.jsonl").open("a") as f:
        f.write(ZfEvent(id="second", type="task.created").to_json() + "\n")
    original = event_index_rows.prepare_rows
    def prepare(records):
        # Another projection can write during CPU-heavy decode/redaction.
        with sqlite3.connect(read_model.db_path(tmp_path), timeout=0.1) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS independent_writer(value)")
            conn.execute("INSERT INTO independent_writer VALUES (1)")
        return original(records)
    monkeypatch.setattr(event_index_rows, "prepare_rows", prepare)
    assert read_model.rebuild(tmp_path)["inserted"] == 1
    # Force a rotation rebuild to fail after deletion has started.
    archive = tmp_path / "events" / "2026-01-01.jsonl"
    archive.parent.mkdir(exist_ok=True)
    (tmp_path / "events.jsonl").rename(archive)
    (tmp_path / "events.jsonl").write_text(ZfEvent(id="third", type="task.done").to_json() + "\n")
    def fail(*args): raise RuntimeError("simulated transaction failure")
    monkeypatch.setattr(event_index_rows, "insert_rows", fail)
    with pytest.raises(RuntimeError): read_model.rebuild(tmp_path)
    with connect_readonly(read_model.db_path(tmp_path)) as conn:
        assert [r[0] for r in conn.execute("SELECT event_id FROM event_index ORDER BY seq")] == ["one", "second"]
    monkeypatch.undo()
    assert read_model.rebuild(tmp_path)["source_seq"] == 3


def test_dashboard_refresh_preserves_old_generation_until_atomic_replacement(tmp_path):
    seed(tmp_path)
    old = {"tasks": [{"id": "T", "status": "ready"}], "metrics_snapshot": {"ratio": 1.123456789}}
    new = {"tasks": [{"id": "T", "status": "done"}], "metrics_snapshot": {"ratio": 2.987654321}}
    dashboard.rebuild_group(tmp_path, tmp_path.parent, None, "board", lambda: old)
    (tmp_path / "kanban.json").write_text('[{"id":"T","status":"done"}]')
    entered, release = threading.Event(), threading.Event()
    def build():
        entered.set(); assert release.wait(5); return new
    try:
        stale = dashboard.read_dashboard(tmp_path, tmp_path.parent, None, "board", build)
        assert stale["read_projection"]["state"] == "refreshing"
        assert stale["tasks"] == old["tasks"]
        assert entered.wait(2)
        assert store.read_group(tmp_path, "board")[0] == old
    finally:
        release.set()
        dashboard._QUEUE.join()
    assert store.read_group(tmp_path, "board")[0] == new


def test_resource_refresh_writes_only_changed_rows(tmp_path):
    seed(tmp_path)
    payload = {"tasks": [{"id": "one", "status": "ready"}, {"id": "two", "status": "done"}], "cost": {"tokens": 123456789}}
    dashboard.rebuild_group(tmp_path, tmp_path.parent, None, "board", lambda: payload)
    with sqlite3.connect(read_model.db_path(tmp_path)) as conn:
        conn.executescript("""CREATE TABLE observed_row_writes(resource TEXT);
            CREATE TRIGGER observe_dashboard_insert AFTER INSERT ON dashboard_resource
            BEGIN INSERT INTO observed_row_writes VALUES (NEW.resource); END;
            CREATE TRIGGER observe_dashboard_delete AFTER DELETE ON dashboard_resource
            BEGIN INSERT INTO observed_row_writes VALUES (OLD.resource); END;""")
    next_payload = {**payload, "tasks": [{"id": "one", "status": "in_progress"}, payload["tasks"][1]]}
    store.write_group(tmp_path, "board", next_payload, fingerprint="next", cursor={"source_seq": 2}, source_changed=False,
                      scope=store.scope_identity(tmp_path, tmp_path.parent, None))
    with connect_readonly(read_model.db_path(tmp_path)) as conn:
        changes = [row[0] for row in conn.execute("SELECT resource FROM observed_row_writes")]
    assert changes and all(row == "tasks" for row in changes)
    assert len(changes) <= 2  # SQLite REPLACE may report deletion as well as insertion.
    assert store.read_group(tmp_path, "board")[0] == next_payload


def test_fingerprint_ignores_own_telemetry_but_invalidates_on_utc_day(tmp_path, monkeypatch):
    seed(tmp_path)
    old = store.source_fingerprint(tmp_path, tmp_path.parent, None)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "web-api-timing.jsonl").write_text('{"duration":1}\n')
    assert store.source_fingerprint(tmp_path, tmp_path.parent, None) == old
    now = time.time()
    monkeypatch.setattr(store.time, "time", lambda: now + 86400)
    assert store.source_fingerprint(tmp_path, tmp_path.parent, None) != old


def test_failed_list_projection_cannot_publish_an_empty_board(tmp_path, monkeypatch):
    from zf.web import server
    seed(tmp_path)
    previous = {"tasks": [{"id": "T", "status": "ready"}]}
    dashboard.rebuild_group(tmp_path, tmp_path.parent, None, "board", lambda: previous)
    (tmp_path / "kanban.json").write_text('{"changed":true}')
    def failed_tasks(*args, **kwargs): raise OSError("fixture task source unavailable")
    monkeypatch.setattr(server, "_kanban", failed_tasks)
    with pytest.raises(ValueError, match="source projection failed"):
        dashboard.rebuild_group(tmp_path, tmp_path.parent, None, "board", lambda: server._snapshot_slice(tmp_path, slice_name="board", project_root=tmp_path.parent))
    assert store.read_group(tmp_path, "board")[0] == previous


@pytest.mark.parametrize("source", ["events", "tasks", "sessions", "notifications"])
def test_failed_pulse_source_retains_previous_metrics(tmp_path, monkeypatch, source):
    from zf.web import overview_pulse as pulse
    from zf.web.projections import events
    seed(tmp_path)
    previous = {"run_pulse": {"events_per_bucket": [1, 2, 3]}}
    dashboard.rebuild_group(tmp_path, tmp_path.parent, None, "pulse", lambda: previous)
    (tmp_path / "kanban.json").write_text('{"changed":true}')
    def unavailable(*args, **kwargs): raise OSError("fixture source unavailable")
    if source == "events": monkeypatch.setattr(events, "events_read_days", unavailable)
    elif source == "tasks": monkeypatch.setattr(pulse.TaskStore, "list_all", unavailable)
    elif source == "sessions": monkeypatch.setattr(pulse, "build_runtime_resource_projection", unavailable)
    else: monkeypatch.setattr(pulse, "build_dispatch_notifications", unavailable)
    with pytest.raises(ValueError, match="source projection failed"):
        dashboard.rebuild_group(tmp_path, tmp_path.parent, None, "pulse", lambda: pulse.build_overview_pulse(tmp_path))
    assert store.read_group(tmp_path, "pulse")[0] == previous
