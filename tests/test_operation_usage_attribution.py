"""Parent budgets use exact cost receipts, retaining unknown coverage."""
import pytest

from zf.core.cost.tracker import CostTracker
from zf.core.events import EventLog, EventWriter
from zf.runtime.housekeeping import apply_agent_usage_event
from zf.runtime.operation_usage_attribution import operation_usage_snapshot
from zf.runtime.workflow_operation import WorkflowOperationService


def _fixture(tmp_path, scope="inclusive"):
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    service = WorkflowOperationService(state_dir=tmp_path, event_log=log, event_writer=writer)
    for name, parent in [("root", ""), ("child", "root")]:
        service.ensure_operation(workflow_run_id="run", operation_id=name, parent_operation_id=parent,
                                 operation_type="agent", request={"prompt": name})
    tracker = CostTracker(tmp_path / "cost.jsonl")
    def emit(name, tokens, cost, sample, **extra):
        event = writer.emit("agent.usage", actor=name, payload={
            "operation_id": name, "usage_sample_id": sample, "model": "gpt-5.5", "backend": "codex",
            "usage": {"input_tokens": tokens, "output_tokens": 0}, "total_cost_usd": cost,
            "usage_scope": "exclusive", **extra,
        })
        apply_agent_usage_event(tracker, event)
        return event
    child = emit("child", 10, .1, "child-sample")
    root = emit("root", 30, .3, "root-sample", usage_scope=scope,
                coverage_complete=True, covered_usage_sample_ids=["child-sample"])
    return log, writer, tracker, child, root


@pytest.mark.parametrize("scope,tokens,cost", [("inclusive", 30, .3), ("exclusive", 40, .4)])
def test_replay_and_order_do_not_double_charge_child(tmp_path, scope, tokens, cost):
    log, writer, tracker, child, root = _fixture(tmp_path, scope)
    apply_agent_usage_event(tracker, root)
    apply_agent_usage_event(tracker, child)
    snapshot = operation_usage_snapshot(log.read_all(), list(reversed(tracker.usage_records())), "root")
    assert snapshot["issues"] == []
    assert snapshot["total_tokens"] == tokens
    assert snapshot["total_usd"] == pytest.approx(cost)
    assert snapshot["operations"]["child"]["reported_usd"] == .1


@pytest.mark.parametrize("fault", ["unknown_scope", "missing_child", "unpriced", "inconsistent_total"])
def test_incomplete_accounting_is_unknown_not_zero(tmp_path, fault):
    log, _, tracker, _, _ = _fixture(tmp_path)
    events = log.read_all()
    records = tracker.usage_records()
    if fault == "unknown_scope":
        events[-1].payload.pop("usage_scope")
    elif fault == "missing_child":
        records = records[1:]
    elif fault == "unpriced":
        records[0]["cost_status"] = "unpriced"
    else:
        records[-1]["cost_usd"] = .01
    snapshot = operation_usage_snapshot(events, records, "root")
    assert not snapshot["usage_known"]
    assert snapshot["total_usd"] is None


def test_out_of_order_cumulative_snapshot_is_not_counter_reset(tmp_path):
    tracker = CostTracker(tmp_path / "cost.jsonl")
    for sample, tokens, timestamp in [("new", 30, "2026-10-29T12:00:00Z"), ("old", 10, "2026-10-29T11:00:00Z")]:
        tracker.record_cumulative_usage(role="reader", instance_id="reader", input_tokens=tokens,
            output_tokens=0, usage_series_id="session", usage_sample_id=sample, occurred_at=timestamp)
    assert tracker.usage_totals()["total_tokens"] == 30


@pytest.mark.parametrize("scope,limit,reason", [("exclusive", .2, "budget_exhausted"), ("unknown", 1, "budget_usage_unknown")])
def test_actual_continuation_entry_enforces_parent_meter(tmp_path, scope, limit, reason):
    from tests.test_read_only_dynamic_continuation import _state, _config, _seed
    from zf.runtime.read_only_dynamic_continuation import execute_read_only_continuation
    state, log, writer = _state(tmp_path)
    config = _config()
    action = _seed(state, log, writer, config=config)
    action["budgets"]["max_cost_usd"] = limit
    event = writer.emit("agent.usage", actor="reader", payload={"operation_id": action["parent_operation_id"],
        "usage_scope": scope, "model": "gpt-5.5", "backend": "codex", "total_cost_usd": .3,
        "usage": {"input_tokens": 100, "output_tokens": 5}})
    apply_agent_usage_event(CostTracker(state / "cost.jsonl"), event)
    result = execute_read_only_continuation(state, config=config, event_log=log, writer=writer, action=action)
    assert result.reason == reason
    assert not any(e.type == "workflow.invoke.requested" for e in log.read_all())
