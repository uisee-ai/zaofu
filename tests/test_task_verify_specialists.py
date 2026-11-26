from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_read_only_dynamic_continuation import _RecordingTransport, _config, _state
from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_pipeline_runtime import TaskPipelineWaiting
from zf.runtime.task_verify_specialists import specialist_inputs, validate_specialists
from zf.runtime.workflow_operation import reduce_workflow_operations


def fixture(tmp_path):
    state, _, _ = _state(tmp_path)
    config = _config()
    signer = RoleConfig(name="signer", role_kind="reader")
    second = RoleConfig(name="inspector", role_kind="reader")
    config.roles.extend([signer, second])
    config.workflow.stages.append(replace(config.workflow.stages[0], id="second", roles=["inspector"]))
    runtime = Orchestrator(state, config, _RecordingTransport())
    runtime.task_store.add(Task(id="T-1", title="task"))
    from zf.runtime.task_contract_snapshot import effective_contract_revision
    from zf.runtime.task_pipeline_contexts import TASK_PIPELINE_GENERATION_ADMITTED, TASK_PIPELINE_GENERATION_SCHEMA

    revision = effective_contract_revision(runtime.task_store.get("T-1"))
    runtime.event_writer.append(ZfEvent(type=TASK_PIPELINE_GENERATION_ADMITTED, id="E1", origin="kernel",
        actor="orchestrator", payload={"schema_version": TASK_PIPELINE_GENERATION_SCHEMA,
            "task_ids": ["T-1"], "workflow_run_id": "RUN", "task_map_generation": "G1"}))
    runtime.event_writer.append(ZfEvent(type="orchestrator.run_plan.admitted", origin="kernel", actor="orchestrator",
        payload={"workflow_run_id": "RUN", "task_map_generation": "G1",
                 "execution_plan_ref": {"ref": "plan", "sha256": "1" * 64}}))
    roster = [{"obligation_id": r, "role": r, "question": f"check {r}", "input_refs": ["task-contract"],
               "expected_output": "independent assessment"} for r in ["researcher", "inspector"]]
    assignment = {"specialists": roster, "preferred_roles": ["signer"], "work_unit_id": "U1",
                  "contract_revision": revision, "execution_plan_ref": {"ref": "plan", "sha256": "1" * 64}}
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    contract = write_immutable_json_sidecar(state, {"task_id": "T-1"}, root="test-inputs",
        kind="contract", schema_version="task-contract.v1", created_by="kernel")
    kwargs = dict(context={"workflow_run_id": "RUN", "task_map_generation": "G1", "generation_admitted_event_id": "E1"},
                  task_id="T-1", target_commit="a" * 40, signer=signer, contract_ref=contract, generation=1)
    return runtime, assignment, kwargs


def test_specialists_dispatch_both_once_before_formal_signer(tmp_path):
    runtime, assignment, args = fixture(tmp_path)
    reasons = {"selection_reason": "Inspect formatting compatibility.", "completion_condition": "List every incompatible field."}
    assignment["specialists"][0].update(reasons)
    for _ in range(2):
        with pytest.raises(TaskPipelineWaiting, match="researcher.*inspector"):
            specialist_inputs(runtime, assignment, **args)
    requests = runtime.event_log.query(type="workflow.invoke.requested")
    assert len(requests) == 2
    for event in requests:
        runtime.run_once(events=[event])
        runtime.run_once(events=[event])
    accepted = runtime.event_log.query(type="workflow.invoke.accepted")
    assert len(accepted) == 2, [e.payload for e in runtime.event_log.query(type="workflow.invoke.rejected")]
    waves = runtime.event_log.query(type="task.fanout.requested")
    assert len(waves) == 2
    assert {w.payload["verify_specialist_obligation"]["role"] for w in waves} == {"researcher", "inspector"}
    for wave in waves:
        runtime.run_once(events=[wave])
    sent = runtime.event_log.query(type="fanout.child.dispatched")
    assert len(sent) == 2, [(e.type, e.payload.get("reason", "")) for e in runtime.event_log.read_all()]
    ops = reduce_workflow_operations(runtime.event_log.read_all())
    assert {o["role_instance"] for o in ops.values() if o["parent_operation_id"]} == {"researcher", "inspector"}
    for dispatch in sent:
        assert dispatch.payload["verify_specialist_obligation"]["role"] == dispatch.payload["role_instance"]
        briefing = Path(dispatch.payload["briefing_path"]).read_text()
        assert "Task Verify specialist evidence" in briefing
        for key, value in reasons.items():
            if dispatch.payload["role_instance"] == "researcher":
                assert dispatch.payload["verify_specialist_obligation"][key] == value and value in briefing
            else:
                assert key not in dispatch.payload["verify_specialist_obligation"] and value not in briefing
    assert all(ops[r.payload["workflow_operation_id"]]["status"] == "running" for r in requests)
    from tests.e2e.test_agent_owned_verification_mock_e2e import _record_required_reads, _success_payload

    for count, dispatch in enumerate(sent, start=1):
        payload = dispatch.payload["payload"]
        assert "specialist-contract" in {r["source_id"] for r in payload["required_reads"]}
        _record_required_reads(runtime.state_dir, payload)
        result = _success_payload(Path(dispatch.payload["briefing_path"]))
        result["report"] = {"status": "passed", "summary": "Assessment by " + dispatch.payload["role_instance"],
                            "recommendation": "approve", "findings": []}
        event = runtime.event_writer.append(ZfEvent(type=payload["canonical_success_event"],
            actor=dispatch.payload["role_instance"], task_id="T-1", payload=result, correlation_id="RUN"))
        runtime.run_once(events=[event])
        if count == 1:
            with pytest.raises(TaskPipelineWaiting, match="inspector"):
                specialist_inputs(runtime, assignment, **args)
    inputs = specialist_inputs(runtime, assignment, **args)
    assert [i["source_id"] for i in inputs] == ["specialist-researcher", "specialist-inspector"]
    assert len({i["sha256"] for i in inputs}) == 2
    assert specialist_inputs(runtime, assignment, **args) == inputs
    assert len(runtime.event_log.query(type="workflow.invoke.requested")) == 2
    # Explicitly adopting these two unchanged contracts on a replacement work unit
    # must preserve each original signed opinion without new transports.
    successor = {**assignment, "work_unit_id": "new-work-unit",
                 "execution_plan_ref": {"ref": "revised", "sha256": "2" * 64},
                 "specialists": [{**m, "reuse_operation_id": r.payload["workflow_operation_id"]}
                                 for m, r in zip(assignment["specialists"], requests, strict=True)]}
    assert specialist_inputs(runtime, successor, **args) == inputs
    assert specialist_inputs(runtime, successor, **args) == inputs
    reused = runtime.event_log.query(type="task.verify.specialist.reused")
    assert len(reused) == 2 and all(e.payload["execution_plan_ref"] == successor["execution_plan_ref"] for e in reused)
    assert len(runtime.event_log.query(type="workflow.invoke.requested")) == 2
    args["context"].update(plan_artifact_package_ref="", plan_artifact_package_digest="")
    weaker = {**successor, "specialists": [dict(m) for m in successor["specialists"]]}
    weaker["specialists"][0].pop("completion_condition")
    with pytest.raises(TaskPipelineWaiting):
        specialist_inputs(runtime, weaker, **args)
    assert len(runtime.event_log.query(type="task.verify.specialist.reused")) == 2
    with pytest.raises(TaskPipelineWaiting):
        specialist_inputs(runtime, assignment, **{**args, "target_commit": "b" * 40})
    assert len(runtime.event_log.query(type="workflow.invoke.requested")) == 4


def test_registered_read_only_patterns_cannot_impersonate_the_signer(tmp_path):
    runtime, unit, args = fixture(tmp_path)
    assert validate_specialists(runtime, unit, ["signer"]) == []
    assert validate_specialists(runtime, unit, ["inspector"])
    unit["specialists"][0]["pattern_id"] = "second"
    assert validate_specialists(runtime, unit, ["signer"])
    unit["specialists"][0]["pattern_id"] = "research-wave"
    unit["specialists"][1]["obligation_id"] = "researcher"
    assert validate_specialists(runtime, unit, ["signer"])


def test_stale_or_forged_invocation_has_no_worker_effect(tmp_path):
    runtime, assignment, args = fixture(tmp_path)
    with pytest.raises(TaskPipelineWaiting):
        specialist_inputs(runtime, assignment, **args)
    request = runtime.event_log.query(type="workflow.invoke.requested")[0]
    forged = replace(request, origin="worker", actor="researcher", id="forged")
    runtime.run_once(events=[forged])
    task = runtime.task_store.get("T-1")
    task.contract.acceptance = "A changed contract"
    runtime.task_store.update(task.id, contract=task.contract)
    runtime.run_once(events=[request])
    assert not runtime.event_log.query(type="task.fanout.requested")
    assert len(runtime.event_log.query(type="workflow.invoke.rejected")) == 2


def test_new_oa_assignment_supersedes_only_its_previous_specialists(tmp_path):
    runtime, assignment, args = fixture(tmp_path)
    with pytest.raises(TaskPipelineWaiting):
        specialist_inputs(runtime, assignment, **args)
    request = runtime.event_log.query(type="workflow.invoke.requested")[0]
    runtime.event_writer.append(ZfEvent(type="orchestrator.run_plan.admitted", origin="kernel", actor="orchestrator",
        payload={"workflow_run_id": "RUN", "task_map_generation": "G1", "verify_assignment_task": "T-1",
                 "execution_plan_ref": {"ref": "new", "sha256": "2" * 64}}))
    runtime.run_once(events=[request])
    assert not runtime.event_log.query(type="fanout.child.dispatched")
    assert "superseded" in runtime.event_log.query(type="workflow.invoke.rejected")[-1].payload["reason"]


def test_specialist_pattern_cannot_publish_a_formal_verdict(tmp_path):
    runtime, unit, args = fixture(tmp_path)
    runtime.config.workflow.stages[0].aggregate.success_event = "judge.passed"
    assert validate_specialists(runtime, unit, ["signer"])
