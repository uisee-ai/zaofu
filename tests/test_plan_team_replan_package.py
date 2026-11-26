"""A team Plan's authorized successor crosses both package and Task Map admission on replay."""

import json

from tests.test_prd_stage_teams import select
from tests.test_reader_fanout_runtime import _state
from tests.test_workflow_registered_controller import _candidate, assembly
from zf.core.config.loader import load_config
from zf.core.events import ZfEvent
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package, reduce_plan_artifact_packages
from zf.runtime.plan_artifact_package_runtime import admit_synthesized_plan_package, admit_task_map_trigger_package
from zf.runtime.run_contract import stable_json_sha256, write_run_contract
from zf.runtime.stage_failure_replan import plan_reader_stage_replan


def test_rejected_team_plan_admits_one_successor_package_and_task_map(assembly, tmp_path):
    select(assembly, ["product-scan", "tech-scan"])
    _, _, path = _candidate(assembly)
    config = load_config(path)
    root = tmp_path / "replan-run"
    root.mkdir()
    state, log, _, runtime = _state(root)
    runtime.config.workflow.stages = config.workflow.stages
    runtime.config.workflow.flow_metadata = {"flow_kind": "prd", "artifact_package": {"mode": "blocking"},
                                            "stage_teams": config.workflow.flow_metadata["stage_teams"]}
    artifacts = state / "artifacts"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "requirement.md").write_text("The command must remain available.")
    contract = {"schema_version": "run-contract.v1", "workflow": {"kind": "prd"}}
    write_run_contract(state, {**contract, "contract_digest": stable_json_sha256(contract)})
    refs = []
    for revision in (1, 2):
        path = artifacts / f"task-map-{revision}.json"
        path.write_text(json.dumps({"schema_version": "task-map.v1", "goal_claims": [{
            "goal_claim_id": "KEEP", "text": "The command remains available.", "mandatory": True}],
            "tasks": [{"task_id": "CLI", "goal_claim_ids": ["KEEP"],
                       "acceptance_criteria": [{"id": "AC-1", "text": f"Exercise the version {revision} entry point."}]}]}))
        refs.append(str(path.relative_to(root)))
    stage = next(s for s in config.workflow.stages if s.id == "prd-plan")
    base = {"pdd_id": "F", "goal_id": "F", "workflow_run_id": "run", "flow_kind": "prd",
            "stage_id": stage.id, "requirement_spec_ref": str((artifacts / "requirement.md").relative_to(root))}
    old = runtime.event_writer.append(ZfEvent(type="task_map.ready", origin="kernel", correlation_id="run",
        payload={**base, "task_map_ref": refs[0], "target_ref": "candidate/run"}))
    first = admit_task_map_trigger_package(runtime, old, [stage])
    assert first and first.payload["artifact_package_status"] == "admitted"
    failure = runtime.event_writer.append(ZfEvent(type="prd.plan.failed", origin="kernel", correlation_id="run",
        causation_id=old.id, payload={**base, "failure_scope": "plan_admission", "trigger_event_id": old.id,
        "plan_admission_incident_id": "current-incident", "reason": "Update the CLI implementation scope."}))
    successor, reason = plan_reader_stage_replan(config, log.read_all(), failure, state_dir=state,
        authorized_generation="owner-generation-2", generation_source_event_id=failure.id,
        authorization_event_id="approval-2", authorization_ref="approvals/replan-2")
    assert successor, reason
    trigger = runtime.event_writer.append(successor)
    assert trigger.type == "prd.requested" and trigger.payload["rework_attempt"] == 1
    assert trigger.payload["rework_of"] == old.id and trigger.payload["stage_replan_generation"] == "owner-generation-2"

    # Start at the admitted authority boundary of the successor Planner/critic.
    # The other dispatch test exercises individual specialist receipts and how they reach the Planner.
    verdict = runtime.event_writer.append(ZfEvent(type="fanout.synth.completed", actor=stage.aggregate.synth_role,
        causation_id=trigger.id, correlation_id="run", payload={"workflow_run_id": "run",
            "plan_synth_contract_digest": "2"*64, "reviewed_plan_candidate_digest": "2"*64}))
    def publish():
        status, decision, result = admit_synthesized_plan_package(runtime, event=verdict,
            manifest={"workflow_run_id": "run", "pdd_id": "F", "trigger_payload": trigger.payload},
            stage_id=stage.id, trace_id="run", success_event="task_map.ready", final_status="completed",
            recommendation="approve", artifact_payload={**base, "task_map_ref": refs[1]})
        assert (status, decision) == ("completed", "approve"), result
        return result

    revised = publish()
    assert revised["plan_artifact_package_digest"] != first.payload["plan_artifact_package_digest"]
    ready = runtime.event_writer.append(ZfEvent(type="task_map.ready", origin="kernel", causation_id=verdict.id,
        correlation_id="run", payload=revised))
    for _ in range(2):
        assert publish() == revised
        assert admit_task_map_trigger_package(runtime, ready, [stage]).payload == revised
    admitted = log.query(type="plan.artifact_package.admitted")
    assert len(admitted) == 2 and admitted[-1].causation_id == verdict.id
    assert admitted[-1].payload["supersedes_package_ref"] == first.payload["plan_artifact_package_ref"]
    assert reduce_plan_artifact_packages(log.read_all(), workflow_run_id="run")["current"]["package_ref"] == revised["plan_artifact_package_ref"]
    body = hydrate_plan_artifact_package(state, {"ref": revised["plan_artifact_package_ref"],
                                                "sha256": revised["plan_artifact_package_digest"]})
    assert body["task_map_generation"] == revised["task_map_generation"] != first.payload["task_map_generation"]
