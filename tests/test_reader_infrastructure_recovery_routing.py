"""Infrastructure readers keep a diagnosis owner without a synthetic human gate."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from zf.core.config.schema import FanoutAggregateConfig, WorkflowStageConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.orchestrator_reactor import EventReactorMixin
from zf.runtime.run_manager import build_run_manager_projection


def fixture(tmp_path, *, resident=True, registered=True, kind="infra"):
    state = tmp_path / "runtime"
    state.mkdir()
    writer = EventWriter(EventLog(state / "events.jsonl"))
    config = ZfConfig()
    config.runtime.run_manager.resident_agent.enabled = resident
    failure_type = "prd.scan.failed" if registered else "custom.scan.failed"
    config.workflow.stages = [WorkflowStageConfig(
        id="prd-scan", trigger="prd.requested", topology="fanout_reader", roles=[],
        aggregate=FanoutAggregateConfig(failure_event=failure_type))]
    writer.emit("run.started", payload={"workflow_run_id": "run-1"})
    trigger = writer.emit("prd.requested", correlation_id="run-1", payload={"workflow_run_id": "run-1", "pdd_id": "P1",
        "requirement_spec_digest": "unchanged-input", "prd_ref": "docs/prd.md"})
    writer.emit("fanout.started", correlation_id="run-1", payload={"workflow_run_id": "run-1", "stage_id": "prd-scan",
        "fanout_id": "f1", "trigger_event_id": trigger.id})
    failure = writer.emit(failure_type, correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "stage_id": "prd-scan", "fanout_id": "f1", "pdd_id": "P1",
        "requirement_spec_digest": "unchanged-input",
        "failure_kind": kind, "failure_class": "semantic_rejection" if kind == "quality" else "reader_execution_failure",
        "reason": "one remaining reader failed", "failed_children": ["tech"]})
    runtime = SimpleNamespace(config=config, state_dir=state, event_log=writer.event_log,
        event_writer=writer, escalation=Mock(), task_store=Mock())
    return runtime, failure


def test_runtime_preserves_registered_rm_diagnosis_without_forced_owner_gate(tmp_path):
    runtime, failure = fixture(tmp_path)
    decision = EventReactorMixin._on_reader_stage_failure_replan(runtime, failure)
    runtime.escalation.escalate.assert_not_called()
    assert decision.target_role == "run-manager"
    projection = build_run_manager_projection(runtime.state_dir, config=runtime.config,
        events=runtime.event_log.read_all(), project_root=tmp_path)
    assert not projection["completion_profile"]["pending_human_decisions"]
    actions = [a for a in projection["pending_actions"] if failure.id in a.get("source_event_ids", [])]
    assert any(a["action"] == "diagnose-attention" for a in actions), actions
    assert not any(e.type in {"repair.action.requested", "prd.scan.completed", "task.done"}
                   for e in runtime.event_log.read_all())


@pytest.mark.parametrize("change", ["disabled", "unregistered", "semantic"])
def test_missing_diagnosis_owner_and_unchanged_semantic_input_keep_owner_gate(tmp_path, change):
    runtime, failure = fixture(tmp_path, resident=change != "disabled",
        registered=change != "unregistered", kind="quality" if change == "semantic" else "infra")
    EventReactorMixin._on_reader_stage_failure_replan(runtime, failure)
    runtime.escalation.escalate.assert_called_once()
