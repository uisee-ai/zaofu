"""Return a rejected pre-operation Plan source to the OA of its approved Run."""

from dataclasses import replace
from pathlib import Path, PureWindowsPath

from zf.core.config.stage_teams import active_members, member_child
from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.fanout import FanoutChild, FanoutContext, FanoutManifestProjector
from zf.runtime.orchestrator_agent_policy import checkpoint_policy
from zf.runtime.orchestrator_agent_role import configured_orchestrator_role
from zf.runtime.plan_team_handoff import plan_member_results
from zf.runtime.plan_team_inputs import PlanSourceUnavailable, _slice
from zf.runtime.plan_team_transfer import approved_team
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import reduce_workflow_operations

REJECTED = "fanout.plan.source.rejected"
REBOUND = "fanout.plan.source.rebound"


def _authority(runtime, events, run, digest, charter):
    proposal, team = approved_team(runtime.state_dir, events, run, digest)
    snapshot = hydrate_sidecar_ref(runtime.state_dir, proposal["effective_config_ref"]).payload
    orchestration = snapshot["config"]["workflow"].get("orchestration", {})
    policy = orchestration.get("flow_policies", {}).get("prd", {})
    oa = configured_orchestrator_role(runtime)
    if (not oa or orchestration.get("oa_profile") not in {oa.name, oa.instance_id}
            or not policy.get("pilot_id") or policy.get("mode") != "semantic_control"
            or "plan_source" not in policy.get("checkpoints", [])
            or policy.get("checkpoint_policies", {}).get("plan_source", "blocking") != "blocking"
            or checkpoint_policy(runtime.config, "plan_source", flow_kind="prd") != "blocking"):
        raise ValueError("Plan source recovery is outside the approved Run policy")
    if charter not in [member_child(member, team["author"])["payload"]["stage_obligation"]
                       for member in active_members(team)]:
        raise ValueError("Plan source recovery does not match the original approved obligation")
    return proposal, oa.instance_id


def project_source_event(manifest, children, event):
    p = event.payload
    child = children.get(p.get("child_id"))
    if (manifest["stage_id"] != "prd-scan" or not child or child["role_instance"] != p.get("role_instance")
            or (manifest.get("workflow_run_id") or manifest["trace_id"]) != p.get("workflow_run_id")):
        return
    charter = child["payload"].get("stage_obligation", {})
    if charter.get("obligation_id") != p.get("obligation_id") or p.get("input_ref") not in charter["input_refs"]:
        return
    payload = child["payload"]
    snapshots = payload.get("source_rebind_snapshots", {})
    if p["input_ref"] in snapshots:
        return
    if event.type == REJECTED and child["status"] == "pending":
        if payload.get("source_rebind_event_id") and event.causation_id != payload["source_rebind_event_id"]:
            return
        child.update(status="awaiting_source", source_rejection_id=event.id, last_event_id=event.id)
    if (event.type == REBOUND and child["status"] == "awaiting_source"
            and child["source_rejection_id"] == p.get("source_event_id")):
        snapshots = {**snapshots, p["input_ref"]: {"ref": p["replacement_ref"], "sha256": p["source_sha256"]}}
        # Keep the approved charter intact. Only execution inputs follow signed replacements.
        refs = [snapshots[ref]["ref"] if ref in snapshots else ref for ref in charter["input_refs"]]
        child["payload"] = {**payload, "input_refs": refs, "source_rebind_event_id": event.id,
                            "source_rejection_id": p["source_event_id"], "source_rebind_snapshots": snapshots}
        child.update(status="pending", last_event_id=event.id)


def defer_source_rejection(runtime, context, child, cause, exc):
    if (context.stage_id != "prd-scan" or not isinstance(exc, PlanSourceUnavailable)
            or checkpoint_policy(runtime.config, "plan_source", flow_kind="prd") != "blocking"):
        return False
    run = child.payload.get("workflow_run_id") or context.trace_id
    with locked_path(runtime.state_dir / "locks" / "plan-source-recovery"):
        events = runtime.event_log.read_all()
        # A restart can redrive the old cached child with fanout.started as cause.
        # Rebuild its current authorization from the ledger, including pre-upgrade receipts.
        manifest = FanoutManifestProjector(runtime.state_dir).rebuild(context.fanout_id, events)
        live = next((c for c in manifest["children"] if c["child_id"] == child.child_id
                     and c["role_instance"] == child.role_instance), None)
        if not live:
            return False
        payload = live["payload"]
        charter = payload.get("stage_obligation", {})
        start = next((e for e in events if e.type == "fanout.started" and e.origin == "kernel"
                      and e.payload.get("fanout_id") == context.fanout_id), None)
        if (not start or cause not in {start.id, child.payload.get("source_rebind_event_id")}
                or run != (manifest.get("workflow_run_id") or manifest["trace_id"])
                or not any(c.get("child_id") == child.child_id and
                           c.get("payload", {}).get("stage_obligation") == charter
                           for c in start.payload.get("expected_children", []))):
            return False
        try:
            proposal, owner = _authority(runtime, events, run, child.payload.get("workflow_proposal_digest", ""), charter)
        except (ValueError, KeyError, OSError):
            return False
        # Another dispatcher may already have deferred, corrected or finished this child.
        # A stale preregistration failure cannot override the newer state.
        if live["status"] != "pending" or child.payload.get("source_rebind_event_id") != payload.get("source_rebind_event_id"):
            return True
        if exc.ref not in charter["input_refs"] or exc.ref in payload.get("source_rebind_snapshots", {}):
            return False
        runtime.event_writer.append(ZfEvent(type=REJECTED, actor="zf-cli", origin="kernel",
            causation_id=payload.get("source_rebind_event_id") or start.id, correlation_id=run, payload={
                "workflow_run_id": run, "workflow_proposal_digest": proposal["proposal_digest"],
                "stage_id": "prd-scan", "fanout_id": context.fanout_id, "child_id": child.child_id,
                "role_instance": child.role_instance, "obligation_id": charter["obligation_id"],
                "input_ref": exc.ref, "reason": str(exc), "recovery_owner": owner}))
    return True


def _waiting(runtime, history, source):
    p = source.payload
    manifest = FanoutManifestProjector(runtime.state_dir).rebuild(p["fanout_id"], history)
    child = next((c for c in manifest.get("children", []) if c["child_id"] == p["child_id"]), {})
    if (child.get("status") != "awaiting_source" or child.get("source_rejection_id") != source.id
            or manifest.get("status") != "started" or manifest.get("aggregate", {}).get("status") != "pending"
            or runtime._fanout_identity_stale_reason(p["fanout_id"])[0]):
        raise ValueError("source rejection is no longer current")
    return manifest, child


def _fail(runtime, source, cause, reason, *, operation=""):
    p = source.payload
    runtime.event_writer.append(ZfEvent(type="fanout.child.failed", actor="zf-cli", origin="kernel",
        causation_id=cause, correlation_id=source.correlation_id, payload={**p,
            "run_id": f"run-{p['fanout_id']}-{p['child_id']}", "status": "failed",
            "failure_kind": "infra", "failure_scope": "operation_preregistration",
            "failure_class": "reader_execution_failure", "source_event_id": source.id,
            "orchestrator_operation_id": operation, "reason": reason, "recovery_owner": "run_manager"}))
    runtime._evaluate_reader_fanout(p["fanout_id"])


def reconcile_source_rejections(runtime, events):
    pending = {e.id: e for e in events if e.type == REJECTED and e.origin == "kernel"}
    if not pending:
        return
    # Request only once. A lost OA with a running operation remains owned by normal lease/transport recovery.
    checkpoints = {e.payload.get("source_event_id"): e for e in events if e.origin == "kernel"
                   and e.type == "orchestrator.semantic.checkpoint.requested"
                   and e.payload.get("checkpoint") == "plan_source"}
    closed = {e.payload.get("source_event_id") for e in events
              if e.origin == "kernel" and e.type in {REBOUND, "fanout.child.failed"}}
    if not pending.keys() - closed:
        return
    operations = reduce_workflow_operations(events)
    by_id = {e.id: e for e in events}
    admissions = {e.payload.get("operation_id"): e for e in events if e.type == "workflow.call.result.admitted"
                  and e.origin == "kernel" and e.payload.get("control_result_schema") == "orchestration-decision.v1"}
    for key, source in pending.items():
        if key in closed:
            continue
        chk = checkpoints.get(key)
        terminal = None
        if chk:
            operation = operations.get(chk.payload["operation_id"], {})
            if operation.get("status") in {"failed", "blocked", "cancelled", "superseded"}:
                terminal = by_id.get(operation["last_event_id"])
            else:
                admitted = admissions.get(chk.payload["operation_id"])
                if not admitted:
                    continue
                try:
                    outcome = _resume_oa_result(runtime, operation, admitted, by_id)
                except (ValueError, OSError) as exc:
                    outcome = {"status": "rejected", "reason": str(exc)}
                if not outcome or outcome["status"] != "rejected":
                    continue
                terminal = replace(admitted, payload={**admitted.payload, "reason": outcome.get("reason")})
        with locked_path(runtime.state_dir / "locks" / "plan-source-recovery"):
            history = runtime.event_log.read_all()
            try:
                manifest, child = _waiting(runtime, history, source)
            except ValueError:
                continue
            if runtime._run_fanout_dispatch_blocked(source):
                continue
            if terminal:
                _fail(runtime, source, terminal.id, "OA source correction failed: " +
                      str(terminal.payload.get("reason") or terminal.type), operation=chk.payload["operation_id"])
                continue
            if any(e.type == "orchestrator.semantic.checkpoint.requested" and e.origin == "kernel" and
                   e.payload.get("source_event_id") == key for e in history):
                continue
            p, charter = source.payload, child["payload"]["stage_obligation"]
            try:
                proposal, owner = _authority(runtime, history, p["workflow_run_id"], p["workflow_proposal_digest"], charter)
                if owner != p["recovery_owner"]:
                    raise ValueError("the OA assigned by the approved Proposal has changed")
                from zf.runtime.orchestrator_agent_operations import request_orchestrator_agent_checkpoint

                request_orchestrator_agent_checkpoint(runtime, source_event=source, checkpoint="plan_source",
                    workflow_run_id=p["workflow_run_id"], checkpoint_policy="blocking", payload={
                        "flow_kind": "prd", "effective_config_ref": proposal["effective_config_ref"],
                        "source_rejection": {**p, "question": charter["question"],
                            "source_event_id": key, "expected_output": charter["expected_output"],
                            "alternatives": charter.get("input_alternatives", {}).get(p["input_ref"], []),
                            "admitted_siblings": plan_member_results(manifest)}})
            except (ValueError, KeyError, OSError) as exc:
                _fail(runtime, source, key, str(exc))


def _resume_oa_result(runtime, operation, admitted, by_id):
    """Replay the actual signed result if the host stopped anywhere after result admission."""
    from zf.runtime.call_result_runtime import hydrate_admitted_control_result, workflow_operation_service
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision

    p = admitted.payload
    if (not operation or p.get("request_hash") != operation.get("request_hash")
            or p.get("workflow_run_id") != operation.get("workflow_run_id")
            or p.get("attempt_id") != operation.get("active_attempt_id")):
        return
    envelope = p["envelope_ref"]
    if operation["status"] == "running":
        workflow_operation_service(runtime).settle(operation_id=operation["operation_id"],
            request_hash=operation["request_hash"], workflow_run_id=operation["workflow_run_id"],
            admitted_call_result_ref=envelope, provider_operation_summary_ref=p.get("provider_operation_summary_ref"),
            causation_id=admitted.id, correlation_id=admitted.correlation_id)
    elif operation["status"] != "settled" or operation.get("admitted_call_result_ref") != envelope:
        return
    # A crash after settlement can precede the canonical provider notice. Its underlying
    # Kernel acceptance is already durable. Use that occurrence as causation in that case.
    notice = by_id.get(p.get("source_event_id"))
    if (not notice or notice.type != "orchestrator.semantic.decision.submitted"
            or notice.payload.get("call_result_envelope_ref") != envelope):
        notice = admitted
    action = replace(notice, payload={**notice.payload, "operation_id": operation["operation_id"],
        "orchestration_decision": hydrate_admitted_control_result(runtime.state_dir, envelope),
        "call_result_envelope_ref": envelope, "control_result_ref": p["control_result_ref"]})
    return apply_orchestrator_agent_decision(runtime, action)


def _permitted(runtime, charter, old, new, descriptors=()):
    if (old == new and not old.endswith("/")) or new in charter.get("input_alternatives", {}).get(old, ()):
        return
    # A frozen directory can only be narrowed. Never infer authority over the siblings of a missing file.
    if (not old.endswith("/") or old == "./" or "#" in old or not new or new.startswith(("/", "~"))
            or "\\" in new or ".." in Path(new).parts or PureWindowsPath(new).drive or "://" in new):
        raise ValueError("replacement_ref was not approved for this Plan input")
    old_path = Path(old)
    new_path = Path(new.partition("#")[0])
    root = runtime.project_root.resolve()
    # A label or access-scoped artifact that happens to look like a directory does
    # not authorize a search of that directory. A new label cannot redirect a narrowed path.
    for descriptor in descriptors:
        name = descriptor.get("ref") or descriptor.get("path")
        labels = {descriptor.get("source_id"), descriptor.get("artifact_id")}
        if (old.partition("#")[0] in labels or new.partition("#")[0] in labels
                or isinstance(name, str) and (root / name).resolve() == (root / old_path).resolve()):
            raise ValueError("directory narrowing cannot change a bound workflow artifact")
    if (not old_path.parts or not (root / old_path).is_dir()
            or not new_path.is_relative_to(old_path) or (root / old_path).resolve() != root / old_path
            or (root / new_path).resolve() != root / new_path or new in charter["input_refs"]):
        raise ValueError("replacement_ref exceeds the approved Plan directory")


def apply_source_decision(runtime, event, decision, outcome):
    with locked_path(runtime.state_dir / "locks" / "plan-source-recovery"):
        history = runtime.event_log.read_all()
        source = next((e for e in history if e.id == outcome["source_event_id"]
                       and e.type == REJECTED and e.origin == "kernel"
                       and e.correlation_id == outcome["workflow_run_id"]), None)
        if not source:
            return {**outcome, "status": "rejected", "applied": False, "reason": "source rejection missing"}
        prior = next((e for e in history if e.causation_id == event.id and e.origin == "kernel"
                      and e.type in {REBOUND, "fanout.child.failed"}), None)
        if prior:
            return {**outcome, "applied": prior.type == REBOUND, "source_resolution_event_id": prior.id}
        try:
            _, child = _waiting(runtime, history, source)
        except ValueError as exc:
            return {**outcome, "status": "superseded", "applied": False, "reason": str(exc)}
        p, charter = source.payload, child["payload"]["stage_obligation"]
        from zf.runtime.run_admission import run_dispatch_block_reason

        if run_dispatch_block_reason(runtime, event=source) not in {"", "run_paused"}:
            return {**outcome, "status": "superseded", "applied": False}
        # Retain a signed, scoped decision received during a pause. All physical dispatch
        # entrances check the Run again, including stale-manifest reconstruction on restart.
        try:
            proposal, _ = _authority(runtime, history, p["workflow_run_id"], p["workflow_proposal_digest"], charter)
            if str(decision.get("execution_status", "completed")).lower() != "completed":
                raise ValueError(decision.get("summary") or "OA source decision failed")
            if decision["decision"] != "select_source":
                raise ValueError(decision.get("summary") or "OA escalated the rejected source")
            replacement = decision["replacement_ref"]
            from zf.runtime.workflow_requests import hydrate_workflow_requirement

            requirement = hydrate_workflow_requirement(runtime.state_dir, {**proposal, "revision": proposal["request_revision"]})
            trigger = child["payload"].get("trigger_payload") or {}
            descriptors = [s for body in (requirement, trigger) for key in ("artifact_refs", "input_refs")
                           for s in body.get(key, []) or [] if isinstance(s, dict)]
            _permitted(runtime, charter, p["input_ref"], replacement, descriptors)
            pinned = _slice(runtime, charter, replacement, {**trigger, "artifact_refs": descriptors}, p["workflow_run_id"])
        except (ValueError, OSError, KeyError) as exc:
            _fail(runtime, source, event.id, str(exc), operation=outcome["operation_id"])
            return {**outcome, "status": "escalated", "applied": False, "reason": str(exc)}
        selected = runtime.event_writer.append(ZfEvent(type=REBOUND, actor="zf-cli", origin="kernel",
            causation_id=event.id, correlation_id=source.correlation_id, payload={**p, "source_event_id": source.id,
                "replacement_ref": replacement, "source_sha256": pinned["sha256"],
                "orchestrator_operation_id": outcome["operation_id"]}))
    _dispatch_rebound(runtime, selected)
    return {**outcome, "source_resolution_event_id": selected.id}


def _dispatch_rebound(runtime, selected):
    p = selected.payload
    manifest = runtime._fanout_manifest(p["fanout_id"])
    child = next((c for c in (manifest or {}).get("children", []) if c["child_id"] == p["child_id"]), {})
    if (child.get("status") != "pending" or child["payload"].get("source_rebind_event_id") != selected.id
            or runtime._fanout_identity_stale_reason(p["fanout_id"])[0] or runtime._run_fanout_dispatch_blocked(selected)):
        return
    reader = FanoutChild(child["child_id"], child["role_instance"], manifest["target_ref"], dict(child["payload"]))
    context = FanoutContext(p["fanout_id"], p["stage_id"], "fanout_reader", manifest["trace_id"],
                            manifest["trigger_event_id"], manifest["target_ref"], [reader])
    roles = runtime._fanout_roles([reader.role_instance])
    prepared = runtime._preregister_reader_fanout_operations(context=context,
        roles_by_instance={r.instance_id: r for r in roles}, causation_id=selected.id,
        aggregate=runtime._fanout_stage_by_id(p["stage_id"]).aggregate)
    for role in roles:
        if role.instance_id == reader.role_instance:
            runtime._dispatch_reader_fanout_child(context=context, child=reader, role=role,
                aggregate=runtime._fanout_stage_by_id(p["stage_id"]).aggregate,
                causation_id=selected.id, prepared_dispatch=prepared.get(reader.child_id))
