"""Check the same pinned mandatory claims before Critic dispatch and Plan admission."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.goal_claim_set import build_goal_claim_set, mandatory_claim_continuity, read_goal_objective
from zf.runtime.plan_requirement_amendment import amended_claim_set_baseline, amendment_binding
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def plan_objective_ref(payload: Mapping) -> str:
    return str(payload.get("requirement_spec_ref") or payload.get("objective_ref")
               or payload.get("prd_ref") or payload.get("product_spec_ref") or payload.get("issue_ref") or "")


def package_claim_continuity(
    state_dir: Path, package: Mapping, current: Mapping, *, workflow_run_id: str,
    goal_id: str, payload: Mapping, events: list, task_map: Mapping,
) -> dict:
    port = next((p for p in [*package["produced"], *package["inherited"]]
                 if p["logical_name"] == "goal_claim_set"), None)
    body = hydrate_sidecar_ref(state_dir, port).payload if port else None
    previous = body if isinstance(body, Mapping) else None
    waivers = _goal_claim_waivers(payload)
    from zf.runtime.plan_derived_claim_correction import corrected_claim_baseline, correction_origin_errors

    previous, changed = corrected_claim_baseline(
        previous, package, events, workflow_run_id, payload.get("stage_replan_generation"),
    )
    result = mandatory_claim_continuity(
        amended_claim_set_baseline(state_dir, workflow_run_id, previous, events=events),
        current, claim_waivers=waivers,
        active_waiver_refs=_active_goal_claim_waiver_refs(events, goal_id=goal_id) if waivers else (),
    )
    failed = {r["goal_claim_id"] for r in result["findings"]}
    result["findings"].extend(r for r in correction_origin_errors(changed, current, task_map)
                              if r["goal_claim_id"] not in failed)
    if result["findings"]:
        result["status"] = "rejected"
    return result


def candidate_claim_errors(
    state_dir: Path, project_root: Path, package: Mapping, task_map: Mapping, trigger: Mapping,
) -> list[dict[str, str]]:
    if not any(p["logical_name"] == "goal_claim_set" for p in [*package["produced"], *package["inherited"]]):
        return []
    run_id = package["workflow_run_id"]
    goal_id = str(trigger.get("goal_id") or run_id)
    log = EventLog(state_dir / "events.jsonl")
    try:
        events = log.read_all()
    finally:
        log.close()
    payload = {**trigger, **amendment_binding(state_dir, run_id, events=events)}
    objective = plan_objective_ref(payload)
    current = build_goal_claim_set(
        task_map, workflow_run_id=run_id, goal_id=goal_id, task_map_generation="",
        objective_ref=objective, objective=read_goal_objective(state_dir, project_root, objective),
    )
    result = package_claim_continuity(state_dir, package, current, workflow_run_id=run_id,
                                      goal_id=goal_id, payload=payload, events=events, task_map=task_map)
    return [{"code": row["code"], "field": f"task_map.goal_claims[{row['goal_claim_id']}]",
             "message": f"Preserve the bound mandatory claim: {row['previous_text']}"}
            for row in result["findings"]]


def _goal_claim_waivers(payload: Mapping[str, Any]) -> dict[str, str]:
    raw = payload.get("goal_claim_waivers")
    rows = raw if isinstance(raw, list) else []
    return {
        str(item.get("goal_claim_id") or "").strip(): str(
            item.get("waiver_ref") or ""
        ).strip()
        for item in rows
        if isinstance(item, Mapping)
        and str(item.get("goal_claim_id") or "").strip()
        and str(item.get("waiver_ref") or "").strip()
    }


def _active_goal_claim_waiver_refs(
    events: list[ZfEvent | Mapping[str, Any]],
    *,
    goal_id: str,
) -> set[str]:
    from zf.runtime.waivers import active_waivers

    active = active_waivers(
        [event for event in events if isinstance(event, ZfEvent)],
        goal_id,
    )
    return {
        str(value)
        for waiver in active
        for value in (waiver.get("signature"), waiver.get("event_id"))
        if str(value).strip()
    }
