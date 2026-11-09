from unittest.mock import patch

from tests.test_run_manager_rework_triage import _config, _state
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.run_manager import build_run_manager_projection


def test_projection_uses_task_store_dependency_closure(tmp_path):
    state, log, _ = _state(tmp_path)
    store = TaskStore(state / "kanban.json")
    for task in [Task(id="T1", title="blocked"),
                 Task(id="T2", title="dependent", blocked_by=["T1"]),
                 Task(id="T3", title="independent")]:
        store.add(task)
    log.append(ZfEvent(type="human.escalate", task_id="T1", payload={
        "blocking_scope": "task", "checkpoint_id": "gate-T1", "reason": "owner input",
    }))
    actions = [{
        "action": "orchestrator-rework-triage", "task_id": task_id,
        "checkpoint_id": task_id, "request_id": task_id,
        "safe_resume_action": "request_orchestrator_triage",
        "failure_class": "settled_impl_requires_semantic_review",
        "fingerprint": task_id, "action_policy": "auto_decide",
        "source_event_type": "workflow.call.result.admitted",
        "target_operation_id": "op-" + task_id, "task_map_generation": "g1",
        "failure_count": 1, "source_event_ids": ["failed-" + task_id],
    } for task_id in ["T2", "T3"]]
    with patch("zf.runtime.orchestrator_agent_execution_review.pending_execution_reviews", return_value=actions):
        result = build_run_manager_projection(state, events=log.read_all(), config=_config())
    assert result["continuation"]["next_operation"] is not None, result["completion_profile"]["pending_human_decisions"]
    assert result["continuation"]["next_operation"]["task_id"] == "T3"
