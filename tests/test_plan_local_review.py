"""Local review is available only when the complete structural proof holds."""
from copy import deepcopy
import json

import pytest

from zf.runtime.plan_local_review import scoped_port_paths
from zf.runtime.plan_local_review_policy import scope_critic_reads


def _scope_policy(state, **kwargs):
    return scope_critic_reads(state, fanout_id="new-fanout", stage_id="plan", plan_revision="new-rev", **kwargs)


def _inputs():
    old_map = {"schema_version": "task-map.v1", "goal_claims": ["mandatory"], "tasks": [
        {"task_id": "A", "blocked_by": [], "acceptance": "old"},
        {"task_id": "B", "blocked_by": ["A"]},
        {"task_id": "C", "blocked_by": []},
    ]}
    new_map = deepcopy(old_map)
    new_map["tasks"][0]["acceptance"] = "new"
    old_port = {"schema_version": "task-scoped-plan-input.v1", "shared": {"interface": "v1"}, "tasks": [
        {"task_id": key, "body": {"evidence": key}} for key in ("A", "B", "C")
    ]}
    new_port = deepcopy(old_port)
    new_port["tasks"][0]["body"]["evidence"] = "A-new"
    return old_map, new_map, old_port, new_port


def test_scoped_review_retains_prerequisites_and_downstream_and_old_contract():
    old_map, new_map, old_port, new_port = _inputs()
    paths = scoped_port_paths(old_map=old_map, new_map=new_map, old_port=old_port,
                              new_port=new_port, affected_task_ids=["A"])
    assert paths == (["$.shared", "$.tasks[0]", "$.tasks[1]"],
                     ["$.shared", "$.tasks[0]", "$.tasks[1]"])


def test_local_partition_selection_is_bounded_for_many_unrelated_tasks():
    tasks = [{"task_id": f"T{index}", "blocked_by": []} for index in range(100)]
    old_map = {"schema_version": "task-map.v1", "tasks": tasks}
    new_map = deepcopy(old_map)
    new_map["tasks"][0]["acceptance"] = "changed"
    old_port = {"schema_version": "task-scoped-plan-input.v1", "shared": {"contract": "v1"},
        "tasks": [{"task_id": task["task_id"], "body": {"detail": "x" * 1000}} for task in tasks]}
    new_port = deepcopy(old_port)
    new_port["tasks"][0]["body"]["detail"] = "changed"
    old_paths, new_paths = scoped_port_paths(old_map=old_map, new_map=new_map, old_port=old_port,
        new_port=new_port, affected_task_ids=["T0"])
    assert old_paths == ["$.shared", "$.tasks[0]"]
    assert new_paths == ["$.shared", "$.tasks[0]"]
    selected_bytes = len(json.dumps([old_port["shared"], old_port["tasks"][0],
                                     new_port["shared"], new_port["tasks"][0]]).encode())
    assert selected_bytes < len(json.dumps(new_port).encode()) // 10


@pytest.mark.parametrize("problem", ["global", "outside_map", "outside_port", "shared", "missing",
                                     "duplicate", "unknown_dependency", "bad_schema", "unknown_scope"])
def test_incomplete_or_broad_proof_keeps_full_read(problem):
    old_map, new_map, old_port, new_port = _inputs()
    scope = ["A"]
    if problem == "global": new_map["goal_claims"] = []
    if problem == "outside_map": new_map["tasks"][2]["acceptance"] = "changed"
    if problem == "outside_port": new_port["tasks"][2]["body"] = {}
    if problem == "shared": new_port["shared"]["interface"] = "v2"
    if problem == "missing": new_port["tasks"].pop(1)
    if problem == "duplicate": new_port["tasks"].append(deepcopy(new_port["tasks"][0]))
    if problem == "unknown_dependency": new_map["tasks"][0]["blocked_by"] = ["missing"]
    if problem == "bad_schema": new_port["schema_version"] = "unknown"
    if problem == "unknown_scope": scope = ["missing"]
    assert scoped_port_paths(old_map=old_map, new_map=new_map, old_port=old_port,
                             new_port=new_port, affected_task_ids=scope) is None


def _policy_fixture(state, scenario=""):
    from datetime import datetime, timedelta, timezone
    from io import StringIO
    from uuid import uuid4
    from tests.test_plan_artifact_package import _run_contract
    from zf.core.events.model import ZfEvent
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime.artifact_output_delivery import emit_artifact_output
    from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, canonical_required_reads, read_attempt_artifact, seal_read_ledger
    from zf.runtime.call_result_envelope import normalize_call_result_envelope, write_immutable_json_sidecar
    from zf.runtime.plan_artifact_package import build_plan_artifact_package, package_event_payload, write_plan_artifact_package
    from zf.runtime.workflow_operation import operation_request_hash

    def save(body, name):
        return write_immutable_json_sidecar(state, body, root=f"fixtures/{name}", kind=name,
            schema_version=body.get("schema_version", "fixture.v1"), created_by="test")

    old_map, new_map, old_port, new_port = _inputs()
    if scenario == "global_map": new_map["goal_claims"] = ["changed"]
    if scenario == "outside_map": new_map["tasks"][2]["title"] = "changed"
    if scenario == "outside_port": new_port["tasks"][2]["body"]["evidence"] = "changed"
    if scenario == "shared_port": new_port["shared"]["interface"] = "v2"
    old_map_ref, new_map_ref = save(old_map, "old-map"), save(new_map, "new-map")
    old_port_ref, new_port_ref = save(old_port, "old-port"), save(new_port, "new-port")
    unchanged = save({"schema_version": "planning.v1", "value": "same"}, "planning")
    requirement = save({"schema_version": "requirement.v1", "body": "original acceptance"}, "requirement")
    new_planning = (save({"schema_version": "planning.v1", "value": "changed"}, "new-planning")
                    if scenario == "global_port" else unchanged)
    new_requirement = (save({"schema_version": "requirement.v1", "body": "new global acceptance"}, "new-requirement")
                       if scenario == "global_requirement" else requirement)
    def port(name, ref):
        return {"logical_name": name, "artifact_kind": name, "producer_stage_id": "plan",
                "ref": ref["ref"], "sha256": ref["sha256"]}
    package = build_plan_artifact_package(workflow_run_id="run-1", flow_kind="prd", producer_stage_id="plan",
        run_contract=_run_contract(state), plan_revision="old-rev", task_map_generation="old-gen",
        produced=[port("task_map", old_map_ref), port("execution_spec", old_port_ref), port("planning_result", unchanged)])
    package_ref = write_plan_artifact_package(state, package)
    cert = save({"schema_version": "scoped-plan-review.v1", "workflow_run_id": "run-1", "scope_complete": True,
        "verification_responsibility_preserved": True, "baseline_package_sha256": package_ref["sha256"],
        "baseline_task_map_sha256": old_map_ref["sha256"], "candidate_task_map_sha256": new_map_ref["sha256"],
        "affected_task_ids": ["A"], "evidence_refs": ["requirement"], "ports": [{"logical_name": "execution_spec",
        "baseline_sha256": old_port_ref["sha256"], "candidate_sha256": new_port_ref["sha256"]}]}, "certificate")
    report = save({"schema_version": "fanout-child-result.v1", "task_map_ref": new_map_ref["ref"],
        "role_instance": "planner", "child_id": "planner", "dispatch_id": "new-planner-attempt",
        "plan_ports": [port("execution_spec", new_port_ref), port("planning_result", new_planning)]}, "report")

    def source(source_id, artifact_id, ref):
        return {**ref, "source_id": source_id, "artifact_id": artifact_id, "allowed_paths": ["$"]}
    children = [source("child-result-planner", "report.json", report),
        source("child-artifact-planner-1", "task_map.json", new_map_ref),
        source("child-artifact-planner-2", "execution_spec.json", new_port_ref),
        source("child-artifact-planner-3", "planning.json", new_planning),
        source("child-artifact-planner-4", "scoped_plan_review.json", cert)]
    requirement_source = source("requirement", "requirement.json", new_requirement)
    old_requirement_source = source("requirement", "requirement.json", requirement)
    requested_tasks = [] if scenario == "no_dispatch_scope" else ["A"]
    rework = save({"schema_version": "plan-rework-context.v1", "rework_of": "old-failure", "task_ids": requested_tasks}, "rework")
    inputs = [*children,
        source("plan-rework-package", "package.json", package_ref),
        source("baseline-plan-port-task_map", "old-map.json", old_map_ref),
        source("baseline-plan-port-execution_spec", "old-port.json", old_port_ref),
        source("plan-rework-context", "rework.json", rework), requirement_source]
    contract = save({"schema_version": "plan-synth-contract.v1", "workflow_run_id": "run-1",
        "fanout_id": "new-fanout", "stage_id": "plan", "plan_revision": "new-rev", "trigger_event_id": "trigger",
        "source_bindings": [{k: s[k] for k in ("source_id", "artifact_id", "ref", "sha256")} for s in inputs],
        "child_bindings": [{"child_id": "planner", **{k: children[0][k] for k in ("source_id", "artifact_id", "sha256")}}]}, "contract")
    sources = [source("plan-synth-contract", "contract.json", contract), *inputs]
    manifest = build_attempt_source_manifest(workflow_run_id="run-1", task_id="", attempt_id="new-attempt",
        dispatch_id="new-attempt", sources=sources, metadata={"source_event_id": "trigger", "plan_revision": "new-rev"})
    required = canonical_required_reads(manifest, output_profile_id="plan-synth")

    old_children = [source(f"child-artifact-old-{index}", f"old-{index}.json", ref)
                    for index, ref in enumerate((old_map_ref, old_port_ref, unchanged))]
    old_report_ref = save({"schema_version": "fanout-child-result.v1", "role_instance": "old-planner",
                           "child_id": "planner", "dispatch_id": "old-planner-attempt"}, "old-report")
    old_report = source("child-result-old-planner", "old-report.json", old_report_ref)
    old_contract = save({"schema_version": "plan-synth-contract.v1", "workflow_run_id": "run-1",
        "fanout_id": "old-fanout", "stage_id": "plan", "plan_revision": "old-rev", "child_bindings": [{"child_id": "planner",
        **{k: old_report[k] for k in ("source_id", "artifact_id", "sha256")}}],
        "source_bindings": [{k: s[k] for k in ("source_id", "artifact_id", "ref", "sha256")}
                            for s in [old_report, *old_children]]}, "old-contract")
    previous_body = {"schema_version": "attempt-source-manifest.v1", "workflow_run_id": "run-1",
        "attempt_id": "old-attempt", "sources": [old_requirement_source, old_report, *old_children,
            source("plan-synth-contract", "old-contract.json", old_contract)]}
    previous_manifest = save(previous_body, "previous-manifest")
    old_policy_body = {"schema_version": "input-consumption-policy.v1", "workflow_run_id": "run-1",
        "attempt_id": "old-attempt", "required_reads": [{"source_id": s["source_id"],
        "artifact_id": s["artifact_id"], "artifact_sha256": s["sha256"],
        "json_path": "$.shared" if scenario == "prior_scoped" and s == old_children[1] else "$",
        "allow_truncated": scenario == "prior_truncated" and s == old_children[1]}
        for s in [old_requirement_source, old_report, *old_children]]}
    old_policy = save(old_policy_body, "old-policy")
    old_request_body = {"schema_version": "workflow-operation-request.v1", "workflow_run_id": "run-1",
        "operation_id": "old-operation", "operation_type": "fanout_synth", "request": {
        "output_profile_id": "plan-synth", "active_attempt_id": "old-attempt", "role_instance": "plan-critic",
        "fanout_id": "old-fanout", "source_manifest_digest": previous_manifest["sha256"],
        "input_consumption_policy_ref": old_policy, "read_policy_digest": old_policy["sha256"],
        "required_reads": old_policy_body["required_reads"]}}
    old_request_hash = operation_request_hash(old_request_body)
    old_request = save(old_request_body, "old-request")
    control = save({"schema_version": "plan-synthesis-result.v1", "execution_status": "completed",
        "verdict": "passed", "plan_synth_contract_digest": old_contract["sha256"],
        "reviewed_plan_candidate_digest": old_contract["sha256"]}, "control")
    session = str(uuid4())
    transcript = state.parent / "old-critic-session.jsonl"
    RoleSessionRegistry(state / "role_sessions.yaml", state.parent).bind_codex_session(
        "plan-critic", session, session_path=transcript)
    provider_outputs = []
    for index, row in enumerate([old_requirement_source, old_report, *old_children]):
        read = read_attempt_artifact(state, manifest=previous_body, source_id=row["source_id"],
            artifact_id=row["artifact_id"], actor="plan-critic", role="plan-critic", provider="codex", record=False)
        output = StringIO()
        emit_artifact_output(state, read["content"], [read["read"]], output)
        provider_outputs.append({"timestamp": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
            "type": "response_item", "payload": {"type": "function_call_output", "call_id": f"call-{index}",
            "output": output.getvalue().removesuffix("\n")}})
    transcript.write_text("\n".join(json.dumps(row) for row in [
        {"type": "session_meta", "payload": {"id": session}}, *provider_outputs]) + "\n")
    ledger = seal_read_ledger(state, "old-attempt")
    envelope = normalize_call_result_envelope(source_payload={"run_id": "old-attempt",
        "fanout_id": "old-fanout", "stage_id": "plan", "plan_revision": "old-rev",
        "plan_synth_contract_digest": old_contract["sha256"],
        "reviewed_plan_candidate_digest": old_contract["sha256"], "provider_session_id": session,
        "read_ledger_ref": ledger["ref"], "read_ledger_digest": ledger["sha256"],
        "input_consumption_status": "satisfied"}, control_result=control,
        workflow_run_id="run-1", operation_id="old-operation", request_hash=old_request_hash,
        source_event_id="old-synth", source_event_type="fanout.synth.completed", actor="plan-critic")
    envelope_ref = save(envelope, "envelope")
    synth = ZfEvent(id="old-synth", type="fanout.synth.completed", payload={"workflow_run_id": "run-1",
        "operation_id": "old-operation", "attempt_id": "old-attempt", "status": "completed",
        "fanout_id": "old-fanout", "stage_id": "plan", "plan_revision": "old-rev",
        "recommendation": "approve", "plan_synth_contract_ref": old_contract["ref"],
        "plan_synth_contract_digest": old_contract["sha256"],
        "reviewed_plan_candidate_digest": old_contract["sha256"], "control_result_ref": control})
    dispatch = ZfEvent(type="fanout.synth.dispatched", origin="kernel", payload={"operation_id": "old-operation",
        "attempt_id": "old-attempt", "attempt_source_manifest": previous_manifest,
        "plan_synth_contract_ref": old_contract["ref"], "plan_synth_contract_digest": old_contract["sha256"],
        "input_consumption_policy_ref": old_policy, "role_instance": "plan-critic"})
    admitted_result = ZfEvent(type="workflow.call.result.admitted", origin="kernel", payload={"source_event_id": synth.id,
        "workflow_run_id": "run-1", "operation_id": "old-operation", "control_result_ref": control,
        "envelope_ref": envelope_ref, "read_ledger_ref": ledger})
    admitted_package = ZfEvent(type="plan.artifact_package.admitted", origin="kernel", causation_id="old-synth", payload={
        **package_event_payload(package, package_ref, status="admitted"), "source_event_id": synth.id})
    trigger = ZfEvent(id="trigger", type="prd.plan.requested", origin="kernel", payload={"workflow_run_id": "run-1",
        "rework_of": "old-failure", "task_ids": requested_tasks, "plan_artifact_package_digest": package_ref["sha256"]})
    started = ZfEvent(type="fanout.started", origin="kernel", causation_id="trigger", payload={"fanout_id": "new-fanout",
        "stage_id": "plan", "trigger_event_id": "trigger", "workflow_run_id": "run-1"})
    old_producer = ZfEvent(type="fanout.child.dispatched", origin="kernel", payload={"fanout_id": "old-fanout",
        "child_id": "planner", "run_id": "old-planner-attempt", "role_instance": "old-planner"})
    new_producer = ZfEvent(type="fanout.child.dispatched", origin="kernel", payload={"fanout_id": "new-fanout",
        "child_id": "planner", "run_id": "new-planner-attempt", "role_instance": "planner"})
    operation_started = ZfEvent(type="workflow.operation.started", origin="kernel", payload={
        "operation_id": "old-operation", "active_attempt_id": "old-attempt", "request_hash": old_request_hash,
        "role_instance": "plan-critic", "provider_session_id": session})
    operation_requested = ZfEvent(type="workflow.operation.requested", origin="kernel", payload={
        "operation_id": "old-operation", "workflow_run_id": "run-1", "request_hash": old_request_hash,
        "request_ref": old_request})
    return manifest, required, [old_producer, operation_requested, dispatch, operation_started, admitted_result, synth,
                                admitted_package, trigger, started, new_producer]


@pytest.mark.parametrize("scenario", ["global_map", "outside_map", "outside_port", "shared_port", "global_port",
                                      "global_requirement", "no_dispatch_scope", "prior_scoped", "prior_truncated"])
def test_local_critic_policy_keeps_full_input_when_change_is_not_proved_local(tmp_path, scenario):
    manifest, required, events = _policy_fixture(tmp_path, scenario)
    assert _scope_policy(tmp_path, events=events, manifest=manifest, required=required, explicit=[], critic_role="critic") == required


def test_local_critic_policy_preserves_full_authority_and_compares_both_versions(tmp_path):
    manifest, required, events = _policy_fixture(tmp_path)
    scoped = _scope_policy(tmp_path, events=events, manifest=manifest, required=required, explicit=[], critic_role="critic")
    selected = {(row["source_id"], row["json_path"]) for row in scoped}
    assert ("child-artifact-planner-2", "$") not in selected
    for source in ("child-artifact-planner-2", "baseline-plan-port-execution_spec"):
        assert {(source, "$.shared"), (source, "$.tasks[0]"), (source, "$.tasks[1]")} <= selected
    assert ("baseline-plan-port-task_map", "$.tasks[0]") in selected
    assert ("baseline-plan-port-task_map", "$.tasks[1]") in selected
    assert ("child-artifact-planner-1", "$") in selected
    assert ("child-artifact-planner-4", "$") in selected
    assert ("requirement", "$") in selected


@pytest.mark.parametrize("problem", ["missing_approval", "wrong_manifest", "explicit_full", "tampered_certificate",
                                      "wrong_trigger_scope", "untrusted_previous", "certificate_not_required",
                                      "truncated_candidate", "truncated_requirement", "same_current_role",
                                      "missing_producer_dispatch", "missing_prior_ledger", "missing_prior_session",
                                      "wrong_prior_session", "tampered_prior_witness"])
def test_local_critic_policy_falls_back_for_unproved_inputs(tmp_path, problem):
    manifest, required, events = _policy_fixture(tmp_path)
    explicit = []
    if problem == "missing_approval": events = [e for e in events if e.type != "workflow.call.result.admitted"]
    if problem == "wrong_manifest": manifest["workflow_run_id"] = "other-run"
    if problem == "explicit_full": explicit = [{"source_id": "child-artifact-planner-2", "json_path": "$"}]
    if problem == "wrong_trigger_scope": next(e for e in events if e.id == "trigger").payload["task_ids"] = ["C"]
    if problem == "untrusted_previous": next(e for e in events if e.type == "workflow.call.result.admitted").origin = "worker"
    if problem == "certificate_not_required": required = [r for r in required if r["artifact_id"] != "scoped_plan_review.json"]
    if problem == "truncated_candidate": next(r for r in required if r["source_id"] == "child-artifact-planner-2")["allow_truncated"] = True
    if problem == "truncated_requirement": next(r for r in required if r["source_id"] == "requirement")["max_chars"] = 20
    if problem == "missing_producer_dispatch": events = [e for e in events if e.type != "fanout.child.dispatched"]
    if problem == "missing_prior_ledger": next(e for e in events if e.type == "workflow.call.result.admitted").payload["read_ledger_ref"] = {}
    if problem == "missing_prior_session": events = [e for e in events if e.type != "workflow.operation.started"]
    if problem == "wrong_prior_session": next(e for e in events if e.type == "workflow.operation.started").payload["provider_session_id"] = "other-session"
    if problem == "tampered_prior_witness":
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref

        ledger = next(e for e in events if e.type == "workflow.call.result.admitted").payload["read_ledger_ref"]
        row = json.loads(hydrate_sidecar_ref(tmp_path, ledger).payload.splitlines()[0])
        (tmp_path / row["provider_observation_ref"]["ref"]).write_text('{"tampered":true}')
    if problem == "tampered_certificate":
        source = next(s for s in manifest["sources"] if s["artifact_id"] == "scoped_plan_review.json")
        (tmp_path / source["ref"]).write_text('{"tampered":true}')
    assert _scope_policy(tmp_path, events=events, manifest=manifest, required=required, explicit=explicit,
                              critic_role="planner" if problem == "same_current_role" else "critic") == required


def test_local_critic_policy_is_pinned_by_the_actual_dispatch_entry(tmp_path, monkeypatch):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime.artifact_read_ledger import active_read_ledger_path, read_attempt_artifact, render_attempt_source_briefing, seal_read_ledger
    from zf.runtime.artifact_read_ledger_validation import validate_required_reads
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task, TaskEvidence
    from zf.core.task.store import TaskStore
    from zf.runtime.call_result_runtime import admit_runtime_call_result, mark_call_operation_started, prepare_call_operation
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    runtime = _runtime(tmp_path)
    manifest, _, events = _policy_fixture(runtime.state_dir)
    for event in events:
        runtime.event_writer.append(event)
    store = TaskStore(runtime.state_dir / "kanban.json")
    store.add(Task(id="C", title="retained", status="done", evidence=TaskEvidence(commit="existing-evidence")))
    retained = store.get("C")
    payload = {"workflow_run_id": "run-1", "fanout_id": "new-fanout", "stage_id": "plan",
               "role_instance": "critic", "output_profile_id": "plan-synth", "input_refs": manifest["sources"],
               "canonical_success_event": "fanout.synth.completed", "canonical_failure_event": "fanout.synth.completed"}
    contract = next(s for s in manifest["sources"] if s["source_id"] == "plan-synth-contract")
    payload.update(plan_revision="new-rev", plan_synth_contract_ref=contract["ref"],
                   plan_synth_contract_digest=contract["sha256"], reviewed_plan_candidate_digest=contract["sha256"])
    prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_synth",
        operation_key="critic", stage_id="plan", task_id="", dispatch_id="new-attempt", causation_id="trigger")
    assert prepared.should_dispatch
    replay = prepare_call_operation(runtime, payload=deepcopy(payload), operation_type="fanout_synth",
        operation_key="critic", stage_id="plan", task_id="", dispatch_id="new-attempt", causation_id="trigger")
    assert replay.request_hash == prepared.request_hash
    policy = hydrate_sidecar_ref(runtime.state_dir, payload["input_consumption_policy_ref"]).payload
    selected = {(row["source_id"], row["json_path"]) for row in policy["required_reads"]}
    assert ("child-artifact-planner-2", "$") not in selected
    assert ("child-artifact-planner-2", "$.tasks[0]") in selected
    assert ("baseline-plan-port-task_map", "$.tasks[0]") in selected
    assert ("child-artifact-planner-4", "$") in selected
    assert ("requirement", "$") in selected
    assert "scope certificate is a claim, not approval" in render_attempt_source_briefing(payload)
    pinned_manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    assert not active_read_ledger_path(runtime.state_dir, "new-attempt").exists()
    mark_call_operation_started(runtime, prepared, task_id="", dispatch_id="new-attempt")
    result = ZfEvent(type="fanout.synth.completed", actor="critic", correlation_id="run-1", payload={
        **payload, "status": "completed", "recommendation": "approve", "summary": "local review passed",
        "report": {"status": "passed", "recommendation": "approve", "summary": "local review passed"}})
    from zf.runtime.plan_local_review_output import validate_local_review_output

    with pytest.raises(ValueError, match="result was not admitted"):
        validate_local_review_output(runtime, event=result, payload={})
    assert admit_runtime_call_result(runtime, result, mode="blocking", dispatch_correction=False).repair_requested
    for row in policy["required_reads"]:
        read_attempt_artifact(runtime.state_dir, manifest=pinned_manifest, source_id=row["source_id"],
                              artifact_id=row["artifact_id"], json_path=row["json_path"])
    ledger = seal_read_ledger(runtime.state_dir, "new-attempt")
    assert validate_required_reads(runtime.state_dir, policy=policy, ledger_descriptor=ledger) == []
    assert admit_runtime_call_result(runtime, result, mode="blocking", dispatch_correction=False).admitted
    assert store.get("C") == retained
    from zf.runtime import plan_artifact_package_runtime as package_runtime
    report_source = next(s for s in pinned_manifest["sources"] if s["source_id"] == "child-result-planner")
    report = hydrate_sidecar_ref(runtime.state_dir, report_source).payload
    output = {"workflow_run_id": "run-1", "flow_kind": "prd", "task_map_ref": report["task_map_ref"],
              "plan_ports": report["plan_ports"]}
    validate_local_review_output(runtime, event=result, payload=output)
    runtime.event_writer.append(ZfEvent(type="fanout.synth.dispatched", origin="kernel", payload={
        "fanout_id": "new-fanout", "operation_id": prepared.operation_id}))
    forged = deepcopy(result)
    forged.payload.pop("operation_id")
    with pytest.raises(ValueError, match="missing its dispatched operation"):
        validate_local_review_output(runtime, event=forged, payload=output)
    forged = deepcopy(result)
    forged.payload["fanout_id"] = "different-fanout"
    with pytest.raises(ValueError, match="does not match the dispatched fanout"):
        validate_local_review_output(runtime, event=forged, payload=output)
    calls = []
    monkeypatch.setattr(package_runtime, "_admit", lambda *_args, **_kwargs: calls.append(True) or {})
    monkeypatch.setattr(package_runtime, "flow_metadata_for", lambda *_args, **_kwargs: {})
    runtime._contract_failure_payload = lambda body, reason: {**body, "reason": reason}
    status, _, _ = package_runtime.admit_synthesized_plan_package(runtime, event=result,
        manifest={"workflow_run_id": "run-1"}, stage_id="plan", trace_id="run-1", success_event="task_map.ready",
        final_status="completed", recommendation="approve", artifact_payload=output)
    assert status == "completed" and calls == [True]
    old_map = next(s for s in pinned_manifest["sources"] if s["source_id"] == "baseline-plan-port-task_map")
    changed = {**output, "task_map_ref": old_map["ref"]}
    calls.clear()
    status, _, failure = package_runtime.admit_synthesized_plan_package(runtime, event=result,
        manifest={"workflow_run_id": "run-1"}, stage_id="plan", trace_id="run-1", success_event="task_map.ready",
        final_status="completed", recommendation="approve", artifact_payload=changed)
    assert status == "failed" and not calls and "reviewed Task Map" in failure["reason"]
    old_port = next(s for s in pinned_manifest["sources"] if s["source_id"] == "baseline-plan-port-execution_spec")
    changed_ports = deepcopy(output["plan_ports"])
    changed_ports[0].update(ref=old_port["ref"], sha256=old_port["sha256"])
    with pytest.raises(ValueError, match="reviewed Task Map"):
        validate_local_review_output(runtime, event=result, payload={**output, "plan_ports": changed_ports})
    with pytest.raises(ValueError, match="reviewed Plan text"):
        validate_local_review_output(runtime, event=result, payload={**output, "plan_md": "unreviewed change"})
    runtime.event_writer.append(ZfEvent(type="plan.artifact_package.admitted", origin="kernel", payload={
        "workflow_run_id": "run-1", "package_slot": "execution_plan", "package_digest": "b" * 64,
        "plan_revision": "newer-rev", "task_map_generation": "newer-gen", "status": "admitted"}))
    with pytest.raises(ValueError, match="baseline or requirement authority changed"):
        validate_local_review_output(runtime, event=result, payload=output)


def test_scoped_review_survives_real_semantic_submit_and_package_gate(tmp_path, monkeypatch):
    from tests.test_call_result_runtime import _runtime
    from zf.runtime import plan_artifact_package_runtime as package_runtime
    from zf.runtime.artifact_read_ledger import read_attempt_artifact
    from zf.runtime.call_result_runtime import mark_call_operation_started, prepare_call_operation
    from zf.runtime.result_submit import SemanticResultSubmitService, provision_role_submit_credential
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    runtime = _runtime(tmp_path)
    manifest, _, events = _policy_fixture(runtime.state_dir)
    for event in events:
        runtime.event_writer.append(event)
    token = provision_role_submit_credential(runtime.state_dir, "critic").read_text().strip()
    contract = next(s for s in manifest["sources"] if s["source_id"] == "plan-synth-contract")
    payload = {"workflow_run_id": "run-1", "fanout_id": "new-fanout", "stage_id": "plan",
        "role_instance": "critic", "output_profile_id": "plan-synth", "input_refs": manifest["sources"],
        "canonical_success_event": "fanout.synth.completed", "canonical_failure_event": "fanout.synth.completed",
        "plan_revision": "new-rev", "plan_synth_contract_ref": contract["ref"],
        "plan_synth_contract_digest": contract["sha256"], "reviewed_plan_candidate_digest": contract["sha256"]}
    prepared = prepare_call_operation(runtime, payload=payload, operation_type="fanout_synth", operation_key="critic",
        stage_id="plan", task_id="", dispatch_id="new-attempt", causation_id="trigger")
    mark_call_operation_started(runtime, prepared, task_id="", dispatch_id="new-attempt")
    source_manifest = hydrate_sidecar_ref(runtime.state_dir, payload["attempt_source_manifest"]).payload
    for row in payload["required_reads"]:
        read_attempt_artifact(runtime.state_dir, manifest=source_manifest, source_id=row["source_id"],
                              artifact_id=row["artifact_id"], json_path=row["json_path"])
    service = SemanticResultSubmitService(state_dir=runtime.state_dir, event_log=runtime.event_log,
                                          event_writer=runtime.event_writer)
    submitted = service.submit(operation_id=prepared.operation_id, role_instance="critic", credential=token,
        semantic_result={"status": "completed", "recommendation": "approve", "summary": "Reviewed",
                         "report": {"status": "passed", "recommendation": "approve", "summary": "Reviewed"}})
    result = next(event for event in runtime.event_log.read_all() if event.id == submitted.canonical_event_id)
    assert "attempt_source_manifest" not in result.payload
    report_source = next(s for s in source_manifest["sources"] if s["source_id"] == "child-result-planner")
    report = hydrate_sidecar_ref(runtime.state_dir, report_source).payload
    output = {"workflow_run_id": "run-1", "flow_kind": "prd", "task_map_ref": report["task_map_ref"],
              "plan_ports": report["plan_ports"]}
    calls = []
    monkeypatch.setattr(package_runtime, "_admit", lambda *_args, **_kwargs: calls.append(True) or {})
    monkeypatch.setattr(package_runtime, "flow_metadata_for", lambda *_args, **_kwargs: {})
    status, _, _ = package_runtime.admit_synthesized_plan_package(runtime, event=result,
        manifest={"workflow_run_id": "run-1"}, stage_id="plan", trace_id="run-1", success_event="task_map.ready",
        final_status="completed", recommendation="approve", artifact_payload=output)
    assert status == "completed" and calls == [True]
