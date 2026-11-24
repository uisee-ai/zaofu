"""Bind only a successor's approved Candidate counterexamples to its stage inputs."""
from __future__ import annotations

from typing import Any

from zf.runtime.artifact_access import require_artifact_access
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
from zf.runtime.candidate_repair_outcome import _published_repair
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


SOURCE = "candidate-repair-feedback"


def attach_candidate_repair_feedback(runtime, payload: dict, contract: dict, context: dict, events: list) -> None:
    stage = payload.get("task_pipeline_stage")
    if stage not in {"impl", "verify"} or not context.get("trigger_event_id"):
        return
    ready = next((e for e in reversed(events) if e.id == context["trigger_event_id"]), None)
    if ready is None or ready.type != "task_map.ready":
        return
    checkpoint = str(ready.payload.get("resume_checkpoint_ref") or "")
    if not checkpoint.startswith("oa-candidate-repair-"):
        return
    # Reconstruct the original request exactly across binary upgrades.
    existing = next((e for e in events if e.origin == "kernel" and e.type == "workflow.operation.requested"
                     and e.payload.get("operation_id") == payload["operation_id"]), None)
    if existing:
        request = hydrate_sidecar_ref(runtime.state_dir, existing.payload["request_ref"]).payload["request"]
        ref = request.get("candidate_repair_feedback_ref")
        if not ref:
            return
        hydrate_sidecar_ref(runtime.state_dir, ref)
    else:
        ref = _freeze_feedback(runtime, payload, contract, context, ready, checkpoint, events)
    payload["candidate_repair_feedback_ref"] = ref
    payload["input_refs"] = [*payload.get("input_refs", []), {**ref, "source_id": SOURCE,
        "artifact_id": SOURCE, "allowed_paths": ["$"]}]


def _freeze_feedback(runtime, payload, contract, context, ready, checkpoint, events):
    run, task = payload["workflow_run_id"], payload["task_id"]
    admission = next((e for e in events if e.type == "orchestrator.semantic.decision.admitted"
                      and e.origin == "kernel" and e.payload.get("operation_id") == checkpoint.removeprefix("oa-candidate-repair-")
                      and e.payload.get("workflow_run_id") == run), None)
    publication = _published_repair(runtime.state_dir, events, admission.causation_id) if admission else None
    if not publication or publication[3].id != ready.id:
        raise ValueError("Candidate repair feedback lacks its admitted OA publication")
    admitted, action, _, _ = publication
    if (contract.get("workflow_run_id") != run or contract.get("task_id") != task
            or contract.get("task_map_generation") != context.get("task_map_generation")
            or ready.payload["workflow_run_id"] != run or context.get("task_map_ref") != ready.payload["task_map_ref"]
            or context.get("task_map_generation") != ready.payload["task_map_generation"]
            or (context.get("task_map_digest") and context["task_map_digest"] != ready.payload["task_map_digest"])
            or context.get("dispatch_base_commit") != action["source_commit"] or task not in ready.payload["task_ids"]):
        raise ValueError("Candidate repair feedback target differs from the published successor")
    gaps = [r for r in action["rework_summary"]["gap_tasks"] if r["task_id"] == task]
    if len(gaps) != 1 or len(gaps[0].get("supersedes_task_ids", [])) != 1:
        raise ValueError("Candidate repair successor lacks a unique predecessor")
    gap = gaps[0]
    parent = gap["supersedes_task_ids"][0]
    if gap.get("base_commit") != action["source_commit"]:
        raise ValueError("Candidate repair successor base differs from the rejected target")
    actor = payload.get("role_instance", "")
    role_name = next((r.name for r in runtime.config.roles if r.instance_id == actor), actor)

    def allowed(ref):
        require_artifact_access(ref.get("access_scope"), actor=actor, role=role_name,
                                purpose="implementation" if payload["task_pipeline_stage"] == "impl" else "task-verify")

    decision_ref = admitted.payload["decision_ref"]
    allowed(decision_ref)
    decision = hydrate_sidecar_ref(runtime.state_dir, decision_ref).payload
    directive, = [d for d in decision["delta"]["directives"] if d.get("candidate_repair")
                  and any(g["task_id"] == task for g in d["candidate_repair"]["gap_tasks"])]
    repair = directive["candidate_repair"]
    if decision["identity"]["workflow_run_id"] != run or repair.get("contract_changed") is not False:
        raise ValueError("Candidate repair feedback decision no longer matches its authority")
    # Re-evaluate the evidence as it stood at the admitted decision. A subsequent
    # integration/Verify attempt must not change the predecessor evidence.
    bound = aggregate_candidate_binding(runtime.state_dir, events[:events.index(admitted) + 1],
        failure_id=action["source_event_id"], run_id=run, task_id=action["pdd_id"])
    if (bound["source_commit"] != action["source_commit"] or bound["candidate_event_id"] != action["candidate_event_id"]
            or bound["task_map_generation"] != action["task_map_generation"]):
        raise ValueError("Candidate repair evidence does not describe the rejected freeze")
    dispositions = {r["finding_id"]: r for r in repair.get("finding_dispositions", [])}
    acceptance = {c["acceptance_id"] for c in contract["acceptance_criteria"]}
    scoped = []
    for row in bound["findings"]:
        if not row["product_repair_eligible"] or row["task_ids"] != [parent]:
            continue
        disposition = dispositions.get(row["finding_id"], {})
        if dispositions and disposition.get("classification") != "product_defect":
            continue
        ref = row["control_result_ref"]
        allowed(ref)
        result = hydrate_sidecar_ref(runtime.state_dir, ref).payload
        original = hydrate_sidecar_ref(runtime.state_dir, {
            "ref": result["contract_snapshot_ref"], "sha256": result["contract_snapshot_digest"]}).payload
        criterion = next((c for c in original["acceptance_criteria"]
                          if c["acceptance_id"] == row["evidence"].get("acceptance_id") and c.get("source_task_id") == parent), {})
        if criterion.get("source_acceptance_id") not in acceptance:
            raise ValueError("Candidate counterexample does not match a successor acceptance criterion")
        scoped.append({"finding_id": row["finding_id"], "acceptance_id": criterion["source_acceptance_id"],
                       "control_result_ref": ref, "pointer": row["pointer"], "evidence": row["evidence"],
                       "disposition": disposition})
    if not scoped:
        raise ValueError("Candidate repair successor lacks a scoped, admitted product counterexample")
    body: dict[str, Any] = {"schema_version": "candidate-repair-feedback.v1", "workflow_run_id": run,
        "task_id": task, "predecessor_task_id": parent, "task_map_generation": context["task_map_generation"],
        "candidate_event_id": bound["candidate_event_id"], "candidate_commit": bound["source_commit"],
        "decision_ref": decision_ref, "ready_event_id": ready.id, "findings": scoped}
    if len(repair["gap_tasks"]) == 1:
        body.update(rationale=repair["rationale"], required_actions=directive["required_actions"])
    return write_immutable_json_sidecar(runtime.state_dir, body, root="candidate-repair-feedback",
        kind="candidate_repair_feedback", schema_version=body["schema_version"], created_by="kernel", source_event_id=admitted.id)
