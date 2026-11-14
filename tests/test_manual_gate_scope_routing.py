import pytest

from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.run_manager_rework_triage import pending_immediate_replan_actions
from zf.runtime.semantic_replan import SEMANTIC_REPLAN_ACTION
from tests.test_run_manager_rework_triage import _state


@pytest.mark.parametrize("recommendation", ["request_replan", "request_evidence_reissue"])
def test_upstream_gap_keeps_replan_despite_future_manual_evidence(tmp_path, recommendation):
    state, log, writer = _state(tmp_path)
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="GAP", title="consumer", status="in_progress", contract=TaskContract(
        behavior="consume upstream contract", evidence_contract={
            "required_manual_evidence": ["evidence/qualification.json"]},
    )))
    failed = writer.emit("dev.blocked", actor="dev", task_id="GAP", payload={
        "failure_class": "upstream_contract_gap", "blocker_kind": "upstream_contract_gap",
        "reason": "publisher has no consumer authorization callback",
        "evidence_refs": ["tests/publication-red.json"],
    })
    writer.emit("task.rework.triage.completed", actor="zf-cli", task_id="GAP", payload={
        "failed_event_id": failed.id, "recommended_action": recommendation,
        "classification": "design_issue", "retryable": False,
    })
    actions = pending_immediate_replan_actions(log.read_all(), task_store=store)
    assert len(actions) == 1
    assert actions[0]["action"] == SEMANTIC_REPLAN_ACTION
    assert actions[0]["safe_resume_action"] != "blocked_external_gate"
