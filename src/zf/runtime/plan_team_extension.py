"""Admit a reserved Plan specialist against fresh evidence in the active scan."""

from __future__ import annotations

from typing import TYPE_CHECKING

from zf.core.config.stage_teams import active_members, member_child, team_member
from zf.core.events.model import ZfEvent
from zf.core.state.locks import locked_path
from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.runtime.fanout import FanoutChild, FanoutContext, FanoutManifestProjector
from zf.runtime.plan_team_handoff import plan_member_results
from zf.runtime.plan_team_transfer import approved_team

if TYPE_CHECKING:
    from zf.runtime.control_actions import ControlledActionService
    from zf.runtime.orchestrator import Orchestrator

_REQUEST = "fanout.plan.extension.requested"
_ADMIT = "fanout.plan.member.admitted"
_REJECT = "fanout.plan.extension.rejected"
_BINDINGS = ("workflow_run_id", "fanout_id", "workflow_proposal_digest", "evidence_event_id")


def request_extension(service: ControlledActionService, requested: ZfEvent, action: str,
                      requested_action: str, payload: dict) -> dict:
    """The existing authenticated request-fanout surface can exercise approved reserve seats."""
    try:
        if payload.get("stage_id") != "prd-scan" or not all(
                isinstance(payload.get(k), str) and payload[k].strip() for k in _BINDINGS):
            raise ValueError("Plan extension requires the scan, Run, Proposal digest and evidence event")
        member = team_member(payload["plan_member"], stage="plan")
        if member.get("disposition") or not member.get("selection_reason") or not member.get("completion_condition"):
            raise ValueError("Plan extension requires an active obligation, selection_reason and completion_condition")
    except (ValueError, TypeError) as exc:
        return service._failed(requested=requested, action=action, requested_action=requested_action,
                               task_id=None, reason=str(exc))
    decision = {**{k: payload[k] for k in _BINDINGS}, "stage_id": "prd-scan", "member": member}
    with locked_path(service.state_dir / "locks" / "plan-team-extension-request"):
        events = service.writer.event_log.read_all()
        originals = {e.id: e for e in events if e.origin == "kernel" and e.type == _REQUEST and e.payload == decision}
        receipts = {e.causation_id: e for e in events if e.origin == "kernel" and e.type in {_ADMIT, _REJECT}
                    and e.causation_id in originals}
        status = "requested"
        event = next((r for r in receipts.values() if r.type == _ADMIT), None)
        if event:
            status = "already_admitted"
        else:
            event = next((r for key, r in originals.items() if key not in receipts), None)
        if event is None:
            event = service.writer.append(ZfEvent(type=_REQUEST, origin="kernel", actor=service.actor,
                correlation_id=payload["workflow_run_id"], causation_id=requested.id, payload=decision))
    service._completed(requested=requested, event=event, action=action, requested_action=requested_action,
                       status=status, task_id=None)
    return {"_status_code": 200 if status == "already_admitted" else 202, "ok": True, "status": status,
            "action": action, "event_id": event.id, "fanout_id": payload["fanout_id"]}



def _authorize(runtime: Orchestrator, event: ZfEvent, events: list[ZfEvent]) -> dict:
    p = event.payload
    run, scan, digest, evidence_id = (p[k] for k in _BINDINGS)
    _, plan = approved_team(runtime.state_dir, events, run, digest)
    configured = (flow_metadata_for(runtime.config, "prd").get("stage_teams") or {}).get("plan")
    if not plan or plan != configured:
        raise ValueError("Plan team differs from the approved Run config")
    member = team_member(p["member"], stage="plan")
    if (member["role"] not in plan.get("reserve_roles", []) or member.get("disposition")
            or member.get("coverage_owner", plan["author"]) != plan["author"]):
        raise ValueError("Role or coverage is outside the approved Plan reserve")
    allowed = {ref for m in plan["members"] if not m.get("disposition") for ref in m["input_refs"]}
    if not set(member["input_refs"]) <= allowed:
        raise ValueError("New member inputs exceed the approved Plan scope")
    manifest = FanoutManifestProjector(runtime.state_dir).rebuild(scan, events)
    starts = [e for e in events if e.origin == "kernel" and e.type == "fanout.started"
              and e.payload.get("stage_id") == "prd-scan" and (e.payload.get("workflow_run_id") or e.correlation_id) == run]
    if (not starts or starts[-1].payload.get("fanout_id") != scan or manifest.get("status") != "started"
            or manifest.get("aggregate", {}).get("status") != "pending" or runtime._fanout_identity_stale_reason(scan)[0]
            or runtime._run_fanout_dispatch_blocked(event)):
        raise ValueError("The named Plan scan is no longer open for this Run")
    start = starts[-1]
    original = start.payload["expected_children"]
    actual_charters = [c.get("payload", {}).get("stage_obligation") for c in original]
    expected_charters = [member_child(m, plan["author"])["payload"]["stage_obligation"] for m in active_members(plan)]
    if actual_charters != expected_charters:
        raise ValueError("Running scan members differ from the approved Plan obligations")
    prior = {m["obligation_id"] for m in plan["members"]} | {"plan-author"}
    if (member["obligation_id"] in prior or any(
            c["role_instance"] == member["role"] or
            (c.get("payload", {}).get("stage_obligation") or {}).get("obligation_id") == member["obligation_id"]
            for c in manifest["children"])):
        raise ValueError("Plan obligation or reserve seat is already assigned")
    evidence = next((e for e in events if e.id == evidence_id and e.origin == "kernel"
                     and e.type == "fanout.child.completed" and e.payload.get("fanout_id") == scan
                     and (e.payload.get("workflow_run_id") or e.correlation_id) == run), None)
    receipt = next((r for r in plan_member_results(manifest) if evidence
                    and r["child_id"] == evidence.payload.get("child_id")
                    and r["result_event_id"] == evidence.payload.get("result_event_id")
                    and isinstance(evidence.payload.get("report"), dict) and evidence.payload["report"]), None)
    if not receipt:
        raise ValueError("New evidence must be a current admitted member report in this scan")
    # Original pre-dispatch metadata contains stage/Run lineage. Copy only shared inputs,
    # never an operation, previous attempt, role-specific scope or role-specific receipt.
    template = original[0]["payload"]
    shared_keys = ("trigger_payload", "workflow_run_id", "task_id", "parent_task_id", "parent_operation_id",
                   "attempt_domain", "result_semantics", "request_id", "request_revision", "requirement_spec_digest",
                   "workflow_input_manifest_ref", "workflow_prompt_ref", "workflow_proposal_ref", "workflow_proposal_digest",
                   "effective_config_ref", "effective_config_digest", "run_contract_ref", "run_contract_digest",
                   "target_snapshot_ref", "target_snapshot_digest", "target_commit", "durable_operation",
                   "result_protocol_mode", "output_profile_id", "output_profile_revision")
    shared = {key: template[key] for key in shared_keys if key in template}
    child = member_child(member, plan["author"])["payload"]
    child.update(shared, plan_extension_evidence=receipt, plan_extension_evidence_event_id=evidence_id)
    return {**{k: p[k] for k in _BINDINGS}, "stage_id": "prd-scan", "member": child["stage_obligation"],
            "child_id": FanoutContext.child_id(member["role"]), "role_instance": member["role"],
            "child_payload": child}


def reconcile_request(runtime: Orchestrator, event: ZfEvent) -> None:
    if event.origin != "kernel" or not event.causation_id:
        return
    events = runtime.event_log.read_all()
    if any(e.origin == "kernel" and e.type in {_ADMIT, _REJECT} and e.causation_id == event.id for e in events):
        return
    try:
        payload = _authorize(runtime, event, events)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        runtime.event_writer.append(ZfEvent(type=_REJECT, origin="kernel", actor="zf-cli", causation_id=event.id,
            correlation_id=event.correlation_id, payload={**{k: event.payload.get(k) for k in _BINDINGS},
                "stage_id": "prd-scan", "reason": str(exc), "recovery_owner": "orchestrator"}))
        return
    admitted = runtime.event_writer.append(ZfEvent(type=_ADMIT, origin="kernel", actor="zf-cli",
        causation_id=event.id, correlation_id=event.correlation_id, payload=payload))
    _dispatch(runtime, admitted)


def _dispatch(runtime: Orchestrator, admitted: ZfEvent) -> None:
    p = admitted.payload
    # A restart may follow the ledger append before its manifest projection reached disk.
    manifest = FanoutManifestProjector(runtime.state_dir).write_manifest(p["fanout_id"], runtime.event_log.read_all())
    child = next((c for c in manifest.get("children", []) if c.get("extension_event_id") == admitted.id), {})
    if (child.get("status") != "pending" or manifest.get("status") != "started"
            or runtime._fanout_identity_stale_reason(p["fanout_id"])[0] or runtime._run_fanout_dispatch_blocked(admitted)):
        return
    roles = [r for r in runtime.config.roles if r.instance_id == p["role_instance"] and r.role_kind == "reader"]
    if len(roles) != 1:
        runtime.event_writer.append(ZfEvent(type="fanout.child.failed", origin="kernel", actor="zf-cli",
            causation_id=admitted.id, correlation_id=admitted.correlation_id, payload={
                "fanout_id": p["fanout_id"], "stage_id": "prd-scan", "workflow_run_id": p["workflow_run_id"],
                "child_id": child["child_id"], "role_instance": child["role_instance"], "status": "failed",
                "reason": "approved_plan_reserve_role_unavailable", "recovery_owner": "orchestrator"}))
        runtime._evaluate_reader_fanout(p["fanout_id"])
        return
    reader = FanoutChild(child["child_id"], child["role_instance"], manifest["target_ref"], dict(child["payload"]))
    context = FanoutContext(manifest["fanout_id"], "prd-scan", "fanout_reader", manifest["trace_id"],
                            manifest["trigger_event_id"], manifest["target_ref"], [reader])
    runtime._dispatch_reader_fanout_child(context=context, child=reader, role=roles[0],
        aggregate=runtime._fanout_stage_by_id("prd-scan").aggregate, causation_id=admitted.id)


def resume_extensions(runtime: Orchestrator, events: list[ZfEvent]) -> None:
    if not any(s.id == "prd-scan" for s in runtime.config.workflow.stages):
        return
    plan = (flow_metadata_for(runtime.config, "prd").get("stage_teams") or {}).get("plan") or {}
    if not plan.get("reserve_roles"):
        return
    # Reuse the session event window already loaded by the normal recovery sweep.
    settled = {e.causation_id for e in events if e.origin == "kernel" and e.type in {_ADMIT, _REJECT}}
    dispatched = {(e.payload.get("fanout_id"), e.payload.get("child_id")) for e in events
                  if e.origin == "kernel" and e.type in {"fanout.child.dispatched", "fanout.child.completed", "fanout.child.failed"}}
    for event in events:
        if event.origin != "kernel":
            continue
        if event.type == _REQUEST and event.id not in settled:
            reconcile_request(runtime, event)
        elif event.type == _ADMIT and (event.payload["fanout_id"], event.payload["child_id"]) not in dispatched:
            _dispatch(runtime, event)
