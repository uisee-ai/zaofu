"""Read the current pre-Impl revision handoff from its causal receipts."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.pause_lifecycle import is_run_dispatch_paused
from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages


def plan_revision_handoff(events: list[ZfEvent], run: str) -> dict:
    """Return public metadata only. A routing receipt cannot prove delivery."""
    if not run:
        return {}
    ready = admission = trigger = None
    fanout_id = ""
    handoff: dict = {}
    for event in events:
        p = event.payload
        if event.origin != "kernel":
            continue
        scope = p.get("workflow_run_id") or p.get("run_id") or event.correlation_id
        if (ready and event.type in {"plan.approved", "plan.rejected"} and p.get("plan_id") == ready.id
                and scope in (run, "", None)):
            ready = None
            handoff = {}
        if scope != run:
            continue
        if event.type == "task_map.ready":
            ready = event
            admission = trigger = None
            handoff, fanout_id = {}, ""
            continue
        if ready is None:
            continue
        if event.type == "orchestrator.semantic.decision.admitted" and p.get("checkpoint") == "pre_impl":
            if p.get("source_event_id") != ready.id or p.get("checkpoint_policy") != "blocking":
                continue
            handoff, fanout_id = {}, ""
            admission = trigger = None
            if (p.get("decision") == "revise" and p.get("operation_id")
                    and isinstance(p.get("decision_ref"), dict) and p["decision_ref"].get("sha256")
                    and event.causation_id and event.causation_id == p.get("decision_event_id")
                    and ready.payload.get("plan_artifact_package_digest")):
                admission = event
                handoff = {
                    "status": "awaiting_route",
                    "decision_event_id": event.causation_id, "admission_event_id": event.id,
                    "source_event_id": ready.id, "sender_operation_id": p["operation_id"],
                    "package_digest": ready.payload["plan_artifact_package_digest"],
                    "task_map_generation": ready.payload.get("task_map_generation", ""),
                }
            continue
        if not admission:
            continue
        if (event.type == "orchestrator.semantic.decision.applied" and not trigger
                and event.causation_id == handoff["decision_event_id"]
                and p.get("operation_id") == handoff["sender_operation_id"]
                and p.get("decision_ref") == admission.payload.get("decision_ref")
                and p.get("status") == "escalated" and p.get("applied") is False):
            handoff.update(status="awaiting_owner", outcome_event_id=event.id)
        elif (event.causation_id == admission.id and p.get("replan_classification") == "oa_pre_impl_revision"
              and p.get("orchestrator_decision_event_id") == handoff["decision_event_id"]
              and p.get("plan_artifact_package_digest") == handoff["package_digest"]
              and p.get("task_map_generation") == handoff["task_map_generation"]
              and p.get("revision_target_role")):
            trigger = event
            handoff.update(status="awaiting_dispatch", obligation_id="plan-author",
                           trigger_event_id=event.id, target_role=p["revision_target_role"])
        elif (event.type == "fanout.started" and trigger and p.get("trigger_event_id") == trigger.id
              and p.get("stage_id") == ready.payload.get("stage_id") and p.get("fanout_id")):
            fanout_id = p["fanout_id"]
        elif (event.type == "fanout.child.dispatched" and fanout_id and p.get("fanout_id") == fanout_id
              and p.get("role_instance") == handoff.get("target_role") and p.get("operation_id")):
            handoff.update(status="dispatched", dispatch_event_id=event.id, operation_id=p["operation_id"])
    if handoff:
        packages = reduce_plan_artifact_packages((e for e in events if e.origin == "kernel"), workflow_run_id=run)
        current = packages.get("current")
        if current and current.get("package_digest") != handoff["package_digest"]:
            return {}
        handoff["paused"] = is_run_dispatch_paused(events, run)
    return handoff
