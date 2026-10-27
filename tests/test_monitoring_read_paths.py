"""Freshness/graph parity, persistent reads and invalidation at Monitoring callers."""
from datetime import datetime, timezone
import os

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.long_horizon import TaskEventIndex, _age_seconds, _event_task_match, project_state_freshness
from zf.web.projections import source_cache


def _freshness_oracle(events, task, actor, now):
    relevant = [e for e in events if (task and _event_task_match(e, task)) or (actor and e.actor == actor)]
    selected = {"event": relevant[-1] if relevant else None}
    kinds = {"progress": {"worker.progress", "phase.progressed"},
             "file_change": {"task.files_touched", "task.ref.updated", "dev.build.done"},
             "test": {"test.passed", "test.failed", "static_gate.passed", "static_gate.failed"},
             "evidence": {"task.done.evidence", "task.evidence_linked", "task.artifact_refs.updated"}}
    selected.update({k: next((e for e in reversed(relevant) if e.type in types), None) for k, types in kinds.items()})
    selected["heartbeat"] = next((e for e in reversed(events) if e.type == "worker.heartbeat" and (not actor or e.actor == actor)), None)
    out = {}
    for key, event in selected.items():
        out[f"last_{key}_at"] = event.ts if event else ""
        out[f"last_{key}_age_sec"] = _age_seconds(event.ts, now=now) if event else None
    out["context_usage_ratio"] = next((float(e.payload[k]) for e in reversed(events)
        if not actor or e.actor in {actor, None}
        for k in ("context_usage_ratio", "usage_ratio", "context_ratio", "ratio")
        if isinstance(e.payload.get(k), int | float)), None)
    out["idle_age_sec"] = out["last_event_age_sec"]
    return out


@pytest.mark.parametrize("task,actor", [("T1", "dev-1"), ("T1", ""), ("", "dev-1"), ("missing", "absent"), ("", "")])
def test_freshness_preserves_nested_keys_actor_or_task_and_ledger_order(tmp_path, task, actor):
    events = [ZfEvent(type=kind, actor=who, task_id=tid, ts=ts, payload=payload) for kind, who, tid, ts, payload in [
        ("task.files_touched", "other", None, "2026-10-26T10:00:00Z", {"nested": {"T1": True}}),
        ("task.done.evidence", "dev-1", "T2", "invalid", {}),
        ("test.failed", "other", None, "2026-10-26T11:00:00Z", {"child": ["T1"]}),
        ("worker.progress", "dev-1", "T2", "2026-10-26T10:00:00Z", {}),
        ("worker.heartbeat", "dev-1", "T2", "2026-10-26T09:00:00Z", {"ratio": 0.9}),
        ("worker.progress", "other", "T1", "2026-10-26T08:00:00Z", {}),
        ("worker.context.warning", None, None, "", {"usage_ratio": 0.3, "ratio": 0.8}),
    ]]
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    expected = _freshness_oracle(events, task, actor, now)
    assert project_state_freshness(tmp_path, task_id=task, actor=actor, now=now, events=events) == expected
    index = TaskEventIndex(events)
    assert project_state_freshness(tmp_path, task_id=task, actor=actor, now=now, index=index) == expected


def test_freshness_skips_unrelated_historical_bodies(tmp_path, monkeypatch):
    events = [ZfEvent(type="agent.output", payload={"large": ["unrelated"]}) for _ in range(500)]
    events.append(ZfEvent(type="worker.progress", task_id="T1", actor="dev-1"))
    index = TaskEventIndex(events)
    def forbidden(*args):
        raise AssertionError("irrelevant historical body matched")
    monkeypatch.setattr(TaskEventIndex, "event_matches_task", forbidden)
    assert project_state_freshness(tmp_path, task_id="T1", actor="dev-1", index=index)["last_event_at"] == events[-1].ts


def test_source_cache_invalidates_archives_even_with_preserved_size_and_mtime(tmp_path):
    from zf.web.projections.agents import _worker_signal_index
    (tmp_path / "events").mkdir()
    archive = tmp_path / "events" / "2026-10-25.jsonl"
    event = ZfEvent(type="worker.progress", actor="dev-1", payload={"summary": "before"})
    archive.write_text(event.to_json() + "\n")
    EventLog(tmp_path / "events.jsonl").append(ZfEvent(type="task.created", actor="other"))
    first = _worker_signal_index(tmp_path)
    assert _worker_signal_index(tmp_path) == first
    stamp = archive.stat()
    event.payload["summary"] = "after!"
    replacement = tmp_path / "replacement"
    replacement.write_text(event.to_json() + "\n")
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    replacement.replace(archive)
    assert _worker_signal_index(tmp_path)["dev-1"]["last_output_summary"] == "after!"


def test_source_cache_persists_and_retries_failures_and_concurrent_changes(tmp_path, monkeypatch):
    from zf.web.projections.projection_failures import collect_projection_failures, record_projection_failure
    from zf.web.projections.read_model import db_path
    source = tmp_path / "input"
    source.write_text("before")
    calls = []
    def build():
        calls.append(1)
        return {"value": source.read_text()}
    def read(builder=build):
        return source_cache.read_source_projection(tmp_path, "fixture", [source], builder)
    assert read() == read() == {"value": "before"}
    assert len(calls) == 1
    source.write_text("after")
    def failed():
        record_projection_failure("fixture")
        return {"value": "failed"}
    with collect_projection_failures() as failures:
        assert read(failed) == {"value": "failed"}
    assert failures == ["fixture"]
    assert read() == {"value": "after"}
    assert len(calls) == 2
    source.write_text("changing")
    def changing():
        result = build()
        source.write_text("final")
        return result
    assert read(changing) == {"value": "changing"}
    assert read() == {"value": "final"}
    # Corrupt disposable storage falls back to the authoritative builder.
    db_path(tmp_path).write_bytes(b"not a database")
    assert read() == {"value": "final"}


def test_source_cache_retries_failed_event_reader(tmp_path, monkeypatch):
    from zf.web.projections import events
    from zf.web.projections.agents import _worker_signal_index
    EventLog(tmp_path / "events.jsonl").append(ZfEvent(type="worker.progress", actor="dev-1", payload={"summary": "ready"}))
    original = events.iter_event_records
    def failed(*args, **kwargs):
        raise OSError("temporary read failure")
    monkeypatch.setattr(events, "iter_event_records", failed)
    assert _worker_signal_index(tmp_path) == {}
    monkeypatch.setattr(events, "iter_event_records", original)
    assert _worker_signal_index(tmp_path)["dev-1"]["last_output_summary"] == "ready"


def test_source_cache_rechecks_input_after_reading_cached_value(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.write_text("old")
    def read():
        return source_cache.read_source_projection(tmp_path, "race", [source], source.read_text)
    assert read() == "old"
    original = source_cache.read_model.get_cached_projection
    def raced(*args, **kwargs):
        result = original(*args, **kwargs)
        if source.read_text() == "old":
            source.write_text("new")
        return result
    monkeypatch.setattr(source_cache.read_model, "get_cached_projection", raced)
    assert read() == "new"


def test_source_cache_config_and_archive_inventory_invalidate(tmp_path):
    from dataclasses import dataclass
    @dataclass
    class Config:
        label: str
    config = Config("first")
    calls = []
    def read():
        return source_cache.read_source_projection(tmp_path, "config", source_cache.task_paths(tmp_path),
            lambda: calls.append(config.label) or config.label, config=config)
    assert read() == read() == "first"
    config.label = "second"
    assert read() == "second"
    (tmp_path / "kanban").mkdir()
    (tmp_path / "kanban" / "archived.json").write_text("[]")
    assert read() == "second"
    assert calls == ["first", "second", "second"]


def test_trace_current_page_uses_aggregates_but_snapshot_cursor_keeps_old_membership(tmp_path, monkeypatch):
    from zf.web.projections import metric_traces
    from zf.web.projections.trace_pages import trace_list_page
    log = EventLog(tmp_path / "events.jsonl")
    for trace in ("a", "b", "c"):
        log.append(ZfEvent(type="worker.progress", correlation_id=trace, task_id="T1"))
    first = trace_list_page(tmp_path, limit=1)
    original = metric_traces.list_events
    def no_scalar_scan(*args, **kwargs):
        raise AssertionError("current page regrouped historical scalar rows")
    monkeypatch.setattr(metric_traces, "list_events", no_scalar_scan)
    assert trace_list_page(tmp_path, limit=1) == first
    log.append(ZfEvent(type="task.done", correlation_id="a", task_id="T1"))
    assert trace_list_page(tmp_path, limit=1)["items"][0]["event_count"] == 2
    monkeypatch.setattr(metric_traces, "list_events", original)
    second = trace_list_page(tmp_path, limit=1, cursor=first["next_cursor"])
    third = trace_list_page(tmp_path, limit=1, cursor=second["next_cursor"])
    assert second["items"][0]["trace_id"] == "b"
    assert third["items"][0]["trace_id"] == "a"
    assert third["items"][0]["event_count"] == 1


def test_graph_health_keeps_last_occurrence_not_max_timestamp():
    from zf.core.task.schema import Task
    from zf.runtime.execution_graph import build_execution_graph, _health
    tasks = {"T1": Task(id="T1", title="test", status="in_progress")}
    events = [(1, ZfEvent(type="worker.heartbeat", task_id="T1", ts="2026-10-26T12:00:00Z")),
              (2, ZfEvent(type="worker.heartbeat", task_id="T1", ts="2026-10-26T10:00:00Z")),
              (3, ZfEvent(type="worker.progress", task_id="other", ts="2026-10-26T11:00:00Z")),
              (4, ZfEvent(type="worker.progress", ts=""))]
    graph = build_execution_graph(task_map={"tasks": [{"task_id": "T1"}]}, tasks=tasks, events=events)
    assert graph["nodes"][0]["actual"]["health"] == _health("in_progress", tasks["T1"], events)


def test_missing_trace_aggregate_falls_back_without_erasing_metrics(tmp_path):
    import sqlite3
    from zf.web.projections.read_model import db_path
    from zf.web.projections.trace_pages import trace_list_page
    EventLog(tmp_path / "events.jsonl").append(ZfEvent(type="task.done", correlation_id="trace"))
    expected = trace_list_page(tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as connection:
        connection.execute("DROP TABLE metric_trace")
    assert trace_list_page(tmp_path) == expected
