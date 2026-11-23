"""A scoped repair cannot silently rewrite a completed, undispatched Task."""
from copy import deepcopy

import pytest

from tests.test_plan_artifact_package import _run_contract
from tests.test_plan_candidate_preflight import _PlanSynthHarness, _source_index, _task_map
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from zf.runtime.plan_candidate_preflight import evaluate_plan_candidate_preflight


def fixture(root):
    baseline = _task_map()
    second = deepcopy(baseline["tasks"][0])
    second.update(task_id="TASK-2", goal_claim_ids=[], allowed_paths=["src/second.py", "tests/test_second.py"])
    second["acceptance_criteria"][0].update(id="AC-2", verification_command_ids=["test-second"])
    second["validation"]["commands"][0].update(id="test-second", acceptance_ids=["AC-2"],
        command="pytest -q tests/test_second.py")
    baseline["tasks"].append(second)
    index = _source_index()
    index["tasks"].append({**index["tasks"][0], "task_id": "TASK-2"})
    candidate = deepcopy(baseline)
    harness = _PlanSynthHarness(root, [{"report": {
        "task_map": candidate, "source_index": index, "plan_md": "# Plan",
    }}])
    state = harness.state_dir
    port = write_immutable_json_sidecar(state, baseline, root="fixtures/maps", kind="task_map",
        schema_version="task-map.v1", created_by="test")
    package = build_plan_artifact_package(workflow_run_id="run-1", flow_kind="issue",
        producer_stage_id="issue-triage", run_contract=_run_contract(state),
        plan_revision="r1", task_map_generation="g1",
        produced=[{**port, "logical_name": "task_map", "producer_stage_id": "issue-triage"}])
    ref = write_plan_artifact_package(state, package)
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="TASK-2", title="Completed second task", status="done", contract=TaskContract(
        evidence_contract={"workflow_run_id": "run-1"})))
    trigger = {"flow_kind": "issue", "workflow_run_id": "run-1", "issue_ref": "issue.md",
        "task_ids": ["TASK-1"], "plan_artifact_package_ref": ref["ref"],
        "plan_artifact_package_digest": ref["sha256"]}
    return harness, trigger, candidate, store


def preflight(harness, trigger):
    return evaluate_plan_candidate_preflight(state_dir=harness.state_dir, project_root=harness.project_root,
        reports=harness.reports, manifest={"trigger_payload": trigger}, metadata={"flow_kind": "issue"})


@pytest.mark.parametrize("change", ["owner", "command", "delete"])
def test_preserved_task_drift_stops_before_critic(tmp_path, change):
    harness, trigger, candidate, _ = fixture(tmp_path)
    if change == "owner":
        candidate["tasks"][1]["acceptance_criteria"][0]["verification_owner"] = "candidate_verify"
    elif change == "command":
        candidate["tasks"][1]["validation"]["commands"][0]["command"] = "echo replacement"
    else:
        candidate["tasks"].pop()
    result = preflight(harness, trigger)
    assert any(e["code"] == "preserved_task_changed" for e in result["errors"])
    harness._dispatch_fanout_synth("fanout-plan", {
        "fanout_id": "fanout-plan", "trace_id": "run-1", "stage_id": "issue-triage",
        "trigger_event_id": "evt-input", "trigger_payload": trigger,
        "aggregate_config": {"success_event": "task_map.ready", "synth_role": "plan-critic"},
        "children": [],
    }, "wait_for_all", "plan-critic")
    assert not harness.critic_dispatch_attempted
    assert harness.finalized.payload["failure_class"] == "plan_candidate_preflight"


def test_selected_task_change_preserves_completed_sibling(tmp_path):
    harness, trigger, candidate, _ = fixture(tmp_path)
    candidate["tasks"][0]["title"] = "Repair the command"
    result = preflight(harness, trigger)
    assert not result["errors"], result["errors"]


def test_candidate_amend_cannot_exempt_a_retained_task(tmp_path):
    harness, trigger, candidate, _ = fixture(tmp_path)
    trigger.update(failed_task_ids=["TASK-1"], resume_scope="failed_children_only")
    candidate.update(resume_scope="gap_tasks_only", amend={"schema_version": "task-map-amend.v1",
        "resume_scope": "gap_tasks_only", "superseded_task_ids": ["TASK-1"], "gap_task_ids": ["TASK-2"]})
    candidate["tasks"][1]["title"] = "Forged replacement"
    assert any(e["code"] == "preserved_task_changed" for e in preflight(harness, trigger)["errors"])


def test_validate_and_submit_preserve_done_row_without_consuming_semantic_retry(tmp_path):
    from tests.test_result_submit import _running_plan_candidate_operation
    from zf.runtime.result_submit import ResultSubmitError

    source = tmp_path / "source"
    source.mkdir()
    harness, _, candidate, _ = fixture(source)
    baseline = deepcopy(candidate)

    def setup(runtime):
        state = runtime.state_dir
        port = write_immutable_json_sidecar(state, baseline, root="fixtures/maps", kind="task_map",
            schema_version="task-map.v1", created_by="test")
        body = build_plan_artifact_package(workflow_run_id="run-plan-candidate", flow_kind="issue",
            producer_stage_id="issue-triage", run_contract=_run_contract(state), plan_revision="r1",
            task_map_generation="g1", produced=[{**port, "logical_name": "task_map", "producer_stage_id": "issue-triage"}])
        ref = write_plan_artifact_package(state, body)
        TaskStore(state / "kanban.json").add(Task(id="TASK-2", title="Preserved", status="done",
            contract=TaskContract(evidence_contract={"workflow_run_id": "run-plan-candidate"})))
        return {"workflow_run_id": "run-plan-candidate", "task_ids": ["TASK-1"],
                "plan_artifact_package_ref": ref["ref"], "plan_artifact_package_digest": ref["sha256"]}

    runtime, prepared, service, token = _running_plan_candidate_operation(tmp_path, validation_setup=setup)
    semantic = {**harness.reports[0]["report"], "status": "passed", "recommendation": "approve",
                "summary": "Scoped repair candidate", "findings": []}
    candidate["tasks"][1]["title"] = "Unrequested rewrite"
    args = dict(operation_id=prepared.operation_id, semantic_result=semantic, role_instance="planner", credential=token)
    validation = service.validate_plan_candidate(**args, project_root=runtime.project_root)
    assert any(e["code"] == "preserved_task_changed" for e in validation["errors"])
    with pytest.raises(ResultSubmitError) as rejected:
        service.submit(**args)
    assert rejected.value.code == "plan_candidate_not_admitted"
    assert rejected.value.details["semantic_rework_cost"] == 0
    semantic["task_map"] = baseline
    assert service.submit(**args).canonical_event_type == "issue.triage.child.completed"


@pytest.mark.parametrize("scope", ["all", "foreign_run", "not_done"])
def test_guard_is_not_an_edit_permission_or_semantic_gate(tmp_path, scope):
    harness, trigger, candidate, store = fixture(tmp_path)
    candidate["tasks"][1]["title"] = "Revised task"
    if scope == "all":
        trigger.pop("task_ids")
    elif scope == "foreign_run":
        task = store.get("TASK-2")
        task.contract.evidence_contract["workflow_run_id"] = "other-run"
        store.update("TASK-2", contract=task.contract)
    else:
        store.update("TASK-2", status="backlog")
    assert not any(e["code"] == "preserved_task_changed" for e in preflight(harness, trigger)["errors"])
