"""Parity and replay/IO regressions for the disposable metric read model."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.segments import iter_event_records
from zf.core.metrics.collector import MetricsCollector
from zf.core.task.schema import Task, TaskContract, TaskEvidence
from zf.core.task.store import TaskStore
from zf.web.projections import metric_facts as facts, metric_views as views, metric_costs as costs
from zf.web.projections.read_model import db_path
from zf.web.projections.sqlite_reader import connect_readonly

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


@pytest.fixture
def state(tmp_path):
    sd = tmp_path / "custom-state"
    sd.mkdir()
    store = TaskStore(sd / "kanban.json")
    store.add(Task(id="T1", title="Done", status="done", evidence=TaskEvidence(commit="a1"),
                   assigned_to="dev-1", contract=TaskContract(feature_id="F1", owner_role="dev")))
    store.add(Task(id="T2", title="Review", status="review", assigned_to="dev-2"))
    return sd


def append(sd, kind, *, offset=0, **kwargs):
    EventLog(sd / "events.jsonl").append(ZfEvent(type=kind, ts=(NOW + timedelta(seconds=offset)).isoformat(), **kwargs))


def oracle(sd):
    return MetricsCollector.compute(events=EventLog(sd / "events.jsonl"),
        tasks=TaskStore(sd / "kanban.json"), cost=CostTracker(sd / "cost.jsonl")).to_dict()


def test_core_all_fields_match_across_append_recovery_and_replay(state):
    kinds = ["task.created", "task.dispatched", "worker.stuck", "worker.progress",
             "worker.stuck", "worker.drift.detected", "scope.violation", "verify.failed",
             "task.rework.requested", "verify.passed", "task.status_changed", "worker.recycled",
             "cost.budget.exceeded", "discriminator.failed", "discriminator.passed", "pane.crash"]
    for i, kind in enumerate(kinds):
        append(state, kind, offset=i * 60, task_id="T1", payload={"signal": "thrashing"})
        # Recovery may straddle any projector batch boundary.
        assert views.core(state) == oracle(state)
        assert views.core(state) == oracle(state)
    TaskStore(state / "kanban.json").update("T2", status="done")
    assert views.core(state) == oracle(state)
    with (state / "events.jsonl").open("a") as handle:
        handle.write("not-json\n")
    append(state, "worker.stuck", offset=1900)
    assert views.core(state) == oracle(state)
    # Simulate restart from persisted SQL, with no source reads on a cache hit.
    assert facts.load_meta(state, "core")["count"] == len(kinds) + 1


def test_normal_append_reads_only_new_bytes_and_task_changes_do_not_replay_events(state, monkeypatch):
    append(state, "worker.stuck", task_id="T1")
    views.core(state)
    calls = []
    original = facts.iter_event_records_from_cursor
    def observed(*args, **kwargs):
        calls.append(kwargs)
        yield from original(*args, **kwargs)
    monkeypatch.setattr(facts, "iter_event_records_from_cursor", observed)
    TaskStore(state / "kanban.json").update("T2", status="in_progress")
    assert views.core(state) == oracle(state)
    assert calls == []
    previous_size = (state / "events.jsonl").stat().st_size
    append(state, "verify.passed", offset=2, task_id="T1")
    assert views.core(state) == oracle(state)
    assert len(calls) == 1 and calls[0]["byte_offset"] == previous_size


def test_rotation_replacement_and_incomplete_tail_rebuild_without_double_count(state):
    append(state, "task.created", task_id="T1")
    assert views.core(state) == oracle(state)
    (state / "events").mkdir()
    (state / "events.jsonl").rename(state / "events" / "2026-10-25.jsonl")
    append(state, "verify.passed", offset=60, task_id="T1")
    assert views.core(state) == oracle(state)
    replacement = state / "replacement"
    replacement.write_text(ZfEvent(type="worker.stuck", ts=NOW.isoformat()).to_json() + "\n")
    replacement.replace(state / "events.jsonl")
    assert views.core(state) == oracle(state)
    # The ledger itself admits only complete newline-terminated event records.
    event = ZfEvent(type="worker.stuck.recovered", ts=NOW.isoformat()).to_json()
    with (state / "events.jsonl").open("a") as handle: handle.write(event[:10])
    facts.refresh_events(state)
    with (state / "events.jsonl").open("a") as handle: handle.write(event[10:] + "\n")
    assert views.core(state) == oracle(state)


def test_cost_projection_matches_precision_duplicates_and_corrections(state):
    tracker = CostTracker(state / "cost.jsonl")
    for i, usd in enumerate([0.1, 0.2, 1e-10, 0.333333333333]):
        tracker.record_usage(role="dev" if i % 2 else "judge", input_tokens=10 + i,
            output_tokens=i, model="unknown-model", backend="codex", provider_cost_usd=usd,
            cache_creation_tokens=3, cache_creation_1h_tokens=2, cache_read_tokens=5,
            source_event_id=f"usage-{i}", usage_sample_id=f"sample-{i}")
        assert views.core(state) == oracle(state)
        projected = costs.ProjectedCostTracker(state / "cost.jsonl")
        assert projected.precision_summary() == tracker.precision_summary()
        assert projected.per_role_totals() == tracker.per_role_totals()
        assert projected.summary_by_backend() == tracker.summary_by_backend()
    # Canonical writer dedupes admission, SQL must preserve the admitted result.
    tracker.record_usage("dev", 999, 999, source_event_id="usage-1", usage_sample_id="sample-1")
    costs.refresh(state)
    assert projected.precision_summary() == tracker.precision_summary()
    rows = [json.loads(line) for line in (state / "cost.jsonl").read_text().splitlines()]
    rows[0]["cost_usd"] = 0.987654321
    replacement = state / "cost.corrected"
    replacement.write_text("".join(json.dumps(row) + "\n" for row in rows))
    replacement.replace(state / "cost.jsonl")
    costs.refresh(state)
    assert projected.per_role_totals() == tracker.per_role_totals()
    assert projected.precision_summary() == tracker.precision_summary()


def test_fleet_and_pulse_keep_clock_windows_and_current_state(state):
    from zf.runtime.fleet_efficiency import build_role_efficiency, build_task_flow_stats
    from zf.web.overview_pulse import build_overview_pulse
    for kind, offset, payload in [("task.dispatched", -500, {}), ("worker.state.changed", -480, {"to": "busy"}),
            ("task.done.accepted", -200, {}), ("task.status_changed", -190, {"status": "done"}),
            ("human.escalate", -100, {"tier": "high"}), ("task.rework.requested", -80, {"rework_kind": "repair"})]:
        append(state, kind, offset=offset, task_id="T1", actor="dev-1", payload=payload)
    current = {task.id: task for task in TaskStore(state / "kanban.json").list_all()}
    events = [(r.seq, r.event) for r in iter_event_records(state)]
    for now in [NOW, NOW + timedelta(days=8)]:
        assert views.fleet(state, now=now) == {
            "task_flow": build_task_flow_stats(events, current, now=now),
            "role_efficiency": build_role_efficiency(events, current, now=now)}
        assert views.pulse(state, now=now) == build_overview_pulse(state, now=now)


def test_monitoring_loop_and_trace_match_full_payload_oracle(state, monkeypatch):
    from zf.runtime.stage_loop_projection import build_loop_view
    from zf.web.projections.loop_view_source import read_stable_loop_view_source
    from zf.web.projections.trace_pages import _trace_summaries, trace_list_page
    for i, run in enumerate(["run-a", "run-b"]):
        append(state, "run.started", offset=i * 10, correlation_id=run, payload={"run_id": run})
        append(state, "run.goal.started", offset=i * 10 + 1, correlation_id=run)
        append(state, "task.dispatched", offset=i * 10 + 2, task_id="T1", actor="dev-1",
               payload={"run_id": run, "stage_id": "impl", "child_id": "T1", "role_instance": "dev-1",
                        "nested": {"trace_id": f"trace-{run}", "body": "x" * 10000}})
        append(state, "dev.build.done", offset=i * 10 + 3, task_id="T1", correlation_id=run,
               payload={"run_id": run, "stage_id": "impl", "status": "completed"})
    full = [(r.seq, r.event) for r in iter_event_records(state)]
    selected = read_stable_loop_view_source(state)
    assert build_loop_view(state_dir=state, events=selected.events, generated_at=NOW.isoformat()) == build_loop_view(state_dir=state, events=full, generated_at=NOW.isoformat())
    assert trace_list_page(state)["items"] == _trace_summaries(full, as_of_seq=full[-1][0])
    with connect_readonly(db_path(state)) as conn:
        stored = conn.execute("SELECT payload FROM metric_event").fetchall()
    assert all("x" * 100 not in row[0] for row in stored)
    # Both Monitoring list paths can reuse persisted facts without raw decoding.
    def no_replay(*args, **kwargs): raise AssertionError("unchanged source replayed")
    monkeypatch.setattr(facts, "iter_event_records_from_cursor", no_replay)
    assert trace_list_page(state)["items"]
    assert read_stable_loop_view_source(state).fingerprint


def test_usage_can_be_bound_after_receipt_admission_without_changing_total(state):
    tracker = CostTracker(state / "cost.jsonl")
    tracker.record_usage("dev", 10, 5, source_event_id="late-usage", provider_cost_usd=0.123456789)
    costs.refresh(state)
    append(state, "agent.usage", id="late-usage", payload={"run_id": "run-2", "attempt_id": "attempt-3", "operation_id": "op-4"})
    facts.refresh_events(state)
    costs.refresh(state)
    with connect_readonly(db_path(state)) as conn:
        receipt = conn.execute("SELECT run_id,attempt_id,operation_id FROM metric_usage").fetchone()
        hours = conn.execute("SELECT scope FROM metric_usage_rollup WHERE kind='hour'").fetchall()
    assert tuple(receipt) == ("run-2", "attempt-3", "op-4")
    assert len(hours) == 1 and json.loads(hours[0][0])[-3:] == list(receipt)
    assert costs.ProjectedCostTracker(state / "cost.jsonl").precision_summary() == tracker.precision_summary()


def test_failed_sql_commit_replays_source_once_and_concurrent_readers_agree(state, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    append(state, "worker.stuck", task_id="T1")
    original = facts.put_meta
    def failed(conn, key, value):
        if key == "core": raise RuntimeError("simulated interrupted generation")
        original(conn, key, value)
    monkeypatch.setattr(facts, "put_meta", failed)
    with pytest.raises(RuntimeError, match="interrupted"):
        facts.refresh_events(state)
    monkeypatch.setattr(facts, "put_meta", original)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: views.core(state), range(4)))
    assert all(result == oracle(state) for result in results)
    with connect_readonly(db_path(state)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM metric_event").fetchone()[0] == 1


def test_corrupt_disposable_cache_keeps_canonical_metrics_and_trace(state):
    from zf.web.projections.trace_pages import trace_list_page
    append(state, "task.created", task_id="T1")
    path = db_path(state)
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"corrupt-sqlite")
    assert views.core(state) == oracle(state)
    assert trace_list_page(state)["items"][0]["event_count"] == 1


def test_scoped_oa_inputs_keep_usage_stale_rework_and_clock_semantics(state):
    from zf.runtime.orchestrator_agent_metrics import build_orchestrator_agent_metrics
    rows = [
        ("workflow.operation.requested", {"operation_id": "op", "operation_type": "orchestrator_agent_semantic"}),
        ("orchestrator.semantic.checkpoint.requested", {"operation_id": "op", "checkpoint": "semantic_failure"}),
        ("agent.usage", {"operation_id": "op", "model": "unpriced-local", "usage": {"input_tokens": 11, "output_tokens": 7}}),
        ("worker.progress", {"body": "large unrelated provider body"}),
        ("workflow.operation.blocked", {"operation_id": "op", "reason": "needs input"}),
        ("workflow.call.result.admitted", {"operation_id": "op", "decision": "rework"}),
        ("dev.build.done", {}),
        ("orchestrator.semantic.rework.requested", {"operation_id": "op"}),
        ("task.rework.requested", {"operation_id": "op"}),
        ("orchestrator.semantic.rework.rejected", {"operation_id": "op", "reason": "stale target"}),
        ("owner.visible_message.requested", {"message_kind": "run_terminal_delivery"}),
        ("owner.delivery.narrative.degraded", {"operation_id": "op"}),
    ]
    for i, (kind, payload) in enumerate(rows):
        append(state, kind, offset=i, task_id="T1", payload=payload)
    clock = NOW + timedelta(hours=1)
    full = EventLog(state / "events.jsonl").read_all()
    assert build_orchestrator_agent_metrics(views.oa_events(state), observed_at=clock) == build_orchestrator_agent_metrics(full, observed_at=clock)


def test_oa_inputs_follow_append_rotation_and_replacement_without_losing_bodies(state):
    def check():
        expected = [e for e in EventLog(state / "events.jsonl").read_all()
                    if e.type in {"agent.usage", "task.rework.requested"}]
        assert views.oa_events(state) == expected
    append(state, "agent.usage", payload={"usage": {"input_tokens": 9007199254740993}, "body": "full"})
    append(state, "worker.progress", payload={"ignored": True})
    check()
    append(state, "task.rework.requested", offset=1, payload={"reason": "nested", "detail": {"values": [1, 2]}})
    check()
    # Atomic replacement invalidates the shared decoded generation.
    replacement = state / "replacement.jsonl"
    replacement.write_bytes((state / "events.jsonl").read_bytes().splitlines(keepends=True)[-1])
    replacement.replace(state / "events.jsonl")
    check()
    (state / "events").mkdir(exist_ok=True)
    (state / "events.jsonl").replace(state / "events" / "2026-10-26.jsonl")
    append(state, "agent.usage", offset=2, payload={"usage": {"input_tokens": 17}})
    check()
