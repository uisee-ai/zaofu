"""Bind an optional Plan coverage transfer to an accepted predecessor Run."""

from pathlib import Path

from zf.core.events.log import EventLog
from zf.runtime.run_scope import events_for_run
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def approved_team(state: Path, events: list, run_id: str, digest: str = "") -> tuple[dict, dict]:
    from zf.runtime.workflow_proposal import load_workflow_proposal

    scoped = events_for_run(events, run_id=run_id, include_legacy_single_run=False)
    accepted = {e.causation_id for e in scoped if e.type == "workflow.invoke.accepted" and e.origin == "kernel"}
    invokes = [e for e in scoped if e.id in accepted and e.type == "workflow.invoke.requested"
               and e.payload.get("workflow_proposal_ref") and e.payload.get("workflow_proposal_digest")
               and (not digest or e.payload["workflow_proposal_digest"] == digest)]
    if not invokes or len({e.payload["workflow_proposal_digest"] for e in invokes}) != 1:
        raise ValueError("Plan transfer requires its bound approved Run/Proposal")
    p = invokes[0].payload
    proposal = load_workflow_proposal(state, p["workflow_proposal_ref"])
    if (proposal["proposal_digest"] != p["workflow_proposal_digest"] or proposal["approval_status"] != "approvable"
            or proposal["effective_config_ref"] != p.get("effective_config_ref")):
        raise ValueError("Plan transfer predecessor Proposal binding mismatch")
    snap = hydrate_sidecar_ref(state, proposal["effective_config_ref"]).payload
    if not isinstance(snap, dict) or snap.get("schema_version") != "effective-config-snapshot.v1":
        raise ValueError("Plan transfer lacks its frozen predecessor config")
    return proposal, (snap["compiled_graph"].get("stage_teams") or {}).get("plan", {})


def validate_predecessors(state: Path, team: dict, *, request_id: str, revision: int, requirement: str) -> None:
    transfers = [m for m in team.get("members", []) if m.get("disposition")]
    if not transfers:
        return
    events = EventLog(state / "events.jsonl").read_all()
    for member in transfers:
        d = member["disposition"]
        proposal, previous = approved_team(state, events, d["previous_run_id"], d["previous_proposal_digest"])
        # An unchanged Requirement may start a successor Run. A revised
        # Requirement must be a later revision of the same tracked request.
        if not (requirement and proposal["requirement_spec_digest"] == requirement or
                proposal["request_id"] == request_id and proposal["request_revision"] < revision):
            raise ValueError("Plan transfer predecessor belongs to another Requirement")
        expected = {key: value for key, value in member.items() if key != "disposition"}
        if expected not in previous.get("members", []):
            raise ValueError("Plan transfer requires an active optional obligation in its predecessor")


def approved_current_transfers(runtime, stage, event) -> bool:
    from zf.core.workflow.flow_metadata import flow_metadata_for
    from zf.core.events.model import ZfEvent

    if stage.id not in {"prd-plan", "prd-scan"}:
        return True
    plan = (flow_metadata_for(runtime.config, "prd").get("stage_teams") or {}).get("plan", {})
    if not any(member.get("disposition") for member in plan.get("members", [])):
        return True
    run = event.payload.get("workflow_run_id") or event.correlation_id or ""
    events = runtime.event_log.read_all()
    try:
        _, approved = approved_team(runtime.state_dir, events, run)
        if approved != plan:
            raise ValueError("current Plan coverage transfer differs from the approved Proposal")
        return True
    except (ValueError, KeyError, OSError) as exc:
        if not any(e.type == "task.fanout.rejected" and e.causation_id == event.id and
                   e.payload.get("reason") == "plan_transfer_unapproved" for e in events):
            runtime.event_writer.append(ZfEvent(type="task.fanout.rejected", actor="zf-cli", origin="kernel",
                task_id=event.task_id, causation_id=event.id, correlation_id=event.correlation_id,
                payload={"reason": "plan_transfer_unapproved", "error": str(exc), "stage_id": stage.id,
                         "source_event_id": event.id, "workflow_run_id": run, "recovery_owner": "run_manager"}))
        return False
