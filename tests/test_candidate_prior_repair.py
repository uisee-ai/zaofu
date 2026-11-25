from dataclasses import replace

import pytest

from tests.test_candidate_repair_feedback import repair_call
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_prior_repair import aggregate_prior_repair_feedback
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash
from zf.runtime.workflow_operation_task_pipeline import task_pipeline_request_hash_body


def _sidecar(runtime, body):
    return write_immutable_json_sidecar(runtime.state_dir, body, root="fixtures/aggregate",
        kind="fixture", schema_version="fixture.v1", created_by="test")


@pytest.mark.parametrize("fault", ["", "other_run", "no_replacement", "other_finding", "private_feedback",
                                   "wrong_hash", "wrong_anchor", "wrong_decision", "damaged", "legacy", "shadow"])
def test_only_the_exact_retained_product_finding_gets_the_original_authorized_feedback(repair_call, fault):
    runtime, ctx, contract, call = repair_call
    events = runtime.event_log.read_all()
    run, task = ctx["workflow_run_id"], contract["task_id"]
    ready = next(e for e in events if e.id == ctx["trigger_event_id"])
    current = ready.payload["rework_summary"]["candidate_obligations"]
    feedback = call("verify")["candidate_repair_feedback_ref"]
    body = hydrate_sidecar_ref(runtime.state_dir, feedback).payload
    parent = body["predecessor_task_id"]
    admission = next(e for e in events if e.id == feedback["source_event_id"])
    if fault == "shadow":
        events = [replace(e, payload={**e.payload, "checkpoint_policy": "shadow"}) if e == admission else e for e in events]
    if fault == "other_finding":
        current = [{**r, "finding_id": r["finding_id"] + "-different"} for r in current]
    if fault == "private_feedback":
        feedback = {**feedback, "access_scope": {"roles": ["impl"]}}
    if fault == "damaged":
        (runtime.state_dir / feedback["ref"]).write_text("{}")
    if fault == "wrong_decision":
        injected = _sidecar(runtime, {**body, "required_actions": ["unapproved action"]})
        feedback = {**feedback, "ref": injected["ref"], "sha256": injected["sha256"]}
    generation = ZfEvent(type="task.pipeline.generation.admitted", origin="kernel", payload={
        "pdd_id": "TASK-1", "trigger_event_id": ready.id, "plan_artifact_package_digest": "pkg",
        "task_map_generation": ctx["task_map_generation"], "workflow_run_id": run})
    req = {"task_pipeline_stage": "verify"}
    if fault != "legacy":
        req["candidate_repair_feedback_ref"] = feedback
    envelope = {"operation_id": "new-task-verify", "workflow_run_id": run, "task_id": task, "request": req}
    request_hash = operation_request_hash(task_pipeline_request_hash_body(envelope, req))
    request = ZfEvent(type="workflow.operation.requested", origin="kernel", payload={
        "workflow_run_id": run, "operation_id": envelope["operation_id"], "request_hash": request_hash,
        "request_ref": _sidecar(runtime, envelope)})
    verify = ZfEvent(type="task.pipeline.verify.completed", origin="", payload={
        **generation.payload, "task_id": task, "output_profile_id": "task-verify", "operation_id": envelope["operation_id"],
        "parent_task_id": "foreign" if fault == "wrong_anchor" else "TASK-1",
        "request_hash": "other" if fault == "wrong_hash" else request_hash})
    replacements = {parent: [{"generation_event_id": generation.id, "package_digest": "pkg",
        "edges": {parent: [task]}, "verification_event_ids": {task: verify.id}}]}
    freeze = ZfEvent(type="candidate.ready", origin="kernel", payload={"pdd_id": "TASK-1",
        "completed_task_replacements": {} if fault == "no_replacement" else replacements})
    contract_ref = _sidecar(runtime, {"candidate_recovery_obligations": current})
    result = _sidecar(runtime, {"contract_snapshot_ref": contract_ref["ref"], "contract_snapshot_digest": contract_ref["sha256"]})
    events += [generation, request, verify, freeze]
    refs, index = aggregate_prior_repair_feedback(runtime, events, "foreign" if fault == "other_run" else run,
        {"candidate_event_id": freeze.id, "members": [{"control_result_ref": result}, {"control_result_ref": result}]})
    if fault:
        assert not refs and not any(row["status"] == "available" for row in index)
        return
    ref, = refs
    entry, = index
    assert ref["ref"] == feedback["ref"] and ref["sha256"] == feedback["sha256"]
    assert entry["source_id"] == ref["source_id"] and entry["decision_admission_event_id"] == admission.id
    assert entry["finding_ids"] == [row["finding_id"] for row in body["findings"]]
    assert hydrate_sidecar_ref(runtime.state_dir, ref).payload["rationale"] == body["rationale"]


def test_aggregate_checkpoint_lists_feedback_for_lazy_read_without_changing_a_prepared_request(tmp_path, monkeypatch):
    from tests.test_candidate_aggregate_review import _mixed
    from zf.runtime import candidate_prior_repair
    from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
    from zf.runtime.run_manager import run_manager_tick

    runtime, _, _ = _mixed(tmp_path)
    run_manager_tick(state_dir=runtime.state_dir, writer=runtime.event_writer, config=runtime.config,
                     project_root=runtime.project_root, event_log=runtime.event_log, spawn_repairs=False)
    event, = [e for e in runtime.event_log.read_all() if e.type == "orchestrator.semantic.failure.requested"]
    source = {**_sidecar(runtime, {"rationale": "original scoped instructions"}), "source_id": "candidate-prior-repair-fixture"}
    expected = [{"source_id": source["source_id"], "status": "available", "finding_ids": ["retained"]}]
    monkeypatch.setattr(candidate_prior_repair, "aggregate_prior_repair_feedback", lambda *_: ([source], expected))
    request = request_semantic_failure_checkpoint(runtime, event)
    sources = request.context.input_body["sources"]
    assert any(s["source_id"] == source["source_id"] and s["sha256"] == source["sha256"] for s in sources)
    failure = next(s for s in sources if s["source_id"] == "semantic-failure-input")
    assert hydrate_sidecar_ref(runtime.state_dir, failure).payload["candidate_verify"]["prior_repair_feedback"] == expected
    policy = hydrate_sidecar_ref(runtime.state_dir, request.context.read_policy_ref).payload
    assert source["source_id"] not in {r["source_id"] for r in policy["required_reads"]}
    monkeypatch.setattr(candidate_prior_repair, "aggregate_prior_repair_feedback", lambda *_: ([], []))
    replay = request_semantic_failure_checkpoint(runtime, event)
    assert replay.replay_hit and replay.request_hash == request.request_hash
