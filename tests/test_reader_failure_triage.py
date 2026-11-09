"""Input execution failure must not masquerade as a missing human verdict."""
from pathlib import Path

import pytest

from tests.test_manual_evidence_gate import _manual_task, _triage_events
from tests.test_run_manager_rework_triage import _state
from zf.core.config.loader import load_config
from zf.core.task.store import TaskStore
from zf.runtime.rework_triage import classify_rework_trigger
from zf.runtime.run_manager_rework_triage import pending_immediate_replan_actions
from zf.runtime.run_manager import build_run_manager_projection


def _events():
    failure, triage = _triage_events(failed_command_id="VC-MANUAL")
    failure.payload.update(failure_class="reader_execution_failure",
        blocker_kind="reader_execution_failure",
        reason="required reads and evidence are missing; no product checks executed")
    failure.payload["impl_self_check"]["command_receipts"] = []
    return failure, triage


@pytest.mark.parametrize("owner", ["wrc", "run_manager"])
def test_input_failure_outranks_evidence_text_and_product_routing(owner):
    config = load_config(Path(__file__).parents[1] / "zf-prd-v5.yaml")
    config.workflow.orchestration.normal_frontier_owner = owner
    config.workflow.rework_routing["dev.blocked"] = "dev"
    failure, _ = _events()
    result = classify_rework_trigger(failure, config)
    assert result.recommended_action == "request_diagnosis"
    assert result.suspected_owner == "run_manager"
    assert not result.should_increment_retry and not result.retryable


def test_historical_misclassification_cannot_recreate_manual_gate(tmp_path):
    store = TaskStore(tmp_path / "kanban.json")
    _manual_task(store)
    assert pending_immediate_replan_actions(list(_events()), task_store=store) == []


@pytest.mark.parametrize("owner", ["wrc", "run_manager"])
def test_actual_rm_projection_routes_old_misclassified_failure_to_diagnosis(tmp_path, owner):
    config = load_config(Path(__file__).parents[1] / "zf-prd-v5.yaml")
    config.workflow.orchestration.normal_frontier_owner = owner
    state, log, _ = _state(tmp_path)
    _manual_task(TaskStore(state / "kanban.json"))
    for event in _events(): log.append(event)
    projection = build_run_manager_projection(state, events=log.read_all(), config=config, project_root=tmp_path)
    actions = [a for a in projection["pending_actions"] if a.get("task_id") == "TASK-MANUAL"]
    assert len(actions) == 1, actions
    assert actions[0]["action"] == "diagnose-attention"
    assert actions[0]["source_event_id"] == "blocked-manual"
    assert actions[0]["policy_decision"]["decision"] == "needs_diagnosis"


@pytest.mark.parametrize("owner", ["wrc", "run_manager"])
def test_later_known_failure_supersedes_old_input_diagnosis(tmp_path, owner):
    from zf.core.events.model import ZfEvent
    from zf.runtime.failure_diagnosis_routing import pending_failure_diagnoses

    config = load_config(Path(__file__).parents[1] / "zf-prd-v5.yaml")
    config.workflow.orchestration.normal_frontier_owner = owner
    state, _, _ = _state(tmp_path)
    _manual_task(TaskStore(state / "kanban.json"))
    failure, triage = _events()
    newer = ZfEvent(type="dev.blocked", task_id="TASK-MANUAL", payload={
        "failure_class": "candidate_dependency_missing", "reason": "current external dependency unavailable"})
    assert pending_failure_diagnoses(state, [failure, triage, newer], config) == []
