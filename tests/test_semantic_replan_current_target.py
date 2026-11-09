from pathlib import Path

import pytest

from tests.test_run_manager_rework_triage import _config, _state
from zf.core.config.schema import RoleConfig, WorkflowStageConfig
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.task_contract_snapshot import current_task_contract_identity


@pytest.mark.parametrize("candidate_contains_base", [True, False])
def test_replan_uses_current_admitted_package_and_integrated_candidate(tmp_path: Path, monkeypatch, candidate_contains_base):
    state, log, writer = _state(tmp_path)
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="BLOCKED", status="in_progress", contract=TaskContract(
        feature_id="PRD", evidence_contract={"workflow_run_id": "run-1", "source_refs": {
            "task_map_ref": "old-map.json", "task_map_generation": "old",
            "plan_artifact_package_ref": "old-package.json", "plan_artifact_package_digest": "old-digest",
        }},
    )))
    identity = current_task_contract_identity(store.get("BLOCKED"), task_map_ref="old-map.json")
    blocked = writer.emit("dev.blocked", task_id="BLOCKED", payload={
        **identity, "workflow_run_id": "run-1", "failure_class": "upstream_contract_gap",
        "base_commit": "base", "contract_snapshot_ref": "failed-contract.json",
        "contract_snapshot_digest": "f" * 64,
        "plan_artifact_package_ref": "old-package.json", "plan_artifact_package_digest": "old-digest",
    })
    writer.emit("task.rework.triage.completed", task_id="BLOCKED", payload={
        "failed_event_id": blocked.id, "classification": "design_issue",
        "recommended_action": "request_replan", "retryable": False,
    })
    ready = writer.emit("task_map.ready", payload={
        "workflow_run_id": "run-1", "pdd_id": "PRD", "task_map_ref": "new-map.json",
        "task_map_generation": "new", "candidate_head_commit": "stale-plan-head",
    })
    writer.emit("plan.artifact_package.admitted", payload={
        "workflow_run_id": "run-1", "package_slot": "execution_plan", "status": "admitted",
        "plan_revision": "new", "task_map_generation": "new", "source_event_id": ready.id,
        "package_id": "new-package", "package_ref": "new-package.json", "package_digest": "n" * 64,
    })
    integrated = writer.emit("integration.queue.integrated", payload={
        "workflow_run_id": "run-1", "status": "integrated", "candidate_branch": "candidate/PRD",
        "candidate_head": "current-candidate", "receipt_ref": {"ref": "receipt.json", "sha256": "r" * 64},
    })
    writer.emit("integration.queue.integrated", payload={
        "workflow_run_id": "other-run", "status": "integrated", "candidate_branch": "candidate/OTHER",
        "candidate_head": "unrelated", "receipt_ref": {"ref": "other.json", "sha256": "o" * 64},
    })
    writer.emit("integration.queue.integrated", payload={
        "workflow_run_id": "run-1", "status": "failed", "candidate_branch": "candidate/PRD",
        "candidate_head": "failed-head", "receipt_ref": {"ref": "failed.json"},
    })
    # An unadmitted map proposal must not replace the package-linked map.
    writer.emit("task_map.ready", payload={"workflow_run_id": "run-1", "pdd_id": "PRD", "task_map_ref": "draft.json"})
    config = _config(with_orchestrator=True)
    config.roles.append(RoleConfig(name="flow-discovery", backend="mock", skills=["zf-gap-task-synth"]))
    config.workflow.stages.append(WorkflowStageConfig(id="discovery", trigger="flow.discovery.requested", topology="fanout_reader", roles=["flow-discovery"], flow_kind="prd"))
    monkeypatch.setattr("zf.runtime.semantic_replan._git_commit_is_ancestor", lambda *a, **k: candidate_contains_base)
    result = run_manager_tick(state_dir=state, writer=writer, event_log=log, config=config, project_root=tmp_path, spawn_repairs=False, action_filter={"semantic-replan-request"})
    assert result.actions_applied == 1
    request = next(e for e in log.read_all() if e.type == "flow.discovery.requested")
    p = request.payload
    assert p["target_ref"] == ("current-candidate" if candidate_contains_base else "base")
    assert p["candidate_head_commit"] == "current-candidate"
    assert p["candidate_event_id"] == integrated.id
    assert p["task_map_ref"] == "new-map.json"
    assert p["task_map_generation"] == "new"
    assert p["plan_artifact_package_ref"] == "new-package.json"
    assert p["contract_snapshot_ref"] == "failed-contract.json"
    assert p["contract_revision"] == identity["contract_revision"]
    assert store.get("BLOCKED").contract.evidence_contract["source_refs"]["task_map_generation"] == "old"
