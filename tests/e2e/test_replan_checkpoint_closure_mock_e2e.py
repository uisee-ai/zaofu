"""Checkpoint recovery through real admission, Git workspaces and mock workers."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from tests.test_writer_fanout_runtime import _state, _git, _commit
from tests.test_controller_rendered_fanout_runtime import _RecordingTransport
from tests.test_task_pipeline_profile import _compile, _task_pipeline
from tests.test_plan_artifact_package import _package
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig, WorkflowOrchestrationConfig, QualityGateConfig
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package import write_plan_artifact_package, package_event_payload
from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.impl_self_check import completion_payload_template
from zf.runtime.task_pipeline_briefing import verification_result_template
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from tests.test_orchestrator_agent_semantic_failure import _submit_rework
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision
from zf.runtime.artifact_read_capability import provision_role_artifact_read_credential
from zf.runtime.verification_commands import verification_command_required_for_stage

pytestmark = pytest.mark.mock_e2e
RUN = "run-recovery"


def _runtime(root):
    state, _, _, seed = _state(root, synthesize_canonical=True)
    config = seed.config
    config.quality_gates["static"] = QualityGateConfig(
        enabled=True, required_checks=["test -f slice-1.txt && test -f slice-2.txt && test -f slice-3.txt"],
    )
    config.workflow.orchestration = WorkflowOrchestrationConfig(
        mode="semantic_control", checkpoints=["semantic_failure"],
        checkpoint_policies={"semantic_failure": "blocking"},
    )
    config.workflow.admission_replan.enabled = True
    config.workflow.admission_replan.resynth_trigger = "prd.plan.requested"
    policy = _compile(_task_pipeline(mode="blocking"))
    policy["pools"]["impl"]["role_instances"] = ["dev-1", "dev-2"]
    policy["pools"]["verify"]["role_instances"] = ["verify-1", "verify-2"]
    config.workflow.flow_metadata = {
        "task_pipeline": policy,
        "result_protocol": {"semantic_submit_profiles": {
            "implementation": "blocking", "task-verify": "blocking",
        }},
    }
    config.roles.extend(RoleConfig(
        name="verify", instance_id=f"verify-{i}", backend="mock", role_kind="reader",
    ) for i in (1, 2))
    config.roles.append(RoleConfig(
        name="orchestrator", instance_id="orchestrator", backend="mock", role_kind="reader",
    ))
    config.roles = [replace(r, lifecycle=RoleLifecycleConfig(mode="on_demand")) for r in config.roles]
    for role in config.roles:
        provision_role_submit_credential(state, role.instance_id)
    provision_role_artifact_read_credential(
        state, "orchestrator", role_name="orchestrator", provider="mock",
    )
    return Orchestrator(state, config, _RecordingTransport(), project_root=root)


def _map():
    body = {"schema_version": "task-map.v1", "feature_id": "F-11111111", "tasks": [{
        "task_id": f"TASK-{i}", "title": f"Deliver slice {i}",
        "owner_role": "dev", "allowed_paths": [f"slice-{i}.txt"],
        "allowed_paths_reason": "isolated recovery proof",
        "blocked_by": ["TASK-1"] if i > 1 else [],
        "acceptance_criteria": [{
            "id": f"AC-{i}", "text": f"slice {i} exists",
            "verification_owner": "task_verify", "verification_tier": "task_non_smoke",
            "verification_command_ids": [f"check-{i}"],
        }],
        "verification": f"test -f slice-{i}.txt",
        "validation": {"commands": [{
            "id": f"check-{i}", "command": f"test -f slice-{i}.txt",
            "acceptance_ids": [f"AC-{i}"], "owner": "impl_self_check",
            "tier": "task_non_smoke", "deterministic": True, "reusable": True,
            "rolling_smoke": True,
        }]},
    } for i in (1, 2, 3)]}
    foundation = body["tasks"][0]
    foundation["acceptance_criteria"].append({
        "id": "AC-PRODUCT", "text": "All slices exist", "mandatory": True,
        "verification_owner": "task_verify", "verification_tier": "task_non_smoke",
        "verification_command_ids": ["product-check"],
    })
    foundation["validation"]["commands"].append({
        "id": "product-check", "command": "test -f slice-1.txt && test -f slice-2.txt && test -f slice-3.txt",
        "acceptance_ids": ["AC-PRODUCT"], "owner": "impl_self_check", "tier": "task_non_smoke",
    })
    return body


def _admit(runtime, body, generation, **scope):
    descriptor = write_immutable_json_sidecar(
        runtime.state_dir, body, root="fixtures/maps", kind="task_map",
        schema_version="task-map.v1", created_by="mock-planner",
    )
    package = _package(runtime.state_dir, generation=generation, revision=generation)
    package["workflow_run_id"] = RUN
    port = next(p for p in package["produced"] if p["logical_name"] == "task_map")
    port.update(ref=descriptor["ref"], sha256=descriptor["sha256"])
    ref = write_plan_artifact_package(runtime.state_dir, package)
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted", correlation_id=RUN,
        payload=package_event_payload(package, ref, status="admitted"),
    ))
    trigger = runtime.event_writer.append(ZfEvent(
        type="task_map.ready", correlation_id=RUN, actor="mock-planner",
        payload={"pdd_id": "F-11111111", "feature_id": "F-11111111",
                 "workflow_run_id": RUN, "flow_kind": "prd",
                 "task_map_ref": descriptor["ref"], "task_map_generation": generation,
                 "plan_artifact_package_id": ref["package_id"],
                 "plan_artifact_package_ref": ref["ref"],
                 "plan_artifact_package_digest": ref["sha256"], **scope},
    ))
    runtime.run_once(events=[trigger])
    return trigger


def _dispatches(runtime):
    return [e for e in runtime.event_log.read_all() if e.type == "task.pipeline.stage.dispatched"]


def _submit(runtime, dispatch, result):
    payload = dispatch.payload
    manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    for required in payload["required_reads"]:
        read_attempt_artifact(runtime.state_dir, manifest=manifest, **{
            key: required[key] for key in ("source_id", "artifact_id", "json_path")
        })
    role = payload["role_instance"]
    token = (runtime.state_dir / f"private/result-submit/roles/{role}.token").read_text().strip()
    submitted = SemanticResultSubmitService(
        state_dir=runtime.state_dir, event_log=runtime.event_log, event_writer=runtime.event_writer,
    ).submit(operation_id=payload["operation_id"], semantic_result=result,
             role_instance=role, credential=token)
    event = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
    runtime.run_once(events=[event])
    return event


def _check_result(runtime, dispatch):
    payload = dispatch.payload
    contract = json.loads((runtime.state_dir / payload["contract_snapshot_ref"]).read_text())
    owner = "task_verify" if payload["task_pipeline_stage"] == "verify" else "impl_self_check"
    commands = [c for c in contract["verification_commands"] if verification_command_required_for_stage(
        c, verification_owner=owner, task_id=dispatch.task_id,
    )]
    for command in commands:
        subprocess.run(command["command"], cwd=payload["workdir"], shell=True, check=True)
    evidence = write_immutable_json_sidecar(
        runtime.state_dir, {"commands": commands, "exit_code": 0,
                            "target_commit": _git(Path(payload["workdir"]), "rev-parse", "HEAD")},
        root="fixtures/checks", kind="command_evidence", schema_version="mock-check.v1",
        created_by=payload["role_instance"],
    )["ref"]
    if payload["task_pipeline_stage"] == "verify":
        result = verification_result_template(contract)
    else:
        check = completion_payload_template(
            contract_snapshot=contract, task_item=payload, task_id=dispatch.task_id,
            run_id=RUN, child_id=payload["operation_id"],
        )["impl_self_check"]
        result = {"schema_version": "implementation-result.v1", "execution_status": "completed",
                  "verdict": "passed", "target_commit": "<HEAD commit>",
                  "self_check": check, "changed_files": [f"slice-{dispatch.task_id[-1]}.txt"],
                  "evidence_refs": [evidence], "known_gaps": [], "summary": "checkpoint self-check"}
    def fill(value):
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        if value in ("<HEAD commit>", "<exact target_commit from Stage Context>"):
            return _git(Path(payload["workdir"]), "rev-parse", "HEAD")
        return evidence if isinstance(value, str) and value.startswith("<durable") else value
    return fill(result)


def _oa_replan(runtime, failure):
    recovery = write_immutable_json_sidecar(
        runtime.state_dir, {"schema_version": "task-recovery-context.v1", "task_id": "TASK-1",
                            "failure_event_ids": [failure.id]},
        root="fixtures/recovery", kind="recovery_context",
        schema_version="task-recovery-context.v1", created_by="mock-run-manager",
    )
    request = runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.failure.requested", actor="run-manager", task_id="TASK-1",
        correlation_id=RUN, payload={"problem_class": "semantic", "workflow_run_id": RUN,
        "failure_fingerprint": "phase-conflict", "failure_event_ids": [failure.id],
        "trigger_event_type": failure.type, "recovery_context_ref": recovery},
    ))
    prepared = request_semantic_failure_checkpoint(runtime, request)
    decision = _submit_rework(runtime, prepared, action="return_to_plan")
    applied = apply_orchestrator_agent_decision(runtime, decision)
    assert applied["applied"], applied
    assert apply_orchestrator_agent_decision(runtime, decision) == applied
    triggers = [e for e in runtime.event_log.read_all() if e.type == "prd.plan.requested"]
    assert len(triggers) == 1
    return triggers[0]


def test_local_replan_retains_commit_and_releases_two_downstream_workers(tmp_path):
    runtime = _runtime(tmp_path)
    body = _map()
    _admit(runtime, body, "g1")
    dispatched = _dispatches(runtime)
    assert [(e.task_id, e.payload["task_pipeline_stage"]) for e in dispatched] == [("TASK-1", "impl")], [
        (e.type, e.payload) for e in runtime.event_log.read_all()
        if e.type.endswith(("deferred", "waiting", "rejected", "failed"))
    ]
    old = dispatched[0]
    old_workdir = Path(old.payload["workdir"])
    checkpoint = _commit(old_workdir, "slice-1.txt", "retained implementation\n", "feat: retain checkpoint")
    product_check = body["tasks"][0]["validation"]["commands"][-1]["command"]
    assert subprocess.run(product_check, cwd=old_workdir, shell=True).returncode == 1
    untouched = {key: runtime.task_store.get(key) for key in ("TASK-2", "TASK-3")}
    failed = _submit(runtime, old, {
        "schema_version": "implementation-result.v1", "execution_status": "completed",
        "verdict": "blocked", "target_commit": checkpoint, "changed_files": ["slice-1.txt"],
        "known_gaps": ["local contract requires downstream artifact"],
        "summary": "contract phase conflict", "failure_class": "contract_phase_conflict",
        "evidence_refs": ["mock://phase-conflict"],
    })
    replan = _oa_replan(runtime, failed)
    assert replan.payload["task_ids"] == ["TASK-1"]
    assert len(_dispatches(runtime)) == 1  # OA applied is not execution recovery.
    revised = deepcopy(body)
    revised["tasks"][0]["base_commit"] = checkpoint
    revised["tasks"][0]["source_refs"] = [f"git:{checkpoint}"]
    revised["tasks"][0]["title"] = "Recheck retained foundation under local contract"
    revised["tasks"][0]["acceptance_criteria"][-1]["verification_owner"] = "candidate_verify"
    revised["tasks"][0]["validation"]["commands"][-1]["owner"] = "candidate_verify"
    trigger = _admit(runtime, revised, "g2", **{
        key: replan.payload[key] for key in ("task_ids", "resume_scope", "rework_of", "rework_attempt")
    })
    new = _dispatches(runtime)[-1]
    assert new.task_id == "TASK-1" and new.payload["task_map_generation"] == "g2"
    assert new.payload["operation_id"] != old.payload["operation_id"]
    assert new.payload["workdir"] != old.payload["workdir"]
    assert _git(Path(new.payload["workdir"]), "rev-parse", "HEAD") == checkpoint
    assert _git(old_workdir, "rev-parse", "HEAD") == checkpoint
    assert untouched == {key: runtime.task_store.get(key) for key in untouched}
    _submit(runtime, new, _check_result(runtime, new))
    verify = _dispatches(runtime)[-1]
    assert verify.payload["task_pipeline_stage"] == "verify", verify.payload
    assert verify.payload["target_commit"] == checkpoint
    verified = _check_result(runtime, verify)
    assert next(r for r in verified["requirement_results"] if r["acceptance_id"] == "AC-PRODUCT")["status"] == "not_applicable"
    _submit(runtime, verify, verified)
    assert runtime.task_store.get("TASK-1").status == "done"
    for _ in range(4):
        runtime.run_once(events=[])
    downstream = [e for e in _dispatches(runtime) if e.task_id in untouched]
    assert {e.task_id for e in downstream} == set(untouched)
    assert len({e.payload["role_instance"] for e in downstream}) == 2
    assert {e.payload["task_map_generation"] for e in downstream} == {"g1"}
    assert all((Path(e.payload["workdir"]) / "slice-1.txt").read_text() == "retained implementation\n" for e in downstream)
    before = len(_dispatches(runtime))
    restarted = Orchestrator(runtime.state_dir, runtime.config, runtime.transport, project_root=tmp_path)
    restarted.run_once(events=[trigger])
    restarted.run_once(events=[])
    assert len(_dispatches(restarted)) == before
    assert len([e for e in restarted.event_log.read_all() if e.type == "task.done" and e.task_id == "TASK-1"]) == 1
    assert restarted.config.quality_gates["static"].required_checks == [product_check]
    assert not any(e.type in {"run.delivery.completed", "run.goal.completed"} for e in restarted.event_log.read_all())
    assert not any(e.type == "candidate.ready" for e in restarted.event_log.read_all())
    for impl in downstream:
        _commit(Path(impl.payload["workdir"]), f"slice-{impl.task_id[-1]}.txt", "downstream\n", "feat: downstream")
        _submit(restarted, impl, _check_result(restarted, impl))
    verifications = [e for e in _dispatches(restarted) if e.task_id in untouched
                     and e.payload["task_pipeline_stage"] == "verify"]
    assert len(verifications) == 2
    for verification in verifications:
        _submit(restarted, verification, _check_result(restarted, verification))
    for _ in range(4):
        restarted.run_once(events=[])
    assert all(restarted.task_store.get(key).status == "done" for key in ("TASK-1", "TASK-2", "TASK-3"))
    frozen = [e for e in restarted.event_log.read_all() if e.type == "candidate.ready"]
    assert len(frozen) == 1
    assert frozen[0].payload["completed_task_ids"] == ["TASK-1", "TASK-2", "TASK-3"]
    assert frozen[0].payload["task_map_generation"] == "g2"
    assert len(frozen[0].payload["task_integration_receipt_refs"]) == 3
    contexts = task_pipeline_generation_contexts(restarted.event_log.read_all())
    assert {key: c["task_map_generation"] for key, c in contexts.items()} == {
        "TASK-1": "g2", "TASK-2": "g1", "TASK-3": "g1",
    }
    for _ in range(2):
        restarted.run_once(events=[])
    assert len([e for e in restarted.event_log.read_all() if e.type == "candidate.ready"]) == 1
    restarted.event_writer.append(ZfEvent(type="simulation.done", payload={"scenario": "replan_checkpoint_closure"}))


def test_failed_child_selector_does_not_activate_other_changed_contracts(tmp_path):
    from zf.runtime.writer_fanout_admission import load_writer_task_map

    runtime = _runtime(tmp_path)
    body = _map()
    _admit(runtime, body, "g1")
    prior = runtime.task_store.get("TASK-2")
    revised = deepcopy(body)
    revised["tasks"][0]["title"] = "New foundation"
    revised["tasks"][1]["title"] = "Related changed contract"
    descriptor = write_immutable_json_sidecar(
        runtime.state_dir, revised, root="fixtures/maps", kind="task_map",
        schema_version="task-map.v1", created_by="mock-planner",
    )
    event = ZfEvent(type="task_map.ready", payload={
        "task_map_ref": descriptor["ref"], "task_ids": ["TASK-1"],
        "resume_scope": "failed_children_only", "rework_of": "local-conflict",
    })
    loaded = load_writer_task_map(
        stage=runtime.config.workflow.stages[0], event=event, pdd_id="F-11111111",
        state_dir=runtime.state_dir, project_root=tmp_path,
    )
    assert [item["task_id"] for item in loaded.task_items] == ["TASK-1"]
    assert runtime.task_store.get("TASK-2") == prior
    # The diagnostic selection is not a revision manifest. A second changed
    # contract needs explicit admission; the full file alone does not apply it.
    event.payload["task_ids"] = ["TASK-1", "TASK-2"]
    selected = load_writer_task_map(
        stage=runtime.config.workflow.stages[0], event=event, pdd_id="F-11111111",
        state_dir=runtime.state_dir, project_root=tmp_path,
    )
    assert [item["task_id"] for item in selected.task_items] == ["TASK-1", "TASK-2"]


def test_running_request_cannot_use_prestart_config_apply(tmp_path):
    from tests.test_workflow_config_apply import _proposal, _apply_payload, _execute
    from zf.runtime.control_actions import ControlledActionService
    from zf.runtime.workflow_requests import mark_workflow_request
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter

    proposal, ref, validation = _proposal(tmp_path)
    state = tmp_path / ".zf"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    for status in ("approved", "submitted", "running"):
        mark_workflow_request(state, "req-apply", status=status, actor="mock-operator", writer=writer)
    before = (tmp_path / "zf.yaml").read_bytes()
    service = ControlledActionService(state, writer, project_root=tmp_path, actor="mock-operator")
    result = _execute(service, _apply_payload(proposal, ref, validation))
    assert result["ok"] is False and result["code"] == "proposal_superseded"
    assert (tmp_path / "zf.yaml").read_bytes() == before
