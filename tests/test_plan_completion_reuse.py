"""Plan-only recovery preserves native completion without inventing a pass."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
from zf.runtime.run_contract import write_run_contract_snapshot
from zf.runtime.task_contract_snapshot import (current_task_contract_identity, write_task_contract_snapshot,
                                              write_target_snapshot, build_target_snapshot)
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    runtime.config.runtime = SimpleNamespace(git=SimpleNamespace(task_ref_prefix="task"))
    seeded = _seed_frozen_candidate(runtime)
    run = seeded["run_id"]
    state = runtime.state_dir

    def emit(kind, payload, task_id=None):
        return runtime.event_writer.append(ZfEvent(type=kind, origin="kernel", task_id=task_id,
            correlation_id=run, payload={"workflow_run_id": run, **payload}))

    def sidecar(body):
        return write_immutable_json_sidecar(state, body, root="reuse-test", kind="test",
            schema_version=body.get("schema_version", "test.v1"), created_by="test")

    task = Task(id="TASK-1", title="child", contract_authority_revision="old-authority", contract=TaskContract(evidence_contract={
        "workflow_run_id": run, "source_refs": {"task_map_generation": "generation-1"}}))
    runtime.task_store.add(task)
    runtime.task_store.update(task.id, status="done", completed_at="2026-11-20T00:00:00+00:00")
    task = runtime.task_store.get(task.id)
    events = runtime.event_log.read_all()
    old_verify = next(e for e in events if e.type == "task.pipeline.verify.completed")
    contract = json.loads((state / old_verify.payload["contract_snapshot_ref"]).read_text())
    contract.update(current_task_contract_identity(task))
    contract["task_map_generation"] = "generation-1"
    contract_ref = write_task_contract_snapshot(state, contract)
    target_ref = write_target_snapshot(state, build_target_snapshot(contract_ref,
        target_commit="b" * 40, contract_snapshot=contract))
    result = sidecar({"schema_version": "verification-result.v1", "verdict": "passed"})
    envelope = sidecar({"schema_version": "call-result-envelope.v1"})
    identity = {"operation_id": "verify-old", "request_hash": "old-hash", "control_result_ref": result}
    admission = emit("workflow.call.result.admitted", {**identity, "semantic_verdict": "passed"})
    verify = emit("task.pipeline.verify.completed", {**old_verify.payload, **identity,
        "contract_snapshot_ref": contract_ref["ref"], "contract_snapshot_digest": contract_ref["sha256"],
        "target_snapshot_ref": target_ref["ref"], "target_snapshot_digest": target_ref["sha256"],
        "contract_authority_revision": task.contract_authority_revision,
        "contract_revision": contract["contract_revision"],
        "semantic_submit_admission_event_id": admission.id, "call_result_envelope_ref": envelope}, task.id)
    receipt = sidecar({"schema_version": "task-integration-receipt.v1", "task_id": task.id,
        "workflow_run_id": run, "task_map_generation": "generation-1", "status": "integrated",
        "new_candidate_head": "c" * 40, "task_ref": "task/TASK-1", "task_commit": "b" * 40,
        "contract_revision": contract["contract_revision"]})
    (state / "refs").mkdir(exist_ok=True)
    (state / "refs/task-index.json").write_text(json.dumps({"TASK-1": {
        "task_ref": "task/TASK-1", "source_commit": "b" * 40}}))
    done = emit("task.done", {"task_map_generation": "generation-1", "candidate_head": "c" * 40,
                              "integration_receipt_ref": receipt}, task.id)
    old_gen = emit("task.pipeline.generation.admitted", {"schema_version": "task-pipeline-generation.v1",
        "generation_id": "old", "task_map_generation": "generation-1", "task_ids": [task.id],
        "dispatch_base_commit": "a" * 40})
    frozen = next(e for e in events if e.type == "candidate.ready")
    candidate = emit("candidate.ready", {**frozen.payload, "pdd_id": "FLOW-ANCHOR"})
    task_map = {"tasks": [{"task_id": task.id}], "target_commit": "c" * 40}
    map_ref = sidecar(task_map)
    package = build_plan_artifact_package(workflow_run_id=run, flow_kind="issue", producer_stage_id="plan",
        run_contract=write_run_contract_snapshot(state, {"schema_version": "run-contract.v1"}),
        plan_revision="generation-2", task_map_generation="generation-2",
        produced=[{**map_ref, "logical_name": "task_map"}], required_ports=["task_map"])
    pkg_ref = write_plan_artifact_package(state, package)
    emit("plan.artifact_package.admitted", {"package_ref": pkg_ref["ref"], "package_digest": pkg_ref["sha256"]})
    bindings = {"task_map_ref": map_ref["ref"], "task_map_generation": "generation-2",
                "plan_artifact_package_ref": pkg_ref["ref"], "plan_artifact_package_digest": pkg_ref["sha256"]}
    source = emit("task_map.ready", bindings)
    emit("plan.approved", {"plan_id": source.id})
    new_gen = emit("task.pipeline.generation.admitted", {**bindings,
        "schema_version": "task-pipeline-generation.v1", "generation_id": "new",
        "trigger_event_id": source.id, "task_ids": [task.id], "dispatch_base_commit": "c" * 40})
    new = deepcopy(task)
    new.status = "backlog"
    new.completed_at = None
    new.contract_authority_revision = "new-authority"
    new.contract.evidence_contract["source_refs"] = bindings
    runtime.task_store.reopen(new)
    service = ControlledActionService(state, runtime.event_writer, config=runtime.config, project_root=runtime.project_root)
    # Isolated unit tier: Git resolution is a host boundary; evidence/admission/CAS are real.
    try:
        from zf.runtime import plan_completion_reuse
        monkeypatch.setattr(plan_completion_reuse, "_git_commit", lambda *_: "c" * 40)
        from zf.runtime import plan_completion_refs
        monkeypatch.setattr(plan_completion_refs, "_git_commit", lambda *_: "b" * 40)
    except ImportError:
        pass  # The same behavioral regression also runs against the old deployment.
    return SimpleNamespace(runtime=runtime, service=service, source=source, candidate=candidate,
        old_gen=old_gen, new_gen=new_gen, verify=verify, done=done, emit=emit, sidecar=sidecar)


def action(r, **extra):
    return r.service.execute(action="stage-retrigger", requested_action="stage-retrigger",
        requested=ZfEvent(type="controlled.action.requested", actor="operator"),
        payload={"source_event_id": r.source.id, "reuse_completed_plan": True, **extra})


def reviewed(r):
    result = action(r, dry_run=True)
    assert result["ok"] and "manifest" in result, result
    return result["manifest"]["manifest_digest"]


def test_reviewed_recovery_keeps_original_verify_and_freezes_new_plan(recovery):
    r = recovery
    count = len(r.runtime.event_log.read_all())
    digest = reviewed(r)
    assert not any(e.type in {"task.status_changed", "task_map.ready"}
                   for e in r.runtime.event_log.read_all()[count:])
    assert r.runtime.task_store.get("TASK-1").status == "backlog"
    result = action(r, reviewed_digest=digest, approval_ref="owner:exact-preview",
                    reuse_rationale="Only navigation changed; acceptance and target remain valid.")
    assert result["ok"], result
    assert r.runtime.task_store.get("TASK-1").status == "done"
    events = r.runtime.event_log.read_all()
    assert len([e for e in events if e.type == "task.done"]) == 1
    assert len([e for e in events if e.type == "task.pipeline.verify.completed"]) == 2  # fixture + admitted source
    context = task_pipeline_generation_contexts(events)["TASK-1"]
    assert context["generation_id"] == "old"
    from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
    next_candidate = r.emit("candidate.ready", {**r.candidate.payload, **r.source.payload})
    fields = prepare_candidate_verification_authority(r.runtime, payload={}, workflow_run_id=r.source.correlation_id,
                                                      task_id="FLOW-ANCHOR")
    contract = json.loads((r.runtime.state_dir / fields["contract_snapshot_ref"]).read_text())
    assert fields["task_map_generation"] == "generation-2"
    assert contract["child_authorities"][0]["task_map_generation"] == "generation-1"
    assert contract["child_authorities"][0]["completion_reuse_ref"]
    assert not action(r, reviewed_digest=digest, approval_ref="owner", reuse_rationale="same")["ok"]


@pytest.mark.parametrize("fault", ["digest", "approval", "rationale", "live_task", "acceptance", "rejected_plan", "native_result", "moved_target", "task_ref"])
def test_reuse_fail_closed_before_task_mutation(recovery, monkeypatch, fault):
    r = recovery
    args = dict(reviewed_digest=reviewed(r), approval_ref="owner", reuse_rationale="same semantics")
    if fault == "digest": args["reviewed_digest"] = "stale"
    elif fault == "approval": args["approval_ref"] = ""
    elif fault == "rationale": args["reuse_rationale"] = ""
    elif fault == "live_task": r.runtime.task_store.update("TASK-1", status="in_progress")
    elif fault == "acceptance":
        task = r.runtime.task_store.get("TASK-1")
        task.contract.verification = "different command"
        r.runtime.task_store.reopen(task)
    elif fault == "rejected_plan": r.emit("plan.rejected", {"plan_id": r.source.id})
    elif fault == "native_result":
        (r.runtime.state_dir / r.verify.payload["control_result_ref"]["ref"]).write_text('{}')
    elif fault == "moved_target":
        from zf.runtime import plan_completion_reuse
        monkeypatch.setattr(plan_completion_reuse, "_git_commit", lambda *_: "d" * 40)
    elif fault == "task_ref":
        (r.runtime.state_dir / "refs/task-index.json").write_text(json.dumps({"TASK-1": {
            "task_ref": "task/TASK-1", "source_commit": "d" * 40}}))
    result = action(r, **args)
    assert not result["ok"], result
    assert r.runtime.task_store.get("TASK-1").status != "done"
    assert not any(e.type == "task.status_changed" for e in r.runtime.event_log.read_all())


def test_crash_after_cas_is_retryable_without_new_worker_pass(recovery, monkeypatch):
    r = recovery
    digest = reviewed(r)
    append = r.service.writer.append

    def crash(event):
        if event.type == "task.status_changed": raise OSError("after CAS")
        return append(event)

    monkeypatch.setattr(r.service.writer, "append", crash)
    args = dict(reviewed_digest=digest, approval_ref="owner", reuse_rationale="reviewed")
    assert not action(r, **args)["ok"]
    monkeypatch.setattr(r.service.writer, "append", append)
    result = action(r, **args)
    assert result["ok"], result
    assert len([e for e in r.runtime.event_log.read_all() if e.type == "task.done"]) == 1


@pytest.mark.parametrize("fault", ["authority", "head", "receipt"])
def test_candidate_rechecks_reuse_bindings(recovery, fault):
    r = recovery
    assert action(r, reviewed_digest=reviewed(r), approval_ref="owner", reuse_rationale="reviewed")["ok"]
    body = {**r.candidate.payload, **r.source.payload}
    if fault == "head": body["candidate_head_commit"] = "d" * 40
    if fault == "authority":
        task = r.runtime.task_store.get("TASK-1")
        task.contract_authority_revision = "unexpected"
        task.status = "backlog"
        r.runtime.task_store.reopen(task)
    if fault == "receipt":
        context = task_pipeline_generation_contexts(r.runtime.event_log.read_all())["TASK-1"]
        (r.runtime.state_dir / context["completion_reuse_ref"]["ref"]).write_text('{}')
    r.emit("candidate.ready", body)
    from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
    with pytest.raises(ValueError):
        prepare_candidate_verification_authority(r.runtime, payload={}, workflow_run_id=r.source.correlation_id,
                                                 task_id="FLOW-ANCHOR")


def test_later_admitted_generation_supersedes_reuse_context(recovery):
    r = recovery
    assert action(r, reviewed_digest=reviewed(r), approval_ref="owner", reuse_rationale="reviewed")["ok"]
    r.emit("task.pipeline.generation.admitted", {**r.new_gen.payload, "generation_id": "third",
                                                 "task_map_generation": "generation-3"})
    assert task_pipeline_generation_contexts(r.runtime.event_log.read_all())["TASK-1"]["generation_id"] == "third"


def test_already_applied_reuse_can_finish_ref_repair_without_another_trigger(recovery):
    r = recovery
    args = dict(reviewed_digest=reviewed(r), approval_ref="owner", reuse_rationale="reviewed")
    first = action(r, **args)
    assert first["ok"], first
    assert reviewed(r) == args["reviewed_digest"]
    result = action(r, **args, repair_task_refs=True)
    assert result["ok"], result
    assert result["retriggered_event_id"] == first["retriggered_event_id"]
    assert len([e for e in r.runtime.event_log.read_all() if e.type == "task_map.ready"]) == 2


@pytest.mark.parametrize("fault", ["running", "restarted", "dirty", "stopped"])
def test_redispatched_workers_need_stopped_clean_workspaces(recovery, monkeypatch, fault):
    r = recovery
    from zf.runtime import plan_completion_reuse
    from zf.runtime.workflow_operation import reduce_workflow_operations

    identity = {"operation_id": "new-impl", "request_hash": "new-hash", "task_id": "TASK-1",
                "task_map_generation": "generation-2", "task_pipeline_stage": "impl"}
    r.emit("workflow.operation.requested", identity, "TASK-1")
    r.emit("workflow.operation.started", identity, "TASK-1")
    r.emit("task.pipeline.stage.dispatched", {**identity, "workdir": str(r.runtime.project_root)}, "TASK-1")
    if fault != "running": r.emit("workflow.operation.interrupted", identity, "TASK-1")
    r.emit("loop.stopped", {})
    if fault == "restarted": r.emit("workflow.operation.started", {**identity, "redrive_count": 1}, "TASK-1")
    monkeypatch.setattr(plan_completion_reuse.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=" M file" if fault == "dirty" else ""))
    result = action(r, dry_run=True)
    if fault != "stopped":
        assert not result["ok"], result
        assert r.runtime.task_store.get("TASK-1").status == "backlog"
        return
    assert result["ok"], result
    assert action(r, reviewed_digest=result["manifest"]["manifest_digest"], approval_ref="owner", reuse_rationale="same")["ok"]
    assert reduce_workflow_operations(r.runtime.event_log.read_all())["new-impl"]["status"] == "cancelled"
