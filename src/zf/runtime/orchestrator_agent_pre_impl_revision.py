"""Return a blocking pre-Impl revision to its original, package-bound Plan author."""
from __future__ import annotations

from contextlib import nullcontext
from hashlib import sha256
from typing import Any, Mapping

from zf.core.events.log import EventLedgerAdvanced
from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_admission import admit_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_policy import checkpoint_policy
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.run_admission import run_dispatch_block_reason
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


_REVISION = "oa_pre_impl_revision"
_BINDING = ("plan_artifact_package_id", "plan_artifact_package_ref", "plan_artifact_package_digest",
            "task_map_generation")
_SCAN_INPUTS = (
    "fanout_id", "stage_id", "trace_id", "stage_member_results", "target_ref", "pdd_id", "feature_id",
    "stage_replan_generation", "stage_replan_generation_source_event_id", "source_refs", "source_ref",
    "request_id", "request_revision", "workflow_request_ref", "workflow_generation", "flow_kind", "goal_id",
    "request_kind", "completion_profile", "workflow_proposal_ref", "workflow_proposal_digest",
    "effective_config_ref", "effective_config_digest", "run_contract_ref", "run_contract_digest", "run_id",
    "requirement_spec_ref", "requirement_spec_digest", "prd_ref", "workflow_input_manifest_ref",
    "workflow_input_manifest_digest", "workflow_prompt_ref", "artifact_refs", "evidence_refs",
)


def reconcile_pre_impl_revision(
    runtime: Any, submitted_event: ZfEvent, *, apply: bool = False,
    escalation_event_id: str = "", approval_ref: str = "",
) -> dict[str, Any]:
    """Preview or apply the admitted decision; historical reconciliation needs explicit authorization."""
    if escalation_event_id and apply and not approval_ref.strip():
        raise ValueError("applying historical pre_impl recovery requires an operator approval reference")
    run = str(submitted_event.payload.get("workflow_run_id") or "")
    key = sha256(run.encode()).hexdigest()
    with (locked_path(runtime.state_dir / "locks" / f"oa-pre-impl-revision-{key}") if apply else nullcontext()):
        for attempt in range(3):
            try:
                return _reconcile(runtime, submitted_event, apply, escalation_event_id, approval_ref)
            except EventLedgerAdvanced:
                if attempt == 2:
                    raise


def _reconcile(
    runtime: Any, submitted_event: ZfEvent, apply: bool, escalation_event_id: str, approval_ref: str,
) -> dict[str, Any]:
    offset = runtime.event_log.current_offset() if apply else None
    events = runtime.event_log.read_all()
    index = {event.id: event for event in events}
    raw = index.get(submitted_event.id)
    if raw is None or raw.type != "orchestrator.semantic.decision.submitted":
        raise ValueError("submitted OA decision missing")
    if escalation_event_id:
        from zf.runtime.orchestrator_agent_pre_impl_recovery import escalated_pre_impl_submission

        if escalated_pre_impl_submission(events, escalation_event_id).id != raw.id:
            raise ValueError("historical escalation belongs to another decision")
    submitted = hydrate_profiled_control_result_event(runtime.state_dir, raw)
    history = [e for e in events if e.origin == "kernel" and e.causation_id == raw.id
               and e.payload.get("decision_event_id") == raw.id]
    admitted = next((e for e in history if e.type == "orchestrator.semantic.decision.admitted"
                     and e.payload.get("checkpoint") == "pre_impl" and e.payload.get("decision") == "revise"
                     and e.payload.get("checkpoint_policy") == "blocking"), None)
    if not admitted:
        raise ValueError("missing admitted blocking pre_impl revision")
    prior = [e.payload for e in history if e.type == "orchestrator.semantic.decision.applied"]
    if prior and prior[-1].get("applied") and prior[-1].get("revision_trigger_event_id"):
        return dict(prior[-1])
    if prior and not escalation_event_id:
        raise ValueError("historical pre_impl outcome requires its original escalation")
    published = next((e for e in events if e.origin == "kernel" and e.causation_id == admitted.id
                      and e.payload.get("replan_classification") == _REVISION
                      and e.payload.get("orchestrator_decision_event_id") == raw.id), None)
    base = dict(admitted.payload)
    if published:
        trigger = published
    else:
        admission = admit_orchestrator_agent_decision(runtime, submitted)
        if not admission.admitted:
            raise ValueError(admission.reason)
        if (admission.checkpoint != "pre_impl" or admission.checkpoint_policy != "blocking"
                or admission.decision_ref != base["decision_ref"]
                or admission.source_event_id != base["source_event_id"]
                or admission.operation_id != base["operation_id"]):
            raise ValueError("admission no longer matches the blocking pre_impl decision")
        decision = admission.decision
        if hydrate_sidecar_ref(runtime.state_dir, admission.decision_ref).payload != decision:
            raise ValueError("revision differs from original admitted decision")
        trigger = _planner_trigger(runtime, events, index, admitted, decision)
        if apply:
            delta_ref = write_immutable_json_sidecar(runtime.state_dir, decision["delta"],
                root="orchestrator-agent/deltas", kind="orchestration_delta", schema_version="orchestration-delta.v1",
                created_by="orchestrator-agent-admission", source_event_id=raw.id)
            trigger.payload.update(orchestration_delta_ref=delta_ref["ref"], orchestration_delta_digest=delta_ref["sha256"])
            if escalation_event_id:
                trigger.payload.update(recovery_escalation_event_id=escalation_event_id, recovery_approval_ref=approval_ref)
            runtime.event_writer.append(trigger, expected_offset=offset,
                allow_intervening=frozenset({"worker.heartbeat", "agent.text"}))
    payload = trigger.payload
    result = {**base, **{k: payload[k] for k in (*_BINDING, "revision_target_stage", "revision_target_role")},
              "revision_trigger_event_id": trigger.id if (apply or published) else "",
              "revision_trigger_type": trigger.type, "required_actions": payload["required_actions"],
              "status": "applied" if apply or published else "preview", "applied": bool(apply or published)}
    if escalation_event_id and apply:
        result.update(recovery_escalation_event_id=escalation_event_id, recovery_approval_ref=approval_ref)
        runtime.event_writer.append(ZfEvent(type="orchestrator.semantic.decision.applied", actor="zf-cli", origin="kernel",
            causation_id=raw.id, correlation_id=raw.correlation_id, payload=result))
    return result


def _planner_trigger(
    runtime: Any, events: list[ZfEvent], index: Mapping[str, ZfEvent],
    admitted: ZfEvent, decision: Mapping[str, Any],
) -> ZfEvent:
    from zf.runtime.orchestrator_agent_decision_apply import _directive_ref_rejection, _operation_source_descriptors
    from zf.runtime.workflow_config_consumer import approved_config_consumer_error

    base = admitted.payload
    run = base["workflow_run_id"]
    if not run or run != decision["identity"]["workflow_run_id"]:
        raise ValueError("pre_impl revision Run mismatch")
    block = run_dispatch_block_reason(runtime, run_id=run)
    if block or is_dispatch_paused(runtime.state_dir, events=events):
        raise ValueError(block or "dispatch_paused")
    ready = index.get(base["source_event_id"])
    if (not ready or ready.origin != "kernel" or ready.type != "task_map.ready"
            or (ready.payload.get("workflow_run_id") or ready.correlation_id) != run
            or any(not ready.payload.get(k) or ready.payload[k] != decision["identity"].get(k) for k in _BINDING if k != "plan_artifact_package_id")
            or (decision["identity"].get("plan_artifact_package_id") and
                decision["identity"]["plan_artifact_package_id"] != ready.payload.get("plan_artifact_package_id"))):
        raise ValueError("pre_impl revision requires the exact Plan package and task_map.ready")
    if (checkpoint_policy(runtime.config, "pre_impl", flow_kind=ready.payload.get("flow_kind", "")) != "blocking"
            or approved_config_consumer_error(runtime.state_dir, runtime.config, ready.payload)):
        raise ValueError("current configuration does not authorize the original blocking pre_impl route")
    from zf.runtime.orchestrator_agent_plan_binding import current_checkpoint_plan_package

    current = current_checkpoint_plan_package(events, workflow_run_id=run)
    if current.get("package_digest") != ready.payload["plan_artifact_package_digest"]:
        raise ValueError("the original Plan package has been superseded")
    scoped = [e for e in events if (e.payload.get("workflow_run_id") or e.correlation_id) == run]
    if any(_plan_decided(e, ready.id, run) for e in events[events.index(ready) + 1:]):
        raise ValueError("the owner has already decided this Plan")
    if any((e.type == "orchestrator.run_plan.admitted"
               and e.payload.get("task_map_generation") == ready.payload["task_map_generation"])
           or (e.type == "orchestrator.semantic.decision.admitted" and e.id != admitted.id
               and e.payload.get("checkpoint") == "pre_impl" and e.payload.get("source_event_id") == ready.id)
           for e in scoped[scoped.index(ready) + 1:]):
        raise ValueError("Plan already has a superseding decision")
    package = hydrate_plan_artifact_package(runtime.state_dir,
        {"ref": ready.payload["plan_artifact_package_ref"], "sha256": ready.payload["plan_artifact_package_digest"]})
    if ready.payload.get("plan_artifact_package_id") != "planpkg-" + ready.payload["plan_artifact_package_digest"]:
        raise ValueError("Plan package id differs from the bound package")
    stage = next((s for s in runtime.config.workflow.stages if s.id == package["producer_stage_id"]), None)
    if (not stage or stage.id != ready.payload.get("stage_id") or stage.aggregate.success_event != ready.type
            or stage.topology != "fanout_reader" or len(stage.roles) != 1):
        raise ValueError("Plan producer must be a registered single-author reader stage")
    successors = scoped[scoped.index(ready) + 1:]
    if any(e.origin == "kernel" and (e.type == stage.trigger or
           (e.type == "fanout.started" and e.payload.get("stage_id") == stage.id))
           for e in successors):
        raise ValueError("a successor Plan has already been requested")
    author = stage.roles[0]
    prior = scoped[:scoped.index(ready)]
    fanout = next((e for e in reversed(prior) if e.type == "fanout.started" and e.origin == "kernel"
                   and e.payload.get("stage_id") == stage.id and e.payload.get("fanout_id") == ready.payload.get("fanout_id")), None)
    receipt = index.get(fanout.payload.get("trigger_event_id")) if fanout else None
    if (not receipt or receipt not in prior or prior.index(receipt) >= prior.index(fanout)
            or receipt.type != stage.trigger or not _trusted_scan_receipt(events, receipt, fanout)
            or [c.get("role_instance") for c in fanout.payload.get("expected_children", [])] != [author]):
        raise ValueError("original Plan author or upstream receipt cannot be established")
    if stage.children and receipt.type == "prd.scan.completed":
        obligation = stage.children[0].payload.get("stage_obligation")
        if obligation:
            from zf.runtime.fanout import FanoutManifestProjector
            from zf.runtime.plan_team_handoff import _plan_inputs_match

            scan_id = receipt.payload.get("fanout_id")
            if not scan_id:
                raise ValueError("missing original Plan team scan")
            manifest = FanoutManifestProjector(runtime.state_dir).rebuild(scan_id, prior)
            if manifest.get("stage_id") != "prd-scan" or not _plan_inputs_match(obligation, manifest, receipt.payload):
                raise ValueError("original Plan team scan receipts incomplete")
    directives = (decision.get("delta") or {}).get("directives") or []
    if not directives:
        raise ValueError("pre_impl requires a revision directive")
    actions = []
    allowed_refs = _operation_source_descriptors(runtime, base)
    for row in directives:
        if row.get("action") != "revise" or row.get("target") != {"role_instance": author, "stage_id": stage.id}:
            raise ValueError("revision must target the original Plan author and stage")
        reason = _directive_ref_rejection(row, allowed_refs)
        if reason:
            raise ValueError(reason)
        req = row.get("required_actions")
        if not isinstance(req, list) or not req or not all(isinstance(a, str) and a.strip() for a in req):
            raise ValueError("each revision directive requires concrete actions")
        actions.extend(req)
    policy = runtime.config.workflow.orchestration
    revisions = [e for e in scoped if e.origin == "kernel" and e.payload.get("replan_classification") == _REVISION
                 and e.type == stage.trigger]
    if (len(revisions) >= policy.max_plan_revisions or
            sum(r.payload.get("revision_fingerprint") == base["decision_fingerprint"] for r in revisions) >= policy.no_progress_limit):
        raise ValueError("pre_impl Plan revision budget exhausted")
    payload = {k: v for k, v in receipt.payload.items() if k in _SCAN_INPUTS}
    historical_owner = {k: receipt.payload[k] for k in ("owner_confirmation", "owner_confirmation_ref",
                        "owner_decision_items", "owner_decision_resolution") if receipt.payload.get(k)}
    if historical_owner:
        payload["historical_orchestration_context"] = {
            "authority": "observation_only", "source_event_id": receipt.id, **historical_owner,
        }
    payload.update({k: ready.payload[k] for k in (*_BINDING, "task_map_ref", "task_map_digest",
                    "goal_claim_set_ref", "goal_claim_set_digest") if ready.payload.get(k)})
    lineage = {"reviewed_plan_fanout_id": fanout.payload["fanout_id"], "upstream_trigger_event_id": receipt.id}
    for key in ("stage_replan_generation", "stage_replan_generation_source_event_id"):
        if receipt.payload.get(key):
            lineage[key] = receipt.payload[key]
    if receipt.payload.get("fanout_id"):
        lineage["upstream_fanout_id"] = receipt.payload["fanout_id"]
    payload.update(workflow_run_id=run, rework_of=ready.id, rework_attempt=len(revisions) + 1, rework_summary=lineage,
        rework_source=admitted.type, replan_classification=_REVISION, required_actions=actions,
        rework_feedback=actions, orchestrator_decision_ref=base["decision_ref"],
        orchestrator_decision_event_id=base["decision_event_id"], revision_fingerprint=base["decision_fingerprint"],
        revision_target_stage=stage.id, revision_target_role=author)
    trigger = ZfEvent(type=stage.trigger, actor="zf-cli", origin="kernel", payload=payload,
                      causation_id=admitted.id, correlation_id=ready.correlation_id or run)
    trigger.payload.update(revision_trigger_event_id=trigger.id, revision_trigger_type=stage.trigger)
    return trigger


def _trusted_scan_receipt(events: list[ZfEvent], receipt: ZfEvent, fanout: ZfEvent) -> bool:
    if receipt.origin == "kernel":
        return True
    if (receipt.origin or receipt.actor != "zf-cli" or not receipt.causation_id
            or any((fanout.payload.get("trigger_payload") or {}).get(k) != v for k, v in receipt.payload.items())):
        return False
    # Older operator-authorized retries use an empty origin. Reconstruct their
    # completed controlled action and its bound goal update and escalation.
    from zf.runtime.run_scope import run_aliases
    from zf.runtime.stage_replan_generation import completed_generation_source

    prior = {}
    rows = events[:events.index(fanout)]
    aliases = run_aliases(rows)
    for event in rows:
        if (event.causation_id == receipt.id and event.payload.get("stage_trigger_event_id") == receipt.id
                and completed_generation_source(event, prior, aliases)):
            return True
        prior[event.id] = event
    return False

def _plan_decided(event: ZfEvent, ready_id: str, run: str) -> bool:
    if event.origin != "kernel" or event.type not in {"plan.approved", "plan.rejected"}:
        return False
    scope = event.payload.get("workflow_run_id") or event.payload.get("run_id") or event.correlation_id
    return event.payload.get("plan_id") == ready_id and (not scope or scope == run)


def is_pre_impl_revision_trigger(event: ZfEvent, config: Any = None) -> bool:
    """Distinguish an attempted directed admission from ordinary downstream events."""
    p = event.payload if isinstance(event.payload, dict) else {}
    if (p.get("replan_classification") == _REVISION and
            (p.get("revision_trigger_event_id") == event.id or p.get("revision_trigger_type") == event.type)):
        return True
    # A malformed or copied envelope on the original input event still claims
    # authority. Do not turn it into an ordinary broadcast by dropping fields.
    if not (p.get("replan_classification") == _REVISION or "revision_target_stage" in p or "revision_target_role" in p):
        return False
    return bool(config and any(s.trigger == event.type and s.aggregate.success_event == "task_map.ready"
                               for s in config.workflow.stages))


def pre_impl_revision_stale_reason(
    runtime: Any, trigger: ZfEvent, stage_id: str, *, events: list[ZfEvent] | None = None,
) -> str:
    """Authorize consumption against the exact decision and the still-current Plan."""
    if not is_pre_impl_revision_trigger(trigger, runtime.config):
        return ""
    p = trigger.payload
    if (p.get("replan_classification") != _REVISION or trigger.origin != "kernel" or trigger.actor != "zf-cli" or not trigger.causation_id
            or p.get("revision_target_stage") != stage_id or p.get("revision_trigger_event_id") != trigger.id):
        return "unauthorized_pre_impl_revision"
    run = p.get("workflow_run_id")
    role = p.get("revision_target_role")
    stage = next((s for s in runtime.config.workflow.stages if s.id == stage_id), None)
    if (not run or not role or not stage or stage.topology != "fanout_reader"
            or stage.trigger != trigger.type or stage.roles != [role]
            or (stage.children and [c.role_instance for c in stage.children] != [role])):
        return "pre_impl_revision_target_changed"
    events = events if events is not None else runtime.event_log.read_all()
    index = {e.id: e for e in events}
    if index.get(trigger.id) != trigger:
        return "unpublished_pre_impl_revision"
    admission = index.get(trigger.causation_id)
    ready = index.get(p.get("rework_of"))
    if (not admission or not ready or ready.type != "task_map.ready" or ready.origin != "kernel"
            or ready.payload.get("stage_id") != stage_id or (ready.payload.get("workflow_run_id") or ready.correlation_id) != run
            or any(ready.payload.get(k) != p.get(k) or not p.get(k) for k in _BINDING)
            or admission.type != p.get("rework_source") or admission.type != "orchestrator.semantic.decision.admitted"
            or admission.origin != "kernel" or admission.actor != "zf-cli"
            or admission.causation_id != p.get("orchestrator_decision_event_id")
            or any(admission.payload.get(k) != v for k, v in {
                "decision_event_id": admission.causation_id, "source_event_id": ready.id, "workflow_run_id": run,
                "checkpoint": "pre_impl", "checkpoint_policy": "blocking", "decision": "revise",
                "decision_ref": p.get("orchestrator_decision_ref"),
            }.items())):
        return "invalid_pre_impl_revision_lineage"
    try:
        decision = hydrate_sidecar_ref(runtime.state_dir, admission.payload["decision_ref"]).payload
    except (ValueError, OSError, KeyError, TypeError):
        return "invalid_pre_impl_revision_decision"
    directives = (decision.get("delta") or {}).get("directives") or []
    if (not directives or any(row.get("target") != {"stage_id": stage_id, "role_instance": role}
                              or row.get("action") != "revise" for row in directives)):
        return "pre_impl_revision_target_changed"
    from zf.runtime.orchestrator_agent_plan_binding import current_checkpoint_plan_package

    current = current_checkpoint_plan_package(events, workflow_run_id=run)
    if current.get("package_digest") != p["plan_artifact_package_digest"]:
        return "pre_impl_revision_package_superseded"
    for e in events[events.index(ready) + 1:]:
        if _plan_decided(e, ready.id, run):
            return "pre_impl_revision_owner_decided"
        other = e.payload
        if e.origin != "kernel" or (other.get("workflow_run_id") or e.correlation_id) != run:
            continue
        if (e.type == "orchestrator.run_plan.admitted" and other.get("task_map_generation") == p["task_map_generation"]
                or e.type == admission.type and e.id != admission.id and other.get("checkpoint") == "pre_impl"
                and other.get("source_event_id") == ready.id
                or e.type == "fanout.started" and other.get("stage_id") == stage_id and other.get("trigger_event_id") != trigger.id
                or e.type == stage.trigger and e.id != trigger.id):
            return "pre_impl_revision_successor_started"
    if is_dispatch_paused(runtime.state_dir, events=events):
        return "dispatch_paused"
    return run_dispatch_block_reason(runtime, run_id=run)

def start_current_revision_fanout(runtime: Any, trigger: ZfEvent, started: ZfEvent) -> bool:
    """Serialize the initial fanout receipt against admission, approval and package changes."""
    stage_id = started.payload["stage_id"]
    roles = [c.get("role_instance") for c in started.payload.get("expected_children", [])]
    if roles != [trigger.payload.get("revision_target_role")]:
        return False
    for _ in range(3):
        offset = runtime.event_log.current_offset()
        events = runtime.event_log.read_all()
        if pre_impl_revision_stale_reason(runtime, trigger, stage_id, events=events) or any(
            e.origin == "kernel" and e.type == "fanout.started" and e.payload.get("stage_id") == stage_id
            and e.payload.get("trigger_event_id") == trigger.id for e in events
        ):
            return False
        try:
            runtime.event_writer.append(started, expected_offset=offset, allow_intervening=frozenset({"worker.heartbeat", "agent.text"}))
            return True
        except EventLedgerAdvanced:
            continue
    return False  # Still unstarted; the ordinary recovery sweep re-evaluates it.


def revision_child_dispatch_allowed(runtime: Any, context: Any, child: Any, role: Any) -> bool:
    """Recover an unsent Plan author only while its original routing receipt is current."""
    body = child.payload.get("trigger_payload") or {}
    if body.get("revision_trigger_event_id") != context.trigger_event_id or body.get("replan_classification") != _REVISION:
        return True
    events = runtime.event_log.read_all()
    trigger = next((e for e in events if e.id == context.trigger_event_id), None)
    return bool(trigger and role.instance_id == trigger.payload.get("revision_target_role")
                and not pre_impl_revision_stale_reason(runtime, trigger, context.stage_id, events=events))
