from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.run_manager import _candidate_rework_action_from_plan


@pytest.mark.parametrize("explicit_gap", [False, True])
def test_replacement_preserves_or_requests_reviewed_consumption_contract(
    tmp_path: Path, explicit_gap: bool,
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    ref = "artifacts/task-map.json"
    path = state / ref
    path.parent.mkdir()
    source = {"schema_version": "task-map.v1", "tasks": [{
        "task_id": "TASK-AVERAGE", "title": "average", "owner_role": "dev",
        "allowed_paths": ["app/src/average.ts", "app/tests/average.test.mjs"],
        "acceptance_criteria": ["input-order Number average within approved domain"],
        "verify_commands": ["npm test"], "source_refs": ["docs/prd.md"],
        "evidence_contract": {"plan_input_selection": {
            "schema_version": "task-plan-input-selection.v1",
            "implementation": ["task_execution_input"],
            "task-verify": ["task_execution_input"],
        }},
    }]}
    path.write_text(json.dumps(source))
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="TASK-AVERAGE", title="average"))
    store.update("TASK-AVERAGE", status="done")
    head = "a" * 40
    events = [ZfEvent(id="current-map", type="task_map.ready", correlation_id="run-1", payload={
        "pdd_id": "PRD-1", "task_map_ref": ref, "task_map_generation": "current",
        "source_commit": head,
    }), ZfEvent(id="candidate-failed", type="test.failed", correlation_id="run-1", payload={
        "pdd_id": "PRD-1", "candidate_head_commit": head,
        "candidate_ref": "refs/heads/candidate/PRD-1",
    })]
    plan = SimpleNamespace(
        pdd_id="PRD-1", trace_id="run-1", action="retrigger", attempt=1,
        source_event_id="candidate-failed", source_event_type="test.failed",
        failed_task_ids=["TASK-AVERAGE"],
        rework_paths=["app/src/average.ts", "runtime.host_verification"],
        rework_summary={}, feedback=["review contract conflict and host evidence"],
    )
    gap = {
        "task_id": "TASK-REPAIR", "supersedes_task_ids": ["TASK-AVERAGE"],
        "evidence_contract": {"plan_input_selection": {
            "schema_version": "task-plan-input-selection.v1",
            "implementation": ["task_execution_input", "source_index"],
            "task-verify": ["task_execution_input", "acceptance_matrix"],
        }},
    }
    if explicit_gap:
        plan.rework_summary = {"gap_tasks": [gap]}

    action = _candidate_rework_action_from_plan(state, events, plan, project_root=tmp_path)

    if explicit_gap:
        assert action["candidate_rework_action"] == "retrigger"
        assert action["rework_summary"]["gap_tasks"] == [gap]
    else:
        assert action["candidate_rework_action"] == "replan"
        assert action["safe_resume_action"] == "candidate_replan"
        assert action["target_ref"] == head
        assert "orchestrator.replan_requested" in action["expected_downstream_events"]
        assert not action["rework_summary"].get("gap_tasks")
        assert "plan_input_selection" in action["rework_summary"]["scope_replan_reason"]
    assert action["source_event_id"] == "candidate-failed"
    assert action["task_map_generation"] == "current"
    assert store.get("TASK-AVERAGE").status == "done"
    assert json.loads(path.read_text()) == source
