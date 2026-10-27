"""Independent Board resources reject damaged or moving canonical sources."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.web import dashboard_projection, server
from zf.web.projection_jobs import build_job
from zf.web.projection_worker import ProjectionJob
from zf.web.projections import dashboard_store, metric_costs
from zf.web.projections.metric_facts import load_meta, put_meta, writer


def state_for(tmp_path: Path) -> Path:
    state = tmp_path / "custom-state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T1", title="Task"))
    EventLog(state / "events.jsonl").append(ZfEvent(type="worker.progress", task_id="T1"))
    return state


@pytest.mark.parametrize("kind", ["feature-index", "feature-list", "active-tasks", "archive-tasks"])
@pytest.mark.parametrize("content", ["{broken", "{}"])
def test_delivery_rejects_corrupt_sources_instead_of_returning_empty(tmp_path, kind, content):
    state = state_for(tmp_path)
    (state / "refs").mkdir()
    (state / "refs" / "feature-index.json").write_text('{"F1":{"title":"feature"}}')
    paths = {"feature-index": state / "refs" / "feature-index.json",
             "feature-list": state / "feature_list.json", "active-tasks": state / "kanban.json",
             "archive-tasks": state / "kanban" / "2026-10-27.json"}
    path = paths[kind]
    path.parent.mkdir(exist_ok=True)
    path.write_text(content if kind != "feature-index" or content != "{}" else "[]")
    response = TestClient(server.create_app(state, project_root=tmp_path)).get("/api/projects/default/delivery-features")
    assert response.status_code == 503, response.text
    assert response.json() == {"status": "unavailable"}


def test_delivery_tolerates_absent_optional_sources_and_rejects_read_error(tmp_path, monkeypatch):
    state = state_for(tmp_path)
    app = server.create_app(state, project_root=tmp_path)
    client = TestClient(app)
    assert client.get("/api/projects/default/delivery-features").status_code == 200
    target = state / "feature_list.json"
    target.write_text("[]")
    original = Path.read_text
    def unreadable(path, *args, **kwargs):
        if path == target:
            raise PermissionError("fixture denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", unreadable)
    response = client.get("/api/projects/default/delivery-features")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}


def test_bad_archived_tasks_cannot_be_published_as_empty_cards(tmp_path):
    state = state_for(tmp_path)
    bad = state / "kanban" / "2026-10-27.json"
    bad.parent.mkdir()
    bad.write_text("{}")
    job = ProjectionJob(state, tmp_path, "board-cards")
    with pytest.raises(ValueError, match="projection failed"):
        dashboard_projection.rebuild_group(state, tmp_path, None, job.group, lambda: build_job(job))
    assert dashboard_store.read_group(state, job.group) is None


@pytest.mark.parametrize("source", ["ready", "recent_events"])
def test_failed_task_inputs_preserve_last_complete_cards(tmp_path, monkeypatch, source):
    from zf.web.projections import events

    state = state_for(tmp_path)
    job = ProjectionJob(state, tmp_path, "board-cards")
    previous = dashboard_projection.rebuild_group(state, tmp_path, None, job.group, lambda: build_job(job))
    TaskStore(state / "kanban.json").update("T1", title="changed")
    def unavailable(*args, **kwargs):
        raise OSError("fixture unavailable")
    if source == "ready":
        monkeypatch.setattr(TaskStore, "ready", unavailable)
    else:
        monkeypatch.setattr(events, "events_read_days", unavailable)
    with pytest.raises(ValueError, match="projection failed"):
        dashboard_projection.rebuild_group(state, tmp_path, None, job.group, lambda: build_job(job))
    assert dashboard_store.read_group(state, job.group)[0] == previous


def replace_same_identity(path: Path, old: str, new: str) -> None:
    before = path.stat()
    value = path.read_text()
    assert old in value and len(old) == len(new)
    time.sleep(0.005)
    path.write_text(value.replace(old, new))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_ino == before.st_ino and path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns and path.stat().st_ctime_ns != before.st_ctime_ns


@pytest.mark.parametrize("archive", [False, True])
def test_cost_correction_same_size_and_mtime_invalidates(tmp_path, archive):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    CostTracker(path).record_usage("dev", 10, 2, provider_cost_usd=0.1)
    target = path
    if archive:
        target = state / "cost" / "2026-10-26.jsonl"
        target.parent.mkdir()
        path.replace(target)
    client = TestClient(server.create_app(state, project_root=tmp_path))
    assert client.get("/api/projects/default/cost").json()["total_usd"] == 0.1
    replace_same_identity(target, "0.1", "0.2")
    response = client.get("/api/projects/default/cost")
    assert response.status_code == 200
    assert response.json()["total_usd"] == 0.2 == CostTracker(path).total_usd()


def test_rewritten_prefix_plus_append_rebuilds_without_double_count(tmp_path):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    tracker = CostTracker(path)
    tracker.record_usage("dev", 10, 2, provider_cost_usd=0.1, usage_sample_id="one")
    metric_costs.refresh(state)
    value = path.read_text().replace("0.1", "0.2")
    path.write_text(value)
    tracker.record_usage("dev", 20, 3, provider_cost_usd=0.3, usage_sample_id="two")
    metric_costs.refresh(state)
    assert metric_costs.ProjectedCostTracker(path).per_role_totals() == tracker.per_role_totals()
    assert metric_costs.ProjectedCostTracker(path).precision_summary() == tracker.precision_summary()
    assert len(load_meta(state, "costs", {})["source"]["cost.jsonl"]) == 4


def test_legacy_metadata_after_append_is_not_mistaken_for_verified_generation(tmp_path):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    tracker = CostTracker(path)
    tracker.record_usage("dev", 10, 2, provider_cost_usd=0.1, usage_sample_id="one")
    metric_costs.refresh(state)
    old_guard = load_meta(state, "costs", {})["source_guard"]
    tracker.record_usage("dev", 20, 3, provider_cost_usd=0.2, usage_sample_id="two")
    metric_costs.refresh(state)
    # An old process updates source/offsets on append but retains unknown keys.
    # Recreate that precise legacy metadata shape after a correct legacy fold.
    with writer(state) as conn, conn:
        metadata = load_meta(state, "costs", {})
        metadata["source_guard"] = old_guard
        put_meta(conn, "costs", metadata)
    assert load_meta(state, "costs", {})["source_guard"]["source"] != load_meta(state, "costs", {})["source"]
    metric_costs.refresh(state)
    metadata = load_meta(state, "costs", {})
    assert metadata["source_guard"]["source"] == metadata["source"]
    assert all(len(identity) == 4 for identity in metadata["source"].values())
    assert metric_costs.ProjectedCostTracker(path).per_role_totals() == tracker.per_role_totals()


def test_malformed_legacy_source_metadata_forces_rebuild(tmp_path):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    tracker = CostTracker(path)
    tracker.record_usage("dev", 10, 2, provider_cost_usd=0.1)
    metric_costs.refresh(state)
    metadata = load_meta(state, "costs", {})
    identity = metadata["source"]["cost.jsonl"]
    metadata["source"]["cost.jsonl"] = identity[:2]
    metadata["source_guard"]["source"]["cost.jsonl"] = identity[:2]
    with writer(state) as conn, conn:
        put_meta(conn, "costs", metadata)
    metric_costs.refresh(state)
    assert len(load_meta(state, "costs", {})["source"]["cost.jsonl"]) == 4
    assert metric_costs.ProjectedCostTracker(path).per_role_totals() == tracker.per_role_totals()


@pytest.mark.parametrize("bad_offset", ["not-an-int", -1, 999999, True])
def test_bad_legacy_offsets_force_full_rebuild(tmp_path, bad_offset):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    tracker = CostTracker(path)
    tracker.record_usage("dev", 10, 2, provider_cost_usd=0.1)
    metric_costs.refresh(state)
    metadata = load_meta(state, "costs", {})
    metadata["offsets"]["cost.jsonl"] = bad_offset
    metadata["source_guard"]["offsets"]["cost.jsonl"] = bad_offset
    with writer(state) as conn, conn:
        put_meta(conn, "costs", metadata)
    tracker.record_usage("dev", 20, 3, provider_cost_usd=0.2)
    metric_costs.refresh(state)
    assert metric_costs.ProjectedCostTracker(path).total_usd() == pytest.approx(0.3)
    assert load_meta(state, "costs", {})["offsets"]["cost.jsonl"] == path.stat().st_size


def test_complete_bad_cost_line_returns_unavailable_keeps_previous_generation(tmp_path):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    CostTracker(path).record_usage("dev", 10, 2, provider_cost_usd=0.1)
    client = TestClient(server.create_app(state, project_root=tmp_path))
    assert client.get("/api/projects/default/cost").json()["total_usd"] == 0.1
    original = load_meta(state, "costs", {})
    with path.open("a") as handle:
        handle.write("{broken}\n")
    response = client.get("/api/projects/default/cost")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}
    assert load_meta(state, "costs", {}) == original
    assert metric_costs.ProjectedCostTracker(path).total_usd() == 0.1


def test_partial_cost_tail_waits_and_finishes_on_append(tmp_path):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    CostTracker(path).record_usage("dev", 10, 2, provider_cost_usd=0.1)
    client = TestClient(server.create_app(state, project_root=tmp_path))
    assert client.get("/api/projects/default/cost").json()["total_usd"] == 0.1
    with path.open("a") as handle:
        handle.write('{"role":"dev","cost_usd":')
    assert client.get("/api/projects/default/cost").json()["total_usd"] == 0.1
    with path.open("a") as handle:
        handle.write("0.2,\"input_tokens\":0,\"output_tokens\":0}\n")
    result = client.get("/api/projects/default/cost")
    assert result.status_code == 200 and result.json()["total_usd"] == 0.3


def test_complete_unterminated_cost_tail_rebuilds_if_append_has_no_separator(tmp_path):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    tracker = CostTracker(path)
    tracker.record_usage("dev", 10, 2, provider_cost_usd=0.1)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    client = TestClient(server.create_app(state, project_root=tmp_path))
    assert client.get("/api/projects/default/cost").json()["total_usd"] == 0.1
    with path.open("ab") as handle:
        handle.write(b"x")  # The previously complete JSON is now one partial record.
    result = client.get("/api/projects/default/cost")
    assert result.status_code == 200
    assert result.json()["total_usd"] == tracker.total_usd() == 0.0


def test_complete_unterminated_cost_tail_accepts_newline_then_new_record(tmp_path, monkeypatch):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    tracker = CostTracker(path)
    tracker.record_usage("dev", 10, 2, provider_cost_usd=0.1)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    metric_costs.refresh(state)
    extra = tmp_path / "extra.jsonl"
    CostTracker(extra).record_usage("dev", 20, 3, provider_cost_usd=0.2)
    with path.open("ab") as handle:
        handle.write(b"\n" + extra.read_bytes())
    seen = []
    original = CostTracker._accumulate_summary
    def observed(summary, entry):
        seen.append(entry.get("input_tokens"))
        return original(summary, entry)
    monkeypatch.setattr(CostTracker, "_accumulate_summary", staticmethod(observed))
    metric_costs.refresh(state)
    assert seen and set(seen) == {20}, "verified append must process only the new receipt"
    assert metric_costs.ProjectedCostTracker(path).total_usd() == pytest.approx(0.3)


def test_incomplete_archived_cost_record_fails_closed_but_valid_record_needs_no_newline(tmp_path):
    state = state_for(tmp_path)
    path = state / "cost.jsonl"
    CostTracker(path).record_usage("dev", 10, 2, provider_cost_usd=0.1)
    archive = state / "cost" / "2026-10-26.jsonl"
    archive.parent.mkdir()
    path.replace(archive)
    archive.write_bytes(archive.read_bytes().rstrip(b"\n"))
    client = TestClient(server.create_app(state, project_root=tmp_path))
    assert client.get("/api/projects/default/cost").json()["total_usd"] == 0.1
    archive.write_bytes(archive.read_bytes()[:-5])
    response = client.get("/api/projects/default/cost")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}


def test_unreadable_cost_archive_directory_is_unavailable(tmp_path, monkeypatch):
    from zf.web.projections import metric_costs as costs
    state = state_for(tmp_path)
    (state / "cost").mkdir()
    client = TestClient(server.create_app(state, project_root=tmp_path))
    original = costs.os.scandir
    def unavailable(path):
        if path == state / "cost":
            raise PermissionError("fixture denied")
        return original(path)
    monkeypatch.setattr(costs.os, "scandir", unavailable)
    response = client.get("/api/projects/default/cost")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}


@pytest.mark.parametrize("kind", ["archive", "active", "archive-directory"])
def test_dangling_cost_source_is_unavailable(tmp_path, kind):
    state = state_for(tmp_path)
    if kind == "archive":
        path = state / "cost" / "2026-10-26.jsonl"
        path.parent.mkdir()
    elif kind == "active":
        path = state / "cost.jsonl"
    else:
        path = state / "cost"
    path.symlink_to(state / "missing-target")
    response = TestClient(server.create_app(state, project_root=tmp_path)).get("/api/projects/default/cost")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}


def test_dangling_feature_index_is_unavailable(tmp_path):
    state = state_for(tmp_path)
    (state / "refs").mkdir()
    (state / "refs" / "feature-index.json").symlink_to(state / "missing-target")
    response = TestClient(server.create_app(state, project_root=tmp_path)).get("/api/projects/default/delivery-features")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}
