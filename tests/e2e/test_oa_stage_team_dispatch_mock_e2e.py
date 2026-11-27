"""Admitted OA assignments drive actual WRC workdirs, transports and receipts."""
from dataclasses import replace
from pathlib import Path

import pytest

from tests.e2e.test_verification_eight_slots_mock_e2e import _runtime
from zf.core.config.schema import RoleConfig, WorkflowStageConfig
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.task_contract_snapshot import effective_contract_revision
from zf.runtime.task_pipeline_runtime import reconcile_task_pipeline_runtime

pytestmark = pytest.mark.mock_e2e


def team_runtime(tmp_path, monkeypatch, *, specialist):
    runtime, deliveries, target = _runtime(tmp_path, monkeypatch, "verify", task_count=8)
    policy = runtime.config.workflow.flow_metadata["task_pipeline"]
    policy["verify_assignment_mode"] = "oa_run_plan"
    policy["pools"]["verify"]["capacity"] = 3
    generation = runtime.event_log.query(type="task.pipeline.generation.admitted")[0]
    tasks = runtime.task_store.list_all()
    revisions = {t.id: effective_contract_revision(t) for t in tasks}
    runtime.event_writer.append(replace(generation, id="generation-with-bindings", payload={
        **generation.payload, "task_contract_revisions": revisions}))
    body = generation.payload
    identity = {name: body[name] for name in ("workflow_run_id", "task_map_generation",
                "plan_artifact_package_ref", "plan_artifact_package_digest")}
    unit = {"work_unit_id": "checks", "task_ids": list(revisions), "stage_ids": ["verify"],
            "verification_scope": {"acceptance_ids": ["*"], "required_evidence": ["independent fixture result"]}}
    units = [unit]
    if specialist:
        monkeypatch.setattr(runtime.transport, "is_alive", lambda instance: instance == "auditor")
        runtime.config.roles.append(RoleConfig(name="auditor", role_kind="reader", backend="mock"))
        runtime.config.workflow.stages.append(WorkflowStageConfig(id="audit", trigger="workflow.invoke.requested",
            topology="fanout_reader", roles=["auditor"]))
        unit["task_ids"] = [t for t in unit["task_ids"] if t != "TASK-0"]
        units.append({**unit, "work_unit_id": "audit-check", "task_ids": ["TASK-0"],
            "specialists": [{"obligation_id": "audit", "role": "auditor", "input_refs": ["contract:$.acceptance_criteria"],
                             "question": "Does the fixture meet the task's criterion?", "expected_output": "fixture finding"}]})
    plan = {"schema_version": "orchestrator-execution-plan.v1", "identity": identity, "work_units": units,
        "delegation": [{"work_unit_id": u["work_unit_id"], "preferred_role_refs": [f"verify-{i}" for i in range(8)]}
                       for u in units]}
    descriptor = write_immutable_json_sidecar(runtime.state_dir, plan, root="fixtures/oa", kind="execution_plan",
                                             schema_version=plan["schema_version"], created_by="mock-oa")
    runtime.event_writer.append(ZfEvent(type="orchestrator.run_plan.admitted", origin="kernel", actor="orchestrator",
        payload={**identity, "execution_plan_ref": descriptor}))
    return runtime, deliveries, target


def test_eight_tasks_dispatch_three_actual_oa_verifiers_and_resume_once(tmp_path, monkeypatch):
    runtime, deliveries, target = team_runtime(tmp_path, monkeypatch, specialist=False)
    reconcile_task_pipeline_runtime(runtime)
    sent = runtime.event_log.query(type="task.pipeline.stage.dispatched")
    assert len(deliveries) == len(sent) == 3
    assert len({e.payload["role_instance"] for e in sent}) == 3
    assert len({e.payload["workdir"] for e in sent}) == 3
    assert all(e.payload["target_commit"] == target and e.payload["orchestration_assignment"]["work_unit_id"] == "checks"
               for e in sent)
    reconcile_task_pipeline_runtime(runtime)
    assert len(deliveries) == 3


def test_specialist_reaches_fanout_and_signer_reads_independent_result(tmp_path, monkeypatch):
    from tests.e2e.test_agent_owned_verification_mock_e2e import _record_required_reads, _success_payload

    runtime, _, target = team_runtime(tmp_path, monkeypatch, specialist=True)
    # Leave a free seat for the formal signer once the independent opinion arrives.
    for task in runtime.task_store.list_all():
        if task.id != "TASK-0":
            runtime.task_store.update(task.id, status="done")
    for _ in range(2):
        reconcile_task_pipeline_runtime(runtime)
    requests = runtime.event_log.query(type="workflow.invoke.requested")
    assert len(requests) == 1 and not runtime.event_log.query(type="task.pipeline.stage.dispatched")
    runtime.run_once(events=requests)
    runtime.run_once(events=runtime.event_log.query(type="task.fanout.requested"))
    child, = runtime.event_log.query(type="fanout.child.dispatched")
    payload = child.payload["payload"]
    _record_required_reads(runtime.state_dir, payload)
    body = _success_payload(Path(child.payload["briefing_path"]))
    body["report"] = {"status": "passed", "summary": "Independent fixture observation", "recommendation": "approve",
                      "findings": []}
    event = runtime.event_writer.append(ZfEvent(type=payload["canonical_success_event"], actor="auditor",
        task_id="TASK-0", payload=body, correlation_id="run-8"))
    runtime.run_once(events=[event])
    reconcile_task_pipeline_runtime(runtime)
    signed, = runtime.event_log.query(type="task.pipeline.stage.dispatched")
    assert signed.payload["target_commit"] == target and signed.payload["role_instance"] != "auditor"
    assert any(r["source_id"] == "specialist-audit" for r in signed.payload["required_reads"])
    assert len(runtime.event_log.query(type="workflow.invoke.requested")) == 1


def test_public_pause_holds_approved_task_verify_until_resume(tmp_path, monkeypatch):
    from tests.test_run_admission import _control
    from zf.runtime.task_pipeline_projection import read_task_pipeline_projection

    runtime, deliveries, _ = team_runtime(tmp_path, monkeypatch, specialist=False)
    generation = runtime.event_log.query(type="task.pipeline.generation.admitted")[-1]
    run = generation.payload["workflow_run_id"]
    runtime.event_writer.append(ZfEvent(type="run.goal.started", actor="zf-cli", payload={"run_id": run, "status": "active"}))
    assert _control(runtime, action="run-pause", run_id=run)["status"] == "paused"
    for _ in range(2):
        reconcile_task_pipeline_runtime(runtime)
    assert not deliveries and not runtime.event_log.query(type="task.pipeline.stage.dispatched")
    visible = read_task_pipeline_projection(runtime.state_dir, project_root=runtime.project_root, config=runtime.config)
    assert {row["pipeline_stage"] for row in visible["tasks"]} == {"run_paused"}
    assert _control(runtime, action="run-resume", run_id=run)["status"] == "running"
    for _ in range(2):
        reconcile_task_pipeline_runtime(runtime)
    assert len(deliveries) == len(runtime.event_log.query(type="task.pipeline.stage.dispatched")) == 3
