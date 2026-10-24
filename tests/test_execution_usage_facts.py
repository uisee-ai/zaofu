from pathlib import Path

import pytest

from zf.core.cost.tracker import CostTracker
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.execution_usage_facts import execution_usage_facts
from zf.runtime.housekeeping import apply_agent_usage_event


def test_cumulative_receipts_and_retry_are_counted_once_without_inventing_cost(tmp_path: Path):
    writer = EventWriter(event_log_from_project(tmp_path))
    tracker = CostTracker(tmp_path / "cost.jsonl")
    for index, (input_tokens, cached, output) in enumerate([(100, 60, 10), (150, 80, 20)]):
        event = writer.append(ZfEvent(type="agent.usage", actor="planner", payload={
            "backend": "codex", "model": "unknown-model", "workflow_run_id": "run-1",
            "stage_id": "prd-plan", "operation_id": "op-1", "active_attempt_id": f"try-{index}",
            "usage_semantics": "cumulative", "usage_series_id": "session-1",
            "usage_sample_id": f"sample-{index}",
            "usage": {"input_tokens": input_tokens, "cached_input_tokens": cached,
                      "output_tokens": output},
        }))
        apply_agent_usage_event(tracker, event)
        apply_agent_usage_event(tracker, event)  # replay must not inflate planning facts
    facts = execution_usage_facts(tmp_path, workflow_run_id="run-1")
    assert facts["available"] is True
    assert len(facts["groups"]) == 2
    assert sum(g["total_tokens"] for g in facts["groups"]) == 170
    assert sum(g["input_tokens"] for g in facts["groups"]) == 70
    assert sum(g["cache_read_tokens"] for g in facts["groups"]) == 80
    assert sum(g["sample_count"] for g in facts["groups"]) == 2
    assert all(g["estimated_usd"] is None and g["billed_usd"] is None for g in facts["groups"])
    assert facts["groups"][1]["attempt_id"] == "try-1"
    assert execution_usage_facts(tmp_path, workflow_run_id="other")["available"] is False


def test_missing_telemetry_or_run_identity_is_not_zero_cost(tmp_path: Path):
    state = tmp_path / "state"
    assert execution_usage_facts(state)["available"] is False
    assert not state.exists()
    CostTracker(state / "cost.jsonl").record_usage("reader", 10, 5, model="unknown-model")
    facts = execution_usage_facts(state, workflow_run_id="run-1")
    assert facts["groups"] == []
    assert facts["unattributed_project_samples"] == 1
    project = execution_usage_facts(state)
    assert project["scope"] == "project_history"
    assert project["groups"][0]["stage_id"] == ""
    assert project["groups"][0]["estimated_usd"] is None


def test_plan_inspection_exposes_real_usage_without_mutating_receipts(tmp_path, capsys):
    import json
    from zf.cli.main import main

    config = tmp_path / "zf.yaml"
    config.write_text("project:\n  name: sample\n  state_dir: custom-state\n")
    state = tmp_path / "custom-state"
    CostTracker(state / "cost.jsonl").record_usage("planner", 20, 5, model="unknown-model")
    before = (state / "cost.jsonl").read_bytes()
    task_map = tmp_path / "task_map.json"
    task_map.write_text('{"tasks": []}')
    assert main(["plan", "inspect-execution", "--config", str(config),
                 "--task-map", str(task_map), "--flow-kind", "prd"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["usage_observations"]["groups"][0]["total_tokens"] == 25
    assert report["usage_observations"]["groups"][0]["estimated_usd"] is None
    assert (state / "cost.jsonl").read_bytes() == before


@pytest.mark.parametrize("binding", ["exact", "other-run", "ambiguous", "missing"])
def test_usage_stage_uses_only_exact_unambiguous_operation_binding(tmp_path, binding):
    log = event_log_from_project(tmp_path)
    writer = EventWriter(log)
    if binding != "missing":
        writer.append(ZfEvent(
            id="operation-request", type="workflow.operation.requested", origin="kernel",
            payload={"workflow_run_id": "OTHER" if binding == "other-run" else "RUN",
                     "operation_id": "OP", "parent_stage_id": "issue-lanes-impl"},
        ))
    if binding == "ambiguous":
        writer.append(ZfEvent(
            type="workflow.operation.requested", origin="kernel",
            payload={"workflow_run_id": "RUN", "operation_id": "OP", "parent_stage_id": "different"},
        ))
    tracker = CostTracker(tmp_path / "cost.jsonl")
    event = writer.append(ZfEvent(type="agent.usage", actor="writer", payload={
        "backend": "codex", "model": "unknown-model", "workflow_run_id": "RUN",
        "operation_id": "OP", "active_attempt_id": "ATTEMPT",
        "usage_semantics": "delta", "usage_sample_id": "sample",
        "usage": {"input_tokens": 20, "output_tokens": 5},
    }))
    apply_agent_usage_event(tracker, event)
    log.close()
    row = execution_usage_facts(tmp_path, workflow_run_id="RUN")["groups"][0]
    assert row["stage_id"] == ("issue-lanes-impl" if binding == "exact" else "")
    assert row["total_tokens"] == 25
    assert row["attempt_id"] == "ATTEMPT"
    assert row["estimated_usd"] is None
