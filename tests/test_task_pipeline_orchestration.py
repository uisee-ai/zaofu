"""OA decisions must change WRC selection, not only persist an artifact."""

from dataclasses import replace

from tests.test_orchestrator_agent_run_plan import (
    _runtime,
    _checkpoint,
    _decision,
    _submit,
)
from tests.test_task_pipeline_reconciler import _policy, _task
from zf.runtime.orchestrator_agent_decision_apply import (
    apply_orchestrator_agent_decision,
)
from zf.runtime.task_pipeline_affinity import task_pipeline_preferred_roles
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.task_pipeline_orchestration import admitted_pipeline_delegations


def _admitted(tmp_path):
    runtime, trigger, loaded, package, ref = _runtime(tmp_path)
    runtime.config.roles.append(replace(runtime.config.roles[-1], instance_id="dev-2"))
    _, prepared = _checkpoint(runtime, trigger, loaded)
    decision = _decision(prepared, role_ref="dev-2")
    unit = decision["run_plan"]["graph"]["work_units"][0]
    unit.update(task_ids=["TASK-1"], stage_ids=["impl"])
    outcome = apply_orchestrator_agent_decision(
        runtime, _submit(runtime, prepared, decision)
    )
    assert outcome["status"] == "applied"
    policy = _policy()
    policy["mode"] = "blocking"
    policy["pools"]["impl"]["role_instances"] = ["dev-1", "dev-2"]
    contexts = {"TASK-1": {**vars(loaded), "flow_kind": "prd"}}
    return runtime, policy, contexts, outcome


def test_admitted_oa_preference_changes_selection(tmp_path):
    runtime, policy, contexts, _ = _admitted(tmp_path)
    projection = TaskPipelineReconciler().reconcile(
        oa_delegations=admitted_pipeline_delegations(
            runtime, generation_contexts=contexts, policy=policy
        ),
        policy=policy,
        tasks=[_task("TASK-1", priority=1)],
        operations=[],
        attempts=[],
        preferred_role_instances=task_pipeline_preferred_roles(
            runtime, generation_contexts=contexts
        ),
    )
    assert projection["dispatchable"]["impl"][0]["role_instance"] == "dev-2"


def test_busy_oa_preference_falls_back_with_receipt(tmp_path):
    runtime, policy, contexts, _ = _admitted(tmp_path)
    delegation = admitted_pipeline_delegations(
        runtime, generation_contexts=contexts, policy=policy
    )
    projection = TaskPipelineReconciler().reconcile(
        policy=policy,
        tasks=[_task("TASK-1", priority=1)],
        operations=[],
        attempts=[{"attempt_id": "other", "status": "sent", "instance_id": "dev-2"}],
        oa_delegations=delegation,
    )
    assignment = projection["dispatchable"]["impl"][0]
    assert assignment["role_instance"] == "dev-1"
    assert (
        assignment["orchestration_assignment"]["reason"]
        == "oa_preferred_unavailable_or_incompatible"
    )


def test_oa_preference_does_not_override_task_capability_or_dependency(tmp_path):
    runtime, policy, contexts, _ = _admitted(tmp_path)
    delegation = admitted_pipeline_delegations(
        runtime, generation_contexts=contexts, policy=policy
    )
    policy["pools"]["impl"]["worker_profiles"] = [
        {"role": "dev-1", "capabilities": ["database"]},
        {"role": "dev-2", "capabilities": []},
    ]
    task = _task("TASK-1", priority=1)
    task["contract"] = {"evidence_contract": {"required_capabilities": ["database"]}}
    kwargs = dict(
        policy=policy,
        tasks=[task],
        operations=[],
        attempts=[],
        oa_delegations=delegation,
    )
    assert (
        TaskPipelineReconciler().reconcile(**kwargs)["dispatchable"]["impl"][0][
            "role_instance"
        ]
        == "dev-1"
    )
    task["blocked_by"] = ["UNFINISHED"]
    assert TaskPipelineReconciler().reconcile(**kwargs)["dispatchable"]["impl"] == []


def test_oa_preference_precedes_session_affinity_and_replays(tmp_path):
    runtime, policy, contexts, _ = _admitted(tmp_path)
    kwargs = dict(
        policy=policy,
        tasks=[_task("TASK-1", priority=1)],
        operations=[],
        attempts=[],
        preferred_role_instances={"TASK-1": {"impl": "dev-1"}},
    )
    first = TaskPipelineReconciler().reconcile(
        **kwargs,
        oa_delegations=admitted_pipeline_delegations(
            runtime, generation_contexts=contexts, policy=policy
        ),
    )
    second = TaskPipelineReconciler().reconcile(
        **kwargs,
        oa_delegations=admitted_pipeline_delegations(
            runtime, generation_contexts=contexts, policy=policy
        ),
    )
    assert first == second
    assert first["dispatchable"]["impl"][0]["role_instance"] == "dev-2"
    contexts["TASK-1"]["task_map_generation"] = "unadmitted-generation"
    assert (
        admitted_pipeline_delegations(
            runtime, generation_contexts=contexts, policy=policy
        )
        == {}
    )


def test_corrupt_admitted_plan_cannot_silently_fall_back(tmp_path):
    import pytest

    runtime, policy, contexts, outcome = _admitted(tmp_path)
    path = runtime.state_dir / outcome["execution_plan_ref"]["ref"]
    path.write_text("{}")
    with pytest.raises(ValueError):
        admitted_pipeline_delegations(
            runtime, generation_contexts=contexts, policy=policy
        )
