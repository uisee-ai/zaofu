"""Owner recovery of a proved, undelivered Plan Critic from its current Planner."""

from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.core.task.store import TaskStore
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.call_result_envelope import (
    canonical_json_sha256, hydrate_call_result_envelope, write_immutable_json_sidecar,
)
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.plan_critic_reassessment import _check_source
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.stage_replan_generation import escalation_generation_id
from zf.runtime.workflow_operation import _safe_component, operation_request_hash
from zf.runtime.workflow_task_lifecycle import reopen_workflow_managed_task_from_run_recovery

KIND = "unissued_plan_critic_recovery"
FIELDS = ("resume_planner_handoff_event_id", "workflow_run_id", "escalation_event_id",
          "planner_admitted_event_id", "planner_control_sha256", "approval_ref")
_WEB_ENVELOPE = {"project_id", "action_id", "actor", "source_session_id", "evidence_refs", "idempotency_key"}


def _only(rows, message):
    matches = list(rows)
    if len(matches) != 1:
        raise ValueError(message)
    return matches[0]


def _run(event):
    return event.payload.get("workflow_run_id") or event.correlation_id


def _binding(payload):
    if set(payload) - set(FIELDS) - _WEB_ENVELOPE or not all(
        isinstance(payload.get(k), str) and payload[k].strip() for k in FIELDS
    ):
        raise ValueError("an exact Run, escalation, handoff failure, Planner admission, digest and owner approval are required")
    return {k: payload[k] for k in FIELDS}


def prepare_checkpoint(state, events, binding):
    run = binding["workflow_run_id"]
    facts = [e for e in events if _run(e) == run and e.origin in {"", "kernel"}]
    kernel = [e for e in facts if e.origin == "kernel"]
    ids = {e.id: e for e in facts}
    escalation = ids.get(binding["escalation_event_id"])
    failure = ids.get(binding["resume_planner_handoff_event_id"])
    if (not escalation or escalation.type != "human.escalate" or escalation.actor != "orchestrator"
            or escalation.payload.get("failure_class") != "stage_replan_cap_exhausted"
            or "start_new_generation" not in escalation.payload.get("allowed_actions", [])):
        raise ValueError("stage cap escalation does not authorize a successor")
    source = ids.get(escalation.payload.get("source_event_id"))
    if (not failure or failure.type != "fanout.synth.completed" or failure.origin != "kernel"
            or failure.actor != "zf-cli" or failure.payload.get("status") != "failed"
            or any(failure.payload.get(k) for k in ("report", "operation_id", "request_hash", "control_result_ref"))
            or not source or source.origin != "kernel" or source.causation_id != failure.id):
        raise ValueError("escalation must follow a pre-dispatch failure without a Critic verdict")
    fanout, stage = failure.payload["fanout_id"], failure.payload["stage_id"]
    starts = [e for e in kernel if e.type == "fanout.started" and e.payload.get("stage_id") == stage]
    if not starts or starts[-1].payload.get("fanout_id") != fanout:
        raise ValueError("a newer fanout owns this Plan stage")
    manifest = FanoutManifestProjector(state).rebuild(fanout, kernel)
    aggregate, spec = manifest["aggregate"], manifest["aggregate_config"]
    if (manifest.get("workflow_run_id") != run or aggregate.get("status") != "failed"
            or aggregate.get("synth_event_id") != failure.id or source.type != spec.get("failure_event")
            or failure.payload.get("role_instance") != spec.get("synth_role")
            or source.payload.get("fanout_id") != fanout or source.payload.get("stage_id") != stage):
        raise ValueError("the selected failure is not the current Plan aggregate")
    children = manifest["children"]
    if len(children) != 1 or children[0].get("status") != "completed":
        raise ValueError("one completed Planner must own the candidate")
    child = children[0]
    admitted = _only((e for e in kernel if e.type == "workflow.call.result.admitted"
                      and e.payload.get("operation_id") == child.get("operation_id")),
                     "the current Planner has no unique admitted result")
    receipt = admitted.payload
    if (admitted.actor != "zf-cli" or admitted.id != binding["planner_admitted_event_id"]
            or receipt.get("admission_status") != "admitted" or receipt.get("semantic_verdict") != "passed"
            or receipt.get("source_event_id") != child.get("result_event_id")
            or not all(child.get(k) and receipt.get(k) == child[k] for k in ("attempt_id", "request_hash"))
            or receipt["control_result_ref"]["sha256"] != binding["planner_control_sha256"]):
        raise ValueError("selected admission or digest differs from the current Planner")
    envelope = hydrate_call_result_envelope(state, receipt["envelope_ref"])
    control = hydrate_sidecar_ref(state, receipt["control_result_ref"]).payload
    if (not all(envelope["identity"].get(k) == receipt[k] for k in ("workflow_run_id", "operation_id", "request_hash", "attempt_id"))
            or receipt["source_event_id"] not in envelope.get("source_event_ids", [])
            or not all(envelope["control_result"].get(k) == receipt["control_result_ref"][k]
                for k in ("schema_version", "ref", "sha256"))
            or not all(control.get(k) == child.get(k) for k in ("operation_id", "attempt_id", "role_instance", "request_hash"))
            or control.get("workflow_run_id") != run or control.get("fanout_id") != fanout
            or control.get("execution_status") != "completed" or control.get("result_semantics") != "artifact_production"
            or control.get("stage_id") != stage or envelope["identity"].get("attempt_domain") != "plan"):
        raise ValueError("Planner envelope does not attest this stage's completed candidate")

    issue = _only((e for e in kernel if e.type == "workflow.operation.requested"
                   and e.payload.get("operation_type") == "fanout_synth"
                   and e.payload.get("active_attempt_id") == failure.payload.get("run_id")),
                  "no unique pre-registered Critic request for the failed attempt")
    oid = issue.payload["operation_id"]
    op_facts = [e for e in kernel if e.payload.get("operation_id") == oid]
    blocked = _only((e for e in op_facts if e.type == "workflow.operation.blocked"
                     and e.payload.get("reason") == "request_hash_divergence"), "Critic registration failure is unproven")
    if (any(a.get("operation_id") == oid for a in TaskAttemptStore(state / "task_attempts.json").current_rows())
            or not (facts.index(admitted) < facts.index(issue) < facts.index(blocked) < facts.index(failure))
            or blocked.payload.get("expected_request_hash") != issue.payload.get("request_hash")
            or any(e.type not in {"workflow.operation.requested", "workflow.operation.blocked"} for e in op_facts)
            or any(e.type == "fanout.synth.dispatched" and e.payload.get("fanout_id") == fanout for e in kernel)
            or any(e.type in {"plan.approved", "task_map.ready", "task.pipeline.generation.admitted"}
                   or (e.origin == "kernel" and e.type == "workflow.operation.requested"
                       and e.payload.get("parent_stage_id") == stage)
                   for e in facts[facts.index(failure) + 1:])):
        raise ValueError("Critic may have been dispatched, completed, or superseded")
    signed = hydrate_sidecar_ref(state, issue.payload["request_ref"]).payload
    request = signed["request"]
    identity = request["result_identity"]
    digest = identity["plan_synth_contract_digest"]
    if (operation_request_hash(signed) != issue.payload["request_hash"]
            or signed["operation_id"] != oid or signed["workflow_run_id"] != run
            or request.get("role_instance") != spec["synth_role"] or request.get("attempt_domain") != "plan"
            or request.get("fanout_id") != fanout or request.get("stage_id") != stage
            or identity.get("reviewed_plan_candidate_digest") != digest):
        raise ValueError("original Critic registration does not bind the current Plan")
    contract = hydrate_sidecar_ref(state, {"ref": identity["plan_synth_contract_ref"], "sha256": digest}).payload
    if (contract.get("schema_version") != "plan-synth-contract.v1" or contract.get("workflow_run_id") != run
            or contract.get("stage_id") != stage or contract.get("fanout_id") != fanout
            or len(contract["child_bindings"]) != 1 or contract["child_bindings"][0].get("result_event_id") != receipt["source_event_id"]
            or contract["child_bindings"][0].get("child_id") != child["child_id"]):
        raise ValueError("Critic inputs belong to a different Planner")
    refs = [r for r in contract["source_bindings"] if r["source_id"].startswith("child-")]
    for suffix, descriptor in (("admitted-call-result", receipt["envelope_ref"]), ("control-result", receipt["control_result_ref"])):
        if not any(r["source_id"] == f"child-{child['child_id']}-{suffix}" and
                   all(r.get(k) == descriptor[k] for k in ("ref", "sha256")) for r in refs):
            raise ValueError("the current Planner outputs are absent from the original Critic contract")
    history = []
    previous = [r for r in contract["source_bindings"] if r["source_id"] == "plan-rejected-review"]
    if previous or any(row.get("source_id") == "plan-rejected-review" for row in request.get("required_reads", [])):
        history_binding = _only(previous, "historical rejection source is missing or ambiguous")
        attempt = signed["active_attempt_id"]
        manifest_digest = request["source_manifest_digest"]
        original = hydrate_sidecar_ref(state, {
            "ref": f"artifacts/attempts/{_safe_component(attempt)}/source-manifests/{manifest_digest}.json",
            "sha256": manifest_digest,
        }).payload
        if original.get("workflow_run_id") != run or original.get("attempt_id") != attempt:
            raise ValueError("historical Critic input manifest is not bound to the original attempt")
        history.append(_only((r for r in original["sources"] if all(r.get(k) == history_binding.get(k)
            for k in ("source_id", "ref", "sha256", "artifact_id"))), "historical rejection binding changed"))
    for ref in [*refs, *history]:
        _check_source(state, ref)
    return {"schema_version": "plan-handoff-checkpoint.v1", **binding, "candidate_inputs": refs,
            "previous_critic_operation_id": oid, "fanout_id": fanout, "stage_id": stage,
            "historical_review_inputs": history,
            "task_id": child.get("task_id") or manifest.get("task_id"), "mode": spec["mode"]}


def apply_handoff_recovery(service, requested, action, requested_action, payload):
    try:
        if service.actor not in {"operator", "web"}:
            raise ValueError("Plan handoff recovery requires owner authority")
        binding = _binding(payload)
        with locked_path(service.state_dir / "locks" / "reader-stage-recovery"):
            review, replayed = _apply(service, requested, binding)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return service._recovery_failed(requested, action, requested_action, str(exc), 409)
    return service._recovery_ok(requested, review, action, requested_action,
        {"review_event_id": review.id, "review_key": review.payload["review_key"], "replayed": replayed,
         "stage_trigger_event_id": review.id, "run_goal_update_event_id": review.causation_id,
         "stage_replan_generation": review.payload["stage_replan_generation"], "run_id": binding["workflow_run_id"],
         "escalation_event_id": binding["escalation_event_id"]})


def _apply(service, requested, binding):
    writer, state = service.writer, service.state_dir
    run = binding["workflow_run_id"]
    failure_id = binding["resume_planner_handoff_event_id"]
    key = canonical_json_sha256(binding)
    offset = writer.event_log.current_offset()
    events = writer.event_log.read_all()
    previous = [e for e in events if e.origin == "kernel" and e.payload.get("recovery_kind") == KIND
                and e.payload.get("resume_planner_handoff_event_id") == failure_id]
    if any(e.payload.get("review_key") != key for e in previous):
        raise ValueError("this failed handoff already used a different approval or candidate")
    marker = next((e for e in previous if e.type == "fanout.aggregate.started"), None)
    if marker:
        return marker, True
    active = next((e for e in previous if e.type == "run.goal.updated"), None)
    entry = build_run_admission_projection(events).runs.get(run)
    terminal = next((e for e in events if entry and e.id == entry.terminal_event_id), None)
    if not (active and entry and entry.status == "running") and not (
        not active and entry and entry.status == "blocked" and terminal and terminal.origin == "kernel"
        and terminal.type == "run.goal.blocked" and terminal.causation_id == binding["escalation_event_id"]
    ):
        raise ValueError("Run is not at the selected cap escalation")
    body = prepare_checkpoint(state, events, binding)
    checkpoint = write_immutable_json_sidecar(state, body, root="plan-synth/reassessments",
        kind="plan_review_checkpoint", schema_version=body["schema_version"], created_by="controlled-action",
        source_event_id=requested.id)
    common = {**binding, "review_key": key, "recovery_kind": KIND, "stage_id": body["stage_id"],
              "fanout_id": body["fanout_id"], "trace_id": run,
              "stage_replan_generation": escalation_generation_id(run, binding["escalation_event_id"]),
              "stage_replan_generation_source_event_id": binding["escalation_event_id"],
              "operator_authorized": True, "operator_authorization_ref": binding["approval_ref"]}
    if not active:
        active = writer.append(ZfEvent(type="run.goal.updated", actor=service.actor, origin="kernel",
            task_id=body["task_id"], correlation_id=run, causation_id=requested.id, payload={**common,
            "run_id": run, "status": "active", "reason": "authorized_stage_replan_new_generation"}),
            expected_offset=offset)
    reopen_workflow_managed_task_from_run_recovery(task_store=TaskStore(state / "kanban.json"),
                                                  event_writer=writer, recovery_event=active)
    # Recheck the ledger after activation and carrier projection before publishing the dispatch checkpoint.
    offset = writer.event_log.current_offset()
    current = writer.event_log.read_all()
    if build_run_admission_projection(current).runs[run].status != "running" or prepare_checkpoint(state, current, binding) != body:
        raise ValueError("Run or candidate changed while authorizing the new Critic")
    return writer.append(ZfEvent(type="fanout.aggregate.started", actor="zf-cli", origin="kernel",
        task_id=body["task_id"], correlation_id=run, causation_id=active.id, payload={**common,
        "review_checkpoint_ref": checkpoint, "mode": body["mode"]}), expected_offset=offset), False


def rejected_handoff_lineage(state, index, events, failure):
    """A real rejection can spend the owner's new generation only against this reviewed candidate."""
    from zf.runtime.plan_synth_recovery_refs import resume_failed_critic_candidate
    from zf.runtime.run_scope import run_aliases
    from zf.runtime.stage_replan_generation import completed_generation_source

    generation = failure.payload.get("stage_replan_generation")
    if not generation:
        return {}
    marker = next((e for _, e in index.rows("fanout.aggregate.started")
                   if e.origin == "kernel" and e.actor == "zf-cli" and e.payload.get("recovery_kind") == KIND
                   and e.payload.get("stage_replan_generation") == generation
                   and e.payload.get("fanout_id") == failure.payload.get("fanout_id")), None)
    if not marker:
        return {}
    receipt = next((e for _, e in index.rows("runtime.action.completed") if e.causation_id == marker.id
                    and e.origin in {"", "kernel"} and not e.payload.get("replayed")), None)
    synth = index.by_id.get(failure.causation_id)
    if (not receipt or completed_generation_source(receipt, index.by_id, run_aliases(events)) is None
            or not synth or failure.origin != "kernel" or _run(failure) != _run(marker)
            or index.positions[synth.id] <= index.positions[marker.id]):
        raise ValueError("new Plan generation lacks a bound approval and independent Critic verdict")
    refs = resume_failed_critic_candidate(state, events, failure, synth.id)
    expected = hydrate_sidecar_ref(state, marker.payload["review_checkpoint_ref"]).payload["candidate_inputs"]
    if {tuple(r[k] for k in ("source_id", "ref", "sha256", "artifact_id")) for r in expected} != {
        tuple(r[k] for k in ("source_id", "ref", "sha256", "artifact_id")) for r in refs
    }:
        raise ValueError("rejected candidate differs from the owner's reviewed Planner")
    return {**generation_context(marker), "previous_plan_candidate_refs": refs}


def generation_context(marker):
    return {**{k: marker.payload[k] for k in ("stage_replan_generation", "stage_replan_generation_source_event_id",
                                              "operator_authorized", "operator_authorization_ref")}, "rework_attempt": 0}
