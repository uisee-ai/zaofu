"""The Board transports the same metrics without duplicating detail bodies."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web import server
from zf.web.projections.board_summary import board_snapshot_summary


def _snapshot() -> dict:
    task = {
        "id": "TASK-1", "status": "blocked", "kanban_column": "ready",
        "attention": {"required": False}, "priority": 2,
        "task_card": {"lifecycle": {"outcome": "cancelled"}},
        "latest_event": {"id": "e1", "ts": "2026-10-26T00:00:00Z", "type": "worker.reported",
                         "payload": {"body": "x" * 100_000}},
        "fanout": {"progress": {"done": 2, "total": 3}, "current_child": {
            "child_id": "c1", "report_ref": "artifacts/report.json", "status": "completed",
            "cost_usd": 0.00000123, "tokens": 123456789, "report": {"body": "y" * 100_000},
        }},
    }
    return {
        "snapshot_slice": "light", "seq": 15,
        "tasks": [task], "archive_tasks": [copy.deepcopy(task)],
        "cost": {"total_usd": 0.00000123, "tokens": 123456789},
        "metrics_snapshot": {"throughput_per_hour": 1.23456789},
        "fleet_stats": {"task_flow": {"done": 2}},
        "features": [{"id": "F1", "passes": 2}],
    }


def test_summary_preserves_metrics_states_refs_and_original_detail() -> None:
    source = _snapshot()
    before = copy.deepcopy(source)
    summary = board_snapshot_summary(source)
    assert source == before  # A token-agnostic shared cache must stay intact.
    assert len(json.dumps(summary)) < len(json.dumps(source)) / 100
    assert set(summary) == set(source)
    for key in ("seq", "cost", "metrics_snapshot", "fleet_stats", "features"):
        assert summary[key] == source[key]
    for key in ("tasks", "archive_tasks"):
        expected = copy.deepcopy(source[key])
        expected[0]["latest_event"].pop("payload")
        expected[0]["fanout"]["current_child"].pop("report")
        assert summary[key] == expected


def test_board_route_uses_cached_summary_and_preserves_light_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "kanban.json").write_text("[]", encoding="utf-8")
    EventLog(state / "events.jsonl").append(ZfEvent(type="loop.started", actor="test"))
    source = _snapshot()
    calls: list[str] = []

    def snapshot_slice(*args, slice_name, **kwargs):
        calls.append(slice_name)
        return source

    monkeypatch.setattr(server, "_snapshot_slice", snapshot_slice)
    client = TestClient(server.create_app(state))
    # Project-scoped default id is available without a real workspace registry.
    light = client.get("/api/projects/default/snapshot/light")
    board = client.get("/api/projects/default/snapshot/board")
    assert light.status_code == board.status_code == 200
    assert board.json() == board_snapshot_summary(light.json())
    assert sorted(calls) == ["board", "light"]
    repeated = client.get("/api/projects/default/snapshot/board", headers={"If-None-Match": board.headers["etag"]})
    assert repeated.status_code == 304
    assert sorted(calls) == ["board", "light"]
    assert client.get("/api/projects/default/snapshot/light").json() == light.json()


def test_real_board_projection_matches_detail_without_reading_report_body(tmp_path: Path) -> None:
    from zf.web.projections.events import _event_to_dict
    from zf.web.projections.fanouts import _fanout_child_projection, _fanout_progress
    from zf.web.projections.tasks import _kanban

    state = tmp_path / "state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T1", title="report", status="in_progress"))
    children = [
        {"child_id": "c1", "task_id": "T1", "status": "completed", "report_path": "report.json",
         "report": {"body": "detail" * 1000, "api_token": "fixture-private-value"}},
        *[{"child_id": f"c{i}", "status": status, "report": {"body": "detail"}}
          for i, status in enumerate(["failed", "blocked", "running", "cancelled", "timed_out", "suspended", None], 2)],
    ]
    manifest = state / "fanouts" / "fanout-1" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"children": children}), encoding="utf-8")
    event = ZfEvent(type="fanout.started", actor="test", task_id="T1",
                    payload={"fanout_id": "fanout-1", "body": "event" * 1000, "api_token": "fixture-private-value"})
    EventLog(state / "events.jsonl").append(event)
    full = _kanban(state)
    compact = _kanban(state, include_detail_bodies=False)
    assert board_snapshot_summary({"tasks": compact}) == board_snapshot_summary({"tasks": full})
    assert "report" not in compact[0]["fanout"]["current_child"]
    assert compact[0]["latest_event"]["payload"] == {}
    assert "fixture-private-value" not in json.dumps(full)
    assert full[0]["fanout"]["current_child"]["report"]["body"] == "detail" * 1000
    assert _fanout_progress(children) == _fanout_progress([_fanout_child_projection(c) for c in children])

    # Header/progress-only reads must not traverse an unused report at all.
    class UnreadBody(dict):
        def items(self):
            raise AssertionError("summary traversed a detail body")

    child = {**children[0], "report": UnreadBody({"body": "private"})}
    assert "report" not in _fanout_child_projection(child, include_report=False)
    event.payload = UnreadBody({"body": "private"})
    assert _event_to_dict(1, event, include_payload=False)["payload"] == {}
