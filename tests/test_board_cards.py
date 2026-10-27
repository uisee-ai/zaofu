"""Board cards can settle independently without manufacturing full snapshots."""
import os
import time

from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.cost.tracker import CostTracker
from zf.core.state.locks import locked_path
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web.server import create_app
from tests.dashboard_projection_helpers import ready_get


def test_cards_settle_before_full_board_and_preserve_the_same_task_projection(tmp_path):
    state = tmp_path / "custom-state"
    state.mkdir()
    tasks = TaskStore(state / "kanban.json")
    tasks.add(Task(id="T1", title="Active TOKEN=private", status="in_progress"))
    tasks.add(Task(id="T2", title="Completed task", status="pending"))
    tasks.update("T2", status="done")
    EventLog(state / "events.jsonl").append(ZfEvent(type="worker.progress", task_id="T1", actor="worker"))
    original_events = (state / "events.jsonl").read_bytes()
    app = create_app(state, project_root=tmp_path)
    with TestClient(app) as client:
        with locked_path(state / "projections/dashboard-board"):
            assert client.get("/api/projects/default/snapshot/board").status_code == 202
            cards = ready_get(client, "/api/projects/default/board-cards")
            assert cards.status_code == 200, cards.text
            partial = cards.json()
            assert partial["project_id"] == "default" and partial["source"] == "projection"
            assert partial["read_projection"]["state"] == "ready"
            assert [task["id"] for task in partial["tasks"]] == ["T1"]
            assert "private" not in cards.text
            assert [task["id"] for task in partial["archive_tasks"]] == ["T2"]
            assert not {"runtime", "cost", "metrics_snapshot", "features"} & partial.keys()
            assert client.get("/api/projects/default/snapshot/board").status_code == 202
            for resource in ("cost", "delivery-features", "channels"):
                assert client.get(f"/api/projects/default/{resource}").status_code == 200
        complete = ready_get(client, "/api/projects/default/snapshot/board")
        assert complete.status_code == 200, complete.text
        assert partial["tasks"] == complete.json()["tasks"]
        assert partial["archive_tasks"] == complete.json()["archive_tasks"]
        tasks.update("T1", title="Changed")
        stale = client.get("/api/projects/default/board-cards").json()
        assert stale["tasks"][0]["title"] == "Active TOKEN=[REDACTED_SECRET]"
        assert stale["read_projection"]["state"] == "refreshing"
        current = ready_get(client, "/api/projects/default/board-cards").json()
        assert current["tasks"][0]["title"] == "Changed"
        assert current["read_projection"]["state"] == "ready"
        assert (state / "events.jsonl").read_bytes() == original_events


def test_independent_resources_fail_without_claiming_an_empty_result(tmp_path, monkeypatch):
    from zf.web import server
    from zf.web.projections.metric_costs import ProjectedCostTracker

    state = tmp_path / "custom-state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T1", title="task"))
    EventLog(state / "events.jsonl").append(ZfEvent(type="worker.progress", task_id="T1"))
    CostTracker(state / "cost.jsonl").record_usage("dev", 10, 2, provider_cost_usd=0.1)
    def unavailable(*args, **kwargs):
        raise OSError("source temporarily unavailable")
    monkeypatch.setattr(ProjectedCostTracker, "per_role_totals", unavailable)
    monkeypatch.setattr(server, "_delivery_features", unavailable)
    client = TestClient(create_app(state, project_root=tmp_path))
    for resource in ("cost", "delivery-features"):
        response = client.get(f"/api/projects/default/{resource}")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}


def test_full_board_reuses_only_the_same_valid_card_generation(tmp_path, monkeypatch):
    from zf.web import dashboard_projection, server
    from zf.web.projection_jobs import build_job
    from zf.web.projection_worker import ProjectionJob

    state = tmp_path / "custom-state"
    state.mkdir()
    tasks = TaskStore(state / "kanban.json")
    tasks.add(Task(id="T1", title="before"))
    EventLog(state / "events.jsonl").append(ZfEvent(type="worker.progress", task_id="T1"))
    card_job = ProjectionJob(state, tmp_path, "board-cards")
    cards = dashboard_projection.rebuild_group(state, tmp_path, None, card_job.group, lambda: build_job(card_job))
    original = server._kanban
    calls = []
    def observed(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(server, "_kanban", observed)
    full = build_job(ProjectionJob(state, tmp_path, "board"))
    assert full["tasks"] == cards["tasks"] and not calls
    tasks.update("T1", title="after")
    changed = build_job(ProjectionJob(state, tmp_path, "board"))
    assert changed["tasks"][0]["title"] == "after" and calls == [True]


def test_card_fingerprints_invalidate_inplace_edits_with_preserved_size_and_mtime(tmp_path):
    from zf.web.projections import dashboard_store

    state = tmp_path / "state"
    state.mkdir()
    path = state / "kanban.json"
    path.write_text('[{"id":"T1","title":"before"}]')
    before = path.stat()
    quick = dashboard_store.quick_fingerprint(state, tmp_path, None)
    complete = dashboard_store.source_fingerprint(state, tmp_path, None)
    # Some filesystems advance ctime on a millisecond clock.
    time.sleep(0.005)
    path.write_text('[{"id":"T1","title":"after!"}]')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size and path.stat().st_mtime_ns == before.st_mtime_ns
    assert dashboard_store.quick_fingerprint(state, tmp_path, None) != quick
    assert dashboard_store.source_fingerprint(state, tmp_path, None) != complete


def test_new_and_legacy_dashboard_generations_do_not_overwrite_each_other(tmp_path):
    import sqlite3
    from zf.web.projections import dashboard_store
    from zf.web.projections.read_model import db_path

    dashboard_store.write_group(tmp_path, "board", {"tasks": [{"title": "new"}]},
                                fingerprint="new", cursor={}, source_changed=False)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("INSERT INTO dashboard_generation SELECT 'board', 'dashboard-resources.v2', fingerprint, "
                     "built_at, shapes, cursor, source_changed, scope, trigger_fingerprint "
                     "FROM dashboard_generation WHERE group_name=?", (f"{dashboard_store.VERSION}:board",))
        conn.execute("INSERT INTO dashboard_resource SELECT 'board', resource, ordinal, ? "
                     "FROM dashboard_resource WHERE group_name=?", ('{"title":"legacy"}', f"{dashboard_store.VERSION}:board"))
    dashboard_store.write_group(tmp_path, "board", {"tasks": [{"title": "newer"}]},
                                fingerprint="newer", cursor={}, source_changed=False)
    assert dashboard_store.read_group(tmp_path, "board")[0]["tasks"][0]["title"] == "newer"
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT payload FROM dashboard_resource WHERE group_name='board'").fetchone()[0] == '{"title":"legacy"}'


def test_independent_cost_includes_archive_when_active_file_is_absent(tmp_path):
    state = tmp_path / "custom-state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T1", title="task"))
    EventLog(state / "events.jsonl").append(ZfEvent(type="worker.progress", task_id="T1"))
    active = state / "cost.jsonl"
    CostTracker(active).record_usage("dev", 17, 3, provider_cost_usd=0.123456789)
    (state / "cost").mkdir()
    active.replace(state / "cost" / "2026-10-26.jsonl")
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.get("/api/projects/default/cost")
    assert response.status_code == 200
    assert response.json()["precision"]["display_total_usd"] == 0.123456789
    assert response.json()["per_role"]["dev"]["input_tokens"] == 17


def test_delivery_does_not_publish_a_value_built_across_source_changes(tmp_path, monkeypatch):
    from zf.web.projections import summaries

    state = tmp_path / "custom-state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T1", title="task"))
    EventLog(state / "events.jsonl").append(ZfEvent(type="worker.progress", task_id="T1"))
    path = state / "refs" / "feature-index.json"
    path.parent.mkdir()
    path.write_text('{}')
    original = summaries._build_delivery_features
    def changing(current):
        value = original(current)
        path.write_text('{"F1": {}}')
        return value
    monkeypatch.setattr(summaries, "_build_delivery_features", changing)
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.get("/api/projects/default/delivery-features")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
