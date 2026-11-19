"""Admit a reviewed successor directly at the existing Candidate reader entry."""
from __future__ import annotations

from types import SimpleNamespace

from zf.core.events import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_continuation import authorized_manifest, preview, _json
from zf.runtime.flow_role_activation_fence import live_run_acceptance
from zf.runtime.goal_claim_set import _digest as claim_digest
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package, hydrate_plan_artifact_package, write_plan_artifact_package,
)
from zf.runtime.run_admission import admit_workflow_invoke, build_run_admission_projection
from zf.runtime.workflow_anchor import mark_workflow_fanout_anchor, mark_workflow_managed_task


def recover_pending_continuations(runtime) -> None:
    """The idle replay caller repairs a crash after acceptance but before entry."""
    events = runtime.event_log.read_all()
    published = {e.payload.get("workflow_run_id") for e in events if e.type == "candidate.ready"}
    rejected = {e.payload.get("source_event_id") for e in events if e.type == "workflow.invoke.rejected"}
    pending = [e for e in events if e.type == "workflow.invoke.requested"
               and e.payload.get("candidate_continuation_ref")
               and e.payload.get("workflow_run_id") not in published and e.id not in rejected]
    if not pending:
        return
    projection = build_run_admission_projection(events)
    for event in pending:
        run_id = str(event.payload.get("workflow_run_id") or "")
        entry = projection.runs.get(run_id)
        if entry is not None and (entry.terminal or entry.status == "paused"):
            continue
        runtime._on_workflow_invoke_requested(event)


def admit_continuation(runtime, event):
    from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision

    payload = event.payload
    run_id = str(payload.get("workflow_run_id") or "")
    task_id = str(event.task_id or "")
    try:
        receipt = authorized_manifest(runtime, payload.get("candidate_continuation_ref") or {}, run_id)
        manifest = receipt["manifest"]
        if task_id != "CONT-" + manifest["manifest_digest"][:24].upper():
            raise ValueError("Candidate continuation anchor mismatch")
        service = SimpleNamespace(state_dir=runtime.state_dir, writer=runtime.event_writer,
                                  config=runtime.config, project_root=runtime.project_root)
        current = preview(service, source_run_id=manifest["source_run_id"],
                          candidate_event_id=manifest["candidate_event_id"])
        if current != manifest:
            raise ValueError("Candidate continuation source changed since approval")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        runtime._emit_workflow_invoke_rejected(event, "Candidate continuation: " + str(exc),
            task_id=task_id, pattern_id=str(payload.get("pattern_id") or ""))
        return WorkflowRuntimeDecision(action="block", task_id=task_id,
                                       reason="Candidate continuation rejected: " + str(exc))
    admission = admit_workflow_invoke(runtime, event)
    if admission.status != "admitted":
        return WorkflowRuntimeDecision(action="observe" if admission.status in {"queued", "paused"} else "block",
            task_id=task_id, reason="Candidate continuation " + admission.status)
    with live_run_acceptance(runtime, payload):
        events = runtime.event_log.read_all()
        existing = [e for e in events if e.payload.get("workflow_run_id") == run_id]
        if any(e.type == "candidate.ready" for e in existing):
            return WorkflowRuntimeDecision(action="observe", task_id=task_id,
                                           reason="Candidate continuation entry already published")
        _publish_entry(runtime, event, receipt, existing)
    return WorkflowRuntimeDecision(action="workflow_invoke", task_id=task_id,
                                   reason="Candidate continuation admitted at frozen Candidate")


def _publish_entry(runtime, invoked, receipt, existing):
    """Each durable step is replayable after a crash, including acceptance."""
    manifest = receipt["manifest"]
    descriptor = invoked.payload["candidate_continuation_ref"]
    run_id = receipt["workflow_run_id"]
    task_id = invoked.task_id
    generation = manifest["task_map_generation"]
    state = runtime.state_dir
    old_claims = _json(state, manifest["source_claim_set_ref"])
    claims = {**old_claims, "workflow_run_id": run_id, "goal_id": run_id}
    claims["claim_set_digest"] = claim_digest({k: v for k, v in claims.items() if k != "claim_set_digest"})
    claim_ref = write_immutable_json_sidecar(state, claims, root="goal-closure/claim-sets",
        kind="goal_claim_set", schema_version="goal-claim-set.v1", created_by="candidate-continuation")
    old_package = hydrate_plan_artifact_package(state, manifest["plan_package_ref"])
    from zf.runtime.product_acceptance import (
        bind_product_acceptance_spec_identity, product_acceptance_binding_from_package,
    )

    produced = [{**claim_ref, "logical_name": "goal_claim_set", "artifact_kind": "goal_claim_set"}]
    acceptance = product_acceptance_binding_from_package(state, manifest["plan_package_ref"])
    if acceptance.get("spec"):
        spec = bind_product_acceptance_spec_identity(acceptance["spec"], workflow_run_id=run_id,
            flow_kind=old_package["flow_kind"], plan_revision=old_package["plan_revision"],
            task_map_generation=generation)
        spec_ref = write_immutable_json_sidecar(state, spec, root="product-acceptance/specs",
            kind="product_acceptance_spec", schema_version="product_acceptance_spec.v1",
            created_by="candidate-continuation")
        produced.append({**spec_ref, "logical_name": "product_acceptance_spec",
                         "artifact_kind": "product_acceptance_spec",
                         "source_package_ref": manifest["plan_package_ref"]["ref"],
                         "source_package_digest": manifest["plan_package_ref"]["sha256"]})
    inherited = [{**p, "source_package_ref": manifest["plan_package_ref"]["ref"],
                  "source_package_digest": manifest["plan_package_ref"]["sha256"]}
                 for p in [*old_package["produced"], *old_package["inherited"]]
                 if p["logical_name"] not in {port["logical_name"] for port in produced}]
    package = build_plan_artifact_package(workflow_run_id=run_id, flow_kind=old_package["flow_kind"],
        producer_stage_id="candidate-continuation", run_contract={"ref": old_package["run_contract_ref"],
            "sha256": old_package["run_contract_sha256"], "contract_digest": old_package["run_contract_digest"]},
        plan_revision=old_package["plan_revision"], task_map_generation=generation,
        produced=produced,
        inherited=inherited, required_ports=old_package["required_ports"],
        package_mode=old_package.get("artifact_package_mode", "blocking"))
    package_ref = write_plan_artifact_package(state, package)
    product_acceptance_binding_from_package(state, package_ref)
    package_fields = {"plan_artifact_package_id": package_ref["package_id"],
                      "plan_artifact_package_ref": package_ref["ref"],
                      "plan_artifact_package_digest": package_ref["sha256"]}
    claim_fields = {"goal_claim_set_ref": claim_ref["ref"], "goal_claim_set_digest": claim_ref["sha256"]}
    common = {**invoked.payload, **package_fields, **claim_fields, "task_map_generation": generation,
              "candidate_continuation_ref": descriptor}

    def emit_once(kind, body):
        prior = next((e for e in existing if e.type == kind), None)
        if prior:
            return prior
        event = runtime.event_writer.append(ZfEvent(type=kind, actor="orchestrator", origin="kernel",
            task_id=task_id, payload=body, causation_id=invoked.id, correlation_id=run_id))
        existing.append(event)
        return event

    task = runtime.task_store.get(task_id)
    if task is None:
        task = mark_workflow_fanout_anchor(Task(id=task_id, title="冻结候选验收接续", status="in_progress"),
            request_id=run_id, workflow_run_id=run_id, pattern_id=invoked.payload["pattern_id"])
        mark_workflow_managed_task(task)
        runtime.task_store.add(task)
    emit_once("task.created", {"task_id": task_id, "workflow_run_id": run_id,
                               "source": "workflow_invoke_bootstrap"})
    emit_once("workflow.invoke.accepted", {**common, "source_event_id": invoked.id,
                                          "topology": "candidate_continuation"})
    source_goal = next(e for e in runtime.event_log.read_all() if e.id == manifest["source_goal_event_id"])
    emit_once("run.goal.started", {**common, "objective": source_goal.payload.get("objective", ""),
                                   "execution_scope": "candidate_continuation"})
    emit_once("goal.claim_set.pinned", {**common, "status": "ready", "mode": "blocking",
        "goal_claim_set_content_digest": claims["claim_set_digest"], "source": "candidate-continuation"})
    emit_once("plan.artifact_package.admitted", {**common, "status": "admitted",
        "package_id": package_ref["package_id"], "package_ref": package_ref["ref"],
        "package_digest": package_ref["sha256"], "plan_revision": package["plan_revision"],
        "package_slot": package["package_slot"], "producer_stage_id": "candidate-continuation",
        "artifact_package_mode": package.get("artifact_package_mode", "blocking"),
        "source": "candidate-continuation"})
    freeze = _json(state, manifest["freeze_receipt_ref"])
    freeze.update({**package_fields, **claim_fields, "workflow_run_id": run_id, "goal_id": run_id,
        "pdd_id": task_id, "feature_id": task_id, "task_id": task_id,
        "generation_id": "continuation-" + manifest["manifest_digest"][:24],
        "freeze_id": manifest["manifest_digest"], "fanout_id": "continuation-" + manifest["manifest_digest"][:24],
        "candidate_continuation_ref": descriptor, "source_candidate_event_id": manifest["candidate_event_id"]})
    freeze_ref = write_immutable_json_sidecar(state, freeze, root="candidate-freeze-receipts",
        kind="candidate_freeze_receipt", schema_version="candidate-freeze-receipt.v1",
        created_by="candidate-continuation")
    # This is a new freeze over the same commit, never an old verdict re-labelled.
    evidence_paths = [str(state / p["receipt_ref"]["ref"]) for p in manifest["execution_evidence"]]
    feedback = (
        "This is an owner-approved verification continuation, not a fresh Impl run. "
        "Completed Tasks retain their source Run and independent verification identity. "
        "Do not import old Candidate verdicts. Reconcile the current acceptance contract against "
        "the bound source execution receipts (target, command, exit code, output and source stability); "
        "receipts are execution facts, not a product verdict. Run only missing or invalidated checks. "
        "Continuation receipt: " + str(state / descriptor["ref"])
        + ". Source execution receipts: " + ", ".join(evidence_paths)
    )
    emit_once("candidate.ready", {**freeze, "freeze_receipt_ref": freeze_ref,
        "freeze_receipt_digest": freeze_ref["sha256"], "target_commit": manifest["candidate_head_commit"],
        "target_ref": manifest["candidate_ref"], "source": "candidate-continuation",
        "operator_feedback": [feedback],
        "requirement_spec_ref": invoked.payload.get("requirement_spec_ref", "")})
