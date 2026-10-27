"""Scoped reads retain complete metrics, old cursor membership and drift."""
import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.delivery_trace import _node_counts, _ship_readiness
from zf.runtime.drift_report import build_drift_report
from zf.runtime.execution_graph import build_execution_graph
from zf.web.projections import metric_facts, trace_pages, trace_spans


def test_trace_detail_hydrates_only_its_scope_but_counts_beyond_page(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "events.jsonl")
    for i in range(1005):
        log.append(ZfEvent(type="worker.progress", actor="dev-1", task_id="T1",
                          payload={"nested": {"trace_id": "chosen"}, "summary": str(i)}))
    log.append(ZfEvent(type="worker.progress", correlation_id="other", payload={"summary": "unrelated"}))
    expected = trace_pages.trace_detail_page(tmp_path, "chosen", limit=2)
    assert expected["event_count"] == 1005 and len(expected["timeline"]) == 2
    original = metric_facts.hydrate_rows
    counts = []
    def selected_only(state, rows, **kwargs):
        assert all(r["trace_key"] == "chosen" for r in rows)
        counts.append(len(rows))
        return original(state, rows, **kwargs)
    monkeypatch.setattr(metric_facts, "hydrate_rows", selected_only)
    def forbidden(*args, **kwargs):
        raise AssertionError("whole-project hydration")
    monkeypatch.setattr(trace_pages, "_events_with_seq", forbidden)
    monkeypatch.setattr(trace_spans, "_events_with_seq", forbidden)
    assert trace_pages.trace_detail_page(tmp_path, "chosen", limit=2) == expected
    trace_spans.trace_span_page(tmp_path, "chosen")
    assert counts == [1005, 1005]
    log.append(ZfEvent(type="task.done", correlation_id="chosen", task_id="T1"))
    older = trace_pages.trace_detail_page(tmp_path, "chosen", limit=2, cursor=expected["next_cursor"])
    assert older["event_count"] == 1005 and older["as_of_seq"] == expected["as_of_seq"]


@pytest.mark.parametrize("planned", [False, True])
def test_delivery_summary_preserves_counts_drift_ship_without_detail_work(planned, monkeypatch):
    from zf.runtime import execution_graph
    tasks = {"T1": Task(id="T1", title="one", status="done", assigned_to="dev-2"),
             "T2": Task(id="T2", title="two", status="in_progress", assigned_to="judge-1")}
    task_map = {"tasks": [{"task_id": "T1", "owner_role": "dev", "wave": 1},
                          {"task_id": "T2", "owner_role": "dev", "blocked_by": ["T1"], "wave": 2}]} if planned else None
    events = [(1, ZfEvent(type="scope.violation", task_id="T1")),
              (2, ZfEvent(type="ship.blocked", task_id="T2"))]
    full = build_execution_graph(tasks=tasks, task_map=task_map, events=events)
    def forbidden(*args, **kwargs):
        raise AssertionError("summary constructed detail geometry/health")
    for name in ("_edges", "_waves", "_enrich_actual"):
        monkeypatch.setattr(execution_graph, name, forbidden)
    summary = build_execution_graph(tasks=tasks, task_map=task_map, events=events, summary_only=True)
    def metrics(graph):
        drift = build_drift_report(graph=graph, events=events)
        counts = _node_counts(graph["nodes"])
        return counts, drift, _ship_readiness(graph["nodes"], counts, drift, events)
    assert metrics(summary) == metrics(full)
    assert summary["diagnostics"] == full["diagnostics"]
