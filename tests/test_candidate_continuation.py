"""A successor reuses evidence, never resurrects or aliases a terminal Run."""
from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.control_actions import ControlledActionService


@pytest.fixture
def source(tmp_path, monkeypatch):
    from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
    from tests.test_run_admission import _config
    from zf.core.task.schema import Task
    from zf.runtime.call_result_envelope import write_immutable_json_sidecar
    from zf.runtime.goal_claim_set import build_goal_claim_set
    from zf.runtime.plan_artifact_package import build_plan_artifact_package, write_plan_artifact_package
    from zf.runtime.run_contract import write_run_contract_snapshot
    from zf.runtime import candidate_continuation

    runtime = _runtime(tmp_path)
    runtime.config = _config()
    _seed_frozen_candidate(runtime)
    run_id = "run-candidate-authority"
    events = runtime.event_log.read_all()
    frozen = next(e for e in events if e.type == "candidate.ready")
    def sidecar(body):
        return write_immutable_json_sidecar(runtime.state_dir, body, root="continuation-test",
            kind="test", schema_version=body.get("schema_version", "test.v1"), created_by="test")
    def emit(kind, body, **kwargs):
        return runtime.event_writer.append(ZfEvent(type=kind, origin="kernel", actor="test",
            correlation_id=run_id, payload={"workflow_run_id": run_id, **body}, **kwargs))
    runtime.task_store.add(Task(id="TASK-1", title="child", status="done"))
    task_map = {"goal_claims": [{"goal_claim_id": "AC-1", "text": "pulse is filterable", "mandatory": True}]}
    claim_set = build_goal_claim_set(task_map, workflow_run_id=run_id, goal_id=run_id,
                                    task_map_generation="generation-1")
    claims = sidecar(claim_set)
    contract = write_run_contract_snapshot(runtime.state_dir, {"schema_version": "run-contract.v1"})
    from tests.test_product_acceptance import _spec
    spec = {**_spec(), "workflow_run_id": run_id, "flow_kind": "issue",
            "plan_revision": "generation-1", "task_map_generation": "generation-1"}
    package = build_plan_artifact_package(workflow_run_id=run_id, flow_kind="issue", producer_stage_id="plan",
        run_contract=contract, plan_revision="generation-1", task_map_generation="generation-1",
        produced=[{**sidecar(task_map), "logical_name": "task_map"}, {**claims, "logical_name": "goal_claim_set"},
                  {**sidecar(spec), "logical_name": "product_acceptance_spec"}],
        required_ports=["task_map", "goal_claim_set", "product_acceptance_spec"])
    pkg = write_plan_artifact_package(runtime.state_dir, package)
    emit("workflow.invoke.requested", {"task_id": "FLOW-ANCHOR", "pattern_id": "plan", "flow_kind": "issue"},
         task_id="FLOW-ANCHOR")
    emit("run.goal.started", {"objective": "pulse is filterable", "task_id": "FLOW-ANCHOR"}, task_id="FLOW-ANCHOR")
    emit("goal.claim_set.pinned", {"goal_claim_set_ref": claims["ref"], "goal_claim_set_digest": claims["sha256"]})
    emit("plan.artifact_package.admitted", {"package_ref": pkg["ref"], "package_digest": pkg["sha256"],
                                           "task_map_generation": "generation-1"})
    emit("plan.approval.requested", {"plan_id": "plan-1", "plan_artifact_package_digest": pkg["sha256"]})
    emit("plan.approved", {"plan_id": "plan-1", "via": "controlled-action:test"})
    verify = next(e for e in events if e.type == "task.pipeline.verify.completed")
    result = sidecar({"schema_version": "verification-result.v1", "verdict": "passed"})
    envelope = sidecar({"schema_version": "call-result-envelope.v1", "identity": {"workflow_run_id": run_id}})
    identity = {"operation_id": "op-verify", "request_hash": "hash", "control_result_ref": result}
    admitted = emit("workflow.call.result.admitted", {**identity, "semantic_verdict": "passed"})
    emit("task.pipeline.verify.completed", {**verify.payload, **identity,
        "semantic_submit_admission_event_id": admitted.id, "call_result_envelope_ref": envelope}, task_id="TASK-1")
    emit("task.pipeline.generation.admitted", {"schema_version": "task-pipeline-generation.v1",
         "generation_id": "pipeline-1", "task_map_generation": "generation-1", "task_ids": ["TASK-1"]})
    body = {**frozen.payload, "pdd_id": "FLOW-ANCHOR", "flow_kind": "issue", "generation_id": "pipeline-1",
            "plan_artifact_package_ref": pkg["ref"], "plan_artifact_package_digest": pkg["sha256"],
            "plan_artifact_package_id": pkg["package_id"]}
    body.pop("freeze_receipt_ref")
    body.pop("freeze_receipt_digest")
    freeze = sidecar(body)
    candidate = emit("candidate.ready", {**body, "freeze_receipt_ref": freeze,
                                        "freeze_receipt_digest": freeze["sha256"]})
    emit("run.cancelled", {"run_id": run_id})
    monkeypatch.setattr(candidate_continuation, "_git_commit",
        lambda root, ref: "c" * 40 if ref.startswith("refs/") else ref)
    service = ControlledActionService(runtime.state_dir, runtime.event_writer,
                                     config=runtime.config, project_root=runtime.project_root)
    return runtime, service, run_id, candidate


def _action(service, run_id, candidate, **extra):
    return service.execute(action="candidate-continue", requested_action="candidate-continue",
        requested=ZfEvent(type="runtime.action.requested", actor="operator"),
        payload={"source_run_id": run_id, "candidate_event_id": candidate.id, **extra})


def test_preview_binds_facts_without_reopening_or_dispatching(source):
    from zf.runtime.run_admission import build_run_admission_projection
    runtime, service, run_id, candidate = source
    result = _action(service, run_id, candidate, dry_run=True)
    assert result["ok"], result
    manifest = result["manifest"]
    assert len(manifest["task_proofs"]) == 1
    assert manifest["candidate_reader_stage_ids"] == ["review"]
    assert _action(service, run_id, candidate, dry_run=True)["manifest"] == manifest
    assert build_run_admission_projection(runtime.event_log.read_all()).runs[run_id].status == "cancelled"
    assert len([e for e in runtime.event_log.read_all() if e.type == "workflow.invoke.requested"]) == 1


@pytest.mark.parametrize("fault", ["unfinished", "wrong_digest", "no_approval", "moved_target", "tampered_plan"])
def test_continuation_apply_fails_closed(source, monkeypatch, fault):
    from zf.runtime import candidate_continuation
    runtime, service, run_id, candidate = source
    preview = _action(service, run_id, candidate, dry_run=True)
    assert preview["ok"], preview
    manifest = preview["manifest"]
    extra = {"reviewed_digest": manifest["manifest_digest"], "approval_ref": "owner:continue"}
    if fault == "unfinished":
        runtime.task_store.update("TASK-1", status="in_progress")
    elif fault == "wrong_digest":
        extra["reviewed_digest"] = "wrong"
    elif fault == "no_approval":
        extra.pop("approval_ref")
    elif fault == "moved_target":
        monkeypatch.setattr(candidate_continuation, "_git_commit", lambda *a: "d" * 40)
    else:
        (runtime.state_dir / manifest["plan_package_ref"]["ref"]).write_text('{}')
    result = _action(service, run_id, candidate, **extra)
    assert result["ok"] is False
    assert len([e for e in runtime.event_log.read_all() if e.type == "workflow.invoke.requested"]) == 1


def test_successor_runtime_reuses_old_task_authority_and_replays_without_duplicate_entry(source):
    from tests.test_run_admission import _Transport
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.candidate_verification_authority import prepare_candidate_verification_authority
    from zf.runtime.run_admission import build_run_admission_projection
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    seeded, service, run_id, candidate = source
    task_before = seeded.task_store.get("TASK-1")
    result = _action(service, run_id, candidate, dry_run=True)
    assert result["ok"], result
    args = {"reviewed_digest": result["manifest"]["manifest_digest"], "approval_ref": "owner:continue"}
    accepted = _action(service, run_id, candidate, **args)
    assert accepted["ok"], accepted
    assert _action(service, run_id, candidate, **args)["status"] == "already_requested"
    successor = accepted["workflow_run_id"]
    invoke = next(e for e in seeded.event_log.read_all() if e.id == accepted["event_id"])
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    decision = runtime._on_workflow_invoke_requested(invoke)
    assert decision.action == "workflow_invoke", decision
    runtime._on_workflow_invoke_requested(invoke)
    events = runtime.event_log.read_all()
    assert build_run_admission_projection(events).runs[run_id].status == "cancelled"
    candidates = [e for e in events if e.type == "candidate.ready" and e.payload.get("workflow_run_id") == successor]
    assert len(candidates) == 1
    assert seeded.task_store.get("TASK-1") == task_before
    assert not any(e.type in {"task_map.ready", "task.pipeline.generation.admitted", "plan.approved"}
                   and e.payload.get("workflow_run_id") == successor for e in events)
    authority = prepare_candidate_verification_authority(runtime,
        payload={"candidate_event_id": candidates[0].id}, workflow_run_id=successor, task_id=invoke.task_id)
    contract = hydrate_sidecar_ref(runtime.state_dir, {"ref": authority["contract_snapshot_ref"],
                                   "sha256": authority["contract_snapshot_digest"]}).payload
    assert contract["workflow_run_id"] == successor
    assert contract["child_authorities"][0]["workflow_run_id"] == run_id
    from zf.runtime.artifact_query.handoff import CanonicalHandoffResolver, build_handoff_authority_contract
    handoff_payload = {**authority, "output_profile_id": "candidate-verify", "artifact_package_mode": "blocking"}
    handoff_payload["handoff_authority_contract"] = build_handoff_authority_contract(
        handoff_payload, output_profile_id="candidate-verify", stage_id="review", operation_type="read")
    resolver = CanonicalHandoffResolver(state_dir=runtime.state_dir,
        project_root=runtime.project_root, config=runtime.config)
    handoff, _ = resolver.resolve_payload(payload=handoff_payload, workflow_run_id=successor,
        task_id=invoke.task_id, attempt_id="continued-reader", dispatch_id="continued-reader-dispatch")
    assert handoff["workflow_run_id"] == successor
    from zf.runtime.product_acceptance import product_acceptance_binding_from_package
    acceptance = product_acceptance_binding_from_package(runtime.state_dir, {
        "ref": candidates[0].payload["plan_artifact_package_ref"],
        "sha256": candidates[0].payload["plan_artifact_package_digest"],
    })
    assert acceptance["spec"]["workflow_run_id"] == successor
    original = product_acceptance_binding_from_package(runtime.state_dir, result["manifest"]["plan_package_ref"])
    assert acceptance["spec"]["user_journeys"] == original["spec"]["user_journeys"]
    assert runtime._maybe_start_reader_fanout(candidates[0])
    started = [e for e in runtime.event_log.read_all() if e.type == "fanout.started"
               and e.payload.get("workflow_run_id") == successor]
    assert len(started) == 1
    assert started[0].payload["stage_id"] == "review"
    from zf.runtime.artifact_read_ledger import ArtifactReadError
    continuation_ref = candidates[0].payload["candidate_continuation_ref"]
    (runtime.state_dir / continuation_ref["ref"]).write_text('{}')
    with pytest.raises(ArtifactReadError, match="invalid Candidate continuation authority"):
        resolver.resolve_payload(payload=handoff_payload, workflow_run_id=successor,
            task_id=invoke.task_id, attempt_id="forged-reader", dispatch_id="forged-reader-dispatch")


def test_web_continuation_requires_explicit_operator_token(tmp_path, monkeypatch):
    from tests.test_web_workflow_proposals import _client, _action_body
    client, _ = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("ZF_WEB_TRUSTED_SESSION", "1")
    route = "/api/projects/default/actions/candidate-continue"
    body = _action_body("continue-auth", {"source_run_id": "missing", "candidate_event_id": "missing", "dry_run": True})
    assert client.post(route, json=body).status_code == 403
    response = client.post(route, headers={"X-ZF-Web-Token": "test-token"}, json=body)
    assert response.status_code == 409, response.text
    assert "source Run" in response.json()["reason"]


def test_restart_after_acceptance_completes_entry_without_duplicate_goal(source, monkeypatch):
    from tests.test_run_admission import _Transport
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.run_admission import build_run_admission_projection
    seeded, service, run_id, candidate = source
    result = _action(service, run_id, candidate, dry_run=True)
    assert result["ok"], result
    accepted = _action(service, run_id, candidate, reviewed_digest=result["manifest"]["manifest_digest"],
                       approval_ref="owner:continue")
    invoke = next(e for e in seeded.event_log.read_all() if e.id == accepted["event_id"])
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    append = runtime.event_writer.append
    def crash(event):
        if event.type == "candidate.ready":
            raise RuntimeError("controller crash")
        return append(event)
    monkeypatch.setattr(runtime.event_writer, "append", crash)
    with pytest.raises(RuntimeError, match="controller crash"):
        runtime._on_workflow_invoke_requested(invoke)
    restarted = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    from zf.runtime.orchestrator_periodic_sweep import run_replay_sweep
    run_replay_sweep(restarted, events=[])
    events = seeded.event_log.read_all()
    rows = [e for e in events if e.payload.get("workflow_run_id") == accepted["workflow_run_id"]]
    for kind in ("workflow.invoke.accepted", "run.goal.started", "plan.artifact_package.admitted", "candidate.ready"):
        assert len([e for e in rows if e.type == kind]) == 1
    assert build_run_admission_projection(events).runs[run_id].status == "cancelled"


def test_goal_provenance_preserves_source_approval_and_task_evidence(source):
    from tests.test_run_admission import _Transport
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.candidate_continuation import closure_provenance
    seeded, service, run_id, candidate = source
    result = _action(service, run_id, candidate, dry_run=True)
    assert result["ok"], result
    accepted = _action(service, run_id, candidate, reviewed_digest=result["manifest"]["manifest_digest"],
                       approval_ref="owner:continue")
    invoke = next(e for e in seeded.event_log.read_all() if e.id == accepted["event_id"])
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    runtime._on_workflow_invoke_requested(invoke)
    inherited = closure_provenance(seeded.event_log.read_all(), state_dir=seeded.state_dir,
                                  workflow_run_id=accepted["workflow_run_id"])
    assert inherited["source_run_id"] == run_id
    assert inherited["task_proofs"][0]["task_id"] == "TASK-1"
    decisions = [e for e in inherited["plan_lifecycle_evidence"] if e["event_type"] == "plan.approved"]
    assert len(decisions) == 1
    assert decisions[0]["payload"]["workflow_run_id"] == run_id


def test_changed_policy_between_preview_and_admission_rejects_successor(source):
    from dataclasses import replace
    from tests.test_run_admission import _Transport
    from zf.runtime.orchestrator import Orchestrator
    seeded, service, run_id, candidate = source
    result = _action(service, run_id, candidate, dry_run=True)
    accepted = _action(service, run_id, candidate, reviewed_digest=result["manifest"]["manifest_digest"],
                       approval_ref="owner:continue")
    invoke = next(e for e in seeded.event_log.read_all() if e.id == accepted["event_id"])
    seeded.config.workflow.run_limits = replace(seeded.config.workflow.run_limits, token_budget=1235)
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    decision = runtime._on_workflow_invoke_requested(invoke)
    assert decision.action == "block"
    assert "changed since approval" in decision.reason
    assert not any(e.type == "candidate.ready" and e.payload.get("workflow_run_id") == accepted["workflow_run_id"]
                   for e in seeded.event_log.read_all())


@pytest.mark.parametrize("terminal", ["run.paused", "run.cancelled"])
def test_idle_recovery_never_reopens_paused_or_cancelled_successor(source, terminal):
    from tests.test_run_admission import _Transport
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.orchestrator_periodic_sweep import run_replay_sweep
    seeded, service, run_id, candidate = source
    result = _action(service, run_id, candidate, dry_run=True)
    accepted = _action(service, run_id, candidate, reviewed_digest=result["manifest"]["manifest_digest"],
                       approval_ref="owner:continue")
    successor = accepted["workflow_run_id"]
    seeded.event_writer.append(ZfEvent(type=terminal, actor="operator",
        payload={"workflow_run_id": successor, "run_id": successor}, correlation_id=successor))
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    run_replay_sweep(runtime, events=[])
    assert not any(e.type == "candidate.ready" and e.payload.get("workflow_run_id") == successor
                   for e in seeded.event_log.read_all())


def test_eight_reader_continuation_dispatches_no_planner_or_impl(source):
    from dataclasses import replace
    from tests.test_run_admission import _Transport
    from zf.runtime.orchestrator import Orchestrator
    seeded, service, run_id, candidate = source
    roles = [replace(seeded.config.roles[0], name=f"verify-{i}", instance_id=f"verify-{i}") for i in range(8)]
    seeded.config.roles = roles
    seeded.config.workflow.stages[0].roles = [r.instance_id for r in roles]
    result = _action(service, run_id, candidate, dry_run=True)
    assert result["ok"], result
    accepted = _action(service, run_id, candidate, reviewed_digest=result["manifest"]["manifest_digest"],
                       approval_ref="owner:continue")
    invoke = next(e for e in seeded.event_log.read_all() if e.id == accepted["event_id"])
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    assert runtime._on_workflow_invoke_requested(invoke).action == "workflow_invoke"
    frozen = next(e for e in seeded.event_log.read_all() if e.type == "candidate.ready"
                  and e.payload.get("workflow_run_id") == accepted["workflow_run_id"])
    assert runtime._maybe_start_reader_fanout(frozen)
    dispatches = [e for e in seeded.event_log.read_all() if e.type == "fanout.child.dispatched"]
    assert {e.payload["role_instance"] for e in dispatches} == {r.instance_id for r in roles}
    assert len(dispatches) == 8
    assert not any(e.type == "task.pipeline.stage.dispatched" for e in seeded.event_log.read_all())


def test_successor_entry_passes_real_v5_blocking_event_schema(source):
    from tests.test_run_admission import _Transport
    from zf.core.config.loader import load_config
    from zf.core.events.writer import EventWriter
    from zf.core.verification.event_schema import EventSchemaRegistry
    from zf.runtime.orchestrator import Orchestrator
    seeded, service, run_id, candidate = source
    result = _action(service, run_id, candidate, dry_run=True)
    accepted = _action(service, run_id, candidate, reviewed_digest=result["manifest"]["manifest_digest"],
                       approval_ref="owner:continue")
    invoke = next(e for e in seeded.event_log.read_all() if e.id == accepted["event_id"])
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    production = load_config(Path(__file__).resolve().parents[1] / "zf-prd-v5.yaml")
    runtime.event_writer = EventWriter(seeded.event_log,
        schema_registry=EventSchemaRegistry.from_config(production), schema_mode="blocking")
    assert runtime._on_workflow_invoke_requested(invoke).action == "workflow_invoke"
    rows = [e for e in seeded.event_log.read_all() if e.payload.get("workflow_run_id") == accepted["workflow_run_id"]]
    assert not [e.payload.get("violations") for e in rows if e.type == "discriminator.failed"]
    assert {"goal.claim_set.pinned", "plan.artifact_package.admitted", "candidate.ready"} <= {e.type for e in rows}


@pytest.mark.parametrize("historical_partial", [False, True])
def test_replay_repairs_missing_lifecycle_and_never_publishes_after_gate_rejection(source, monkeypatch, historical_partial):
    from tests.test_run_admission import _Transport
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.orchestrator_periodic_sweep import run_replay_sweep
    seeded, service, run_id, candidate = source
    result = _action(service, run_id, candidate, dry_run=True)
    accepted = _action(service, run_id, candidate, reviewed_digest=result["manifest"]["manifest_digest"],
                       approval_ref="owner:continue")
    invoke = next(e for e in seeded.event_log.read_all() if e.id == accepted["event_id"])
    runtime = Orchestrator(seeded.state_dir, seeded.config, _Transport(), project_root=seeded.project_root)
    append = runtime.event_writer.append
    def drop_lifecycle(event):
        if event.type in {"goal.claim_set.pinned", "plan.artifact_package.admitted"}:
            # Historical fixture models the previous unchecked writer result;
            # normal rejection must stop before Candidate publication.
            return event if historical_partial else ZfEvent(type="discriminator.failed", actor="kernel")
        return append(event)
    monkeypatch.setattr(runtime.event_writer, "append", drop_lifecycle)
    if historical_partial:
        runtime._on_workflow_invoke_requested(invoke)
        frozen = next(e for e in seeded.event_log.read_all() if e.type == "candidate.ready"
                      and e.payload.get("workflow_run_id") == accepted["workflow_run_id"])
        assert runtime._maybe_start_reader_fanout(frozen) is False
        assert not any(e.type == "fanout.started" for e in seeded.event_log.read_all())
    else:
        with pytest.raises(ValueError, match="event admission blocked"):
            runtime._on_workflow_invoke_requested(invoke)
        assert not any(e.type == "candidate.ready" and e.payload.get("workflow_run_id") == accepted["workflow_run_id"]
                       for e in seeded.event_log.read_all())
    monkeypatch.setattr(runtime.event_writer, "append", append)
    if historical_partial:
        frozen = next(e for e in seeded.event_log.read_all() if e.type == "candidate.ready"
                      and e.payload.get("workflow_run_id") == accepted["workflow_run_id"])
        assert runtime._maybe_start_reader_fanout(frozen)
        before_idle = [e.type for e in seeded.event_log.read_all()
                       if e.payload.get("workflow_run_id") == accepted["workflow_run_id"]]
        assert before_idle.index("plan.artifact_package.admitted") < before_idle.index("fanout.started")
    run_replay_sweep(runtime, events=[])
    rows = [e for e in seeded.event_log.read_all() if e.payload.get("workflow_run_id") == accepted["workflow_run_id"]]
    for kind in ("goal.claim_set.pinned", "plan.artifact_package.admitted", "candidate.ready"):
        assert len([e for e in rows if e.type == kind]) == 1


def test_continuation_preview_rejects_missing_source_without_invoking(tmp_path: Path):
    from tests.test_candidate_verification_authority import _runtime

    runtime = _runtime(tmp_path)
    service = ControlledActionService(runtime.state_dir, runtime.event_writer,
                                     config=runtime.config, project_root=runtime.project_root)
    result = service.execute(action="candidate-continue", requested_action="candidate-continue",
        requested=ZfEvent(type="runtime.action.requested", actor="operator"),
        payload={"source_run_id": "missing", "candidate_event_id": "missing", "dry_run": True})
    assert result["ok"] is False
    assert "source Run" in result["reason"]
    assert not any(e.type == "workflow.invoke.requested" for e in runtime.event_log.read_all())


def test_continuation_cannot_be_claimed_by_a_bare_invoke(tmp_path: Path):
    from tests.test_run_admission import _runtime

    _, _, runtime = _runtime(tmp_path)
    event = ZfEvent(type="workflow.invoke.requested", actor="worker", payload={
        "workflow_run_id": "forged", "candidate_continuation_ref": {
            "ref": "artifacts/missing.json", "sha256": "a" * 64,
        },
    })
    decision = runtime._on_workflow_invoke_requested(event)
    assert decision.action == "block"
    assert "continuation" in decision.reason
    assert not any(e.type == "candidate.ready" for e in runtime.event_log.read_all())
