"""An exact OA assignment uses the same local audit ACs as the formal Verify route."""
from types import SimpleNamespace

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.runtime.oa_task_verify_assignment import current_scope
from zf.runtime.task_contract_snapshot import effective_contract_revision


def task_for(mode):
    local_owner = "task_verify" if mode == "mixed" else "candidate_verify"
    ids = [("local", local_owner, True), ("final", "candidate_verify", True)]
    if mode == "ordinary":
        ids.append(("writer-check", "task_verify", True))
    elif mode == "optional":
        ids.append(("optional-probe", "candidate_verify", False))
    else:
        ids.append(("person", "human", False))
    criteria = [{"id": key, "statement": key, "verification_owner": owner, "mandatory": required,
                 "verification_command_ids": [key], "verification_tier": "runtime"} for key, owner, required in ids]
    commands = [{"id": key, "command": f"probe {key}", "owner": owner, "acceptance_ids": [key]}
                for key, owner, _ in ids]
    evidence = {"runtime_only": True, "write_free": True,
                "task_pipeline_entry_mode": "verify_only", "immutable_baseline": "git:" + "e" * 40,
                "frozen_candidate_acceptance_ids": ["final"]}
    if mode == "broken":
        evidence["frozen_candidate_acceptance_ids"] = ["unknown"]
    if mode == "ordinary":
        evidence = {"task_pipeline_entry_mode": "standard"}
    if mode == "scope_mismatch":
        evidence.pop("frozen_candidate_acceptance_ids")
    return Task(id="T", contract=TaskContract(acceptance_criteria=criteria, evidence_contract=evidence,
        scope=["src/"] if mode in {"ordinary", "scope_mismatch"} else [], validation={"commands": commands}))


@pytest.mark.parametrize(("mode", "expected"), [("audit", ["local"]), ("optional", ["local"]),
    ("mixed", ["local"]), ("ordinary", ["writer-check"]), ("scope_mismatch", []), ("broken", [])])
def test_named_scope_requires_exact_current_task_criteria_and_authority(mode, expected):
    task = task_for(mode)
    runtime = SimpleNamespace(task_store={"T": task})
    revision = effective_contract_revision(task)
    context = {"task_contract_revisions": {"T": revision}}
    def bind(selected, *, current=context, roles=("reader",)):
        unit = {"verification_scope": {"acceptance_ids": selected, "required_evidence": ["local result"]}}
        return current_scope(runtime, "T", current, {}, unit, list(roles))
    assert bind(["*"])["contract_revision"] == revision
    if expected:
        assert bind(expected)["verification_scope"]["acceptance_ids"] == expected
    assert bind(["final"]) is None
    assert bind([*expected, "final"]) is None
    assert bind(["different-task"]) is None
    assert bind(["local", "person"]) is None
    assert bind(["optional-probe"]) is None
    assert bind(["local"], current={"task_contract_revisions": {"T": "stale"}}) is None
    assert bind(["*"], roles=()) is None


def test_scoped_correction_offers_the_same_ids_as_exact_admission(tmp_path, monkeypatch):
    from tests.test_orchestrator_agent_run_plan import _runtime
    from zf.runtime.oa_task_verify_assignment import _request_correction

    runtime, trigger, _, _, package_ref = _runtime(tmp_path)
    task = task_for("audit")
    context = {"workflow_run_id": "run", "task_map_generation": "map", "plan_artifact_package_ref": package_ref["ref"],
               "plan_artifact_package_digest": package_ref["sha256"]}
    monkeypatch.setattr("zf.runtime.orchestrator_agent_operations.request_orchestrator_agent_checkpoint",
                        lambda runtime, **params: params["payload"])
    result = _request_correction(runtime, task, context, trigger, {})
    assert result["verify_assignment_acceptance"] == ["local"]
    assert result["verify_assignment_revision"] == effective_contract_revision(task)


def test_stock_oa_admission_dispatches_the_exact_audit_to_one_verify_seat(tmp_path):
    from tests.test_orchestrator_agent_run_plan import _checkpoint, _decision, _runtime, _submit
    from tests.test_task_pipeline_reconciler import _policy
    from zf.core.config.schema import RoleConfig
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
    from zf.runtime.task_pipeline_orchestration import admitted_pipeline_delegations
    from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler

    runtime, trigger, loaded, _, _ = _runtime(tmp_path)
    role = "verify-1"
    runtime.config.roles.append(RoleConfig(name=role, instance_id=role, role_kind="reader", backend="mock"))
    policy = _policy()
    policy.update(mode="blocking", verify_assignment_mode="oa_run_plan")
    policy["pools"]["verify"].update(role_instances=[role], capacity=1)
    runtime.config.workflow.flow_metadata["task_pipeline"] = policy
    task = task_for("audit")
    runtime.task_store = TaskStore(runtime.state_dir / "kanban.json")
    runtime.task_store.add(task)
    _, prepared = _checkpoint(runtime, trigger, loaded)
    decision = _decision(prepared, role_ref=role, source=prepared.context.source_manifest["sources"][0])
    unit = decision["run_plan"]["graph"]["work_units"][0]
    unit.update(stage_ids=["verify"], task_ids=[task.id],
                verification_scope={"acceptance_ids": ["local"], "required_evidence": ["check current baseline"]})
    decision["run_plan"]["delegation"][0].update(preferred_role_refs=[role], capability_refs=[])
    assert apply_orchestrator_agent_decision(runtime, _submit(runtime, prepared, decision))["status"] == "applied"
    context = {**vars(loaded), "flow_kind": "prd", "task_contract_revisions": {task.id: effective_contract_revision(task)}}
    delegation = admitted_pipeline_delegations(runtime, generation_contexts={task.id: context}, policy=policy)
    view = TaskPipelineReconciler().reconcile(policy=policy, tasks=[task], operations=[], attempts=[], oa_delegations=delegation)
    assert view["dispatchable"]["impl"] == []
    assigned, = view["dispatchable"]["verify"]
    assert (assigned["task_id"], assigned["role_instance"]) == (task.id, role)
    assert assigned["orchestration_assignment"]["verification_scope"]["acceptance_ids"] == ["local"]
