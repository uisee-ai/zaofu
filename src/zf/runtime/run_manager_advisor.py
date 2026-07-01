"""Proposal-only Run Manager replan advisor."""

from __future__ import annotations

from typing import Any


FORBIDDEN_ADVISOR_EVENTS = [
    "task_map.ready",
    "candidate.ready",
    "workflow.resume.applied",
]


def build_replan_advisor_projection(
    events: list[Any],
    *,
    no_progress: dict[str, Any],
    completion_profile: dict[str, Any],
    repair_ledger: dict[str, Any],
) -> dict[str, Any]:
    recommendations = []
    for item in no_progress.get("items") or []:
        if not isinstance(item, dict):
            continue
        recommendations.append(_recommendation(
            kind="no_progress_replan_advice",
            reason="same fingerprint crossed no-progress threshold",
            fingerprint=str(item.get("fingerprint") or ""),
            source_event_id=str(item.get("event_id") or ""),
            recommended_route="reflection_replan_advisor",
            verification_plan=[
                "read source events from run_context_bundle",
                "produce replan.proposal.md/json",
                "operator or Run Manager must approve any active workflow mutation",
            ],
        ))
    blockers = completion_profile.get("blockers") or []
    if "action_verify_failed" in blockers:
        recommendations.append(_recommendation(
            kind="verify_failure_advice",
            reason="completion is blocked by open run.manager.action.verify.failed",
            fingerprint="",
            source_event_id=str((completion_profile.get("open_verify_failures") or [{}])[-1].get("event_id") or ""),
            recommended_route="autoresearch_or_reflection",
            verification_plan=[
                "inspect expected downstream event mismatch",
                "propose deterministic resume fix or task_map/source_refs correction",
            ],
        ))
    if int((repair_ledger.get("summary") or {}).get("blocked") or 0) > 0:
        recommendations.append(_recommendation(
            kind="repair_blocked_advice",
            reason="repair ledger has blocked fingerprints",
            fingerprint="",
            source_event_id="",
            recommended_route="human_or_backlog",
            verification_plan=[
                "review repair ledger item next_allowed_action",
                "create backlog candidate if harness behavior must change",
            ],
        ))
    return {
        "schema_version": "run-manager.replan-advisor.v1",
        "is_derived_projection": True,
        "authority": "proposal_only",
        "forbidden_events": FORBIDDEN_ADVISOR_EVENTS,
        "summary": {
            "recommendations": len(recommendations),
            "needs_operator_review": bool(recommendations),
        },
        "recommendations": recommendations,
    }


def _recommendation(
    *,
    kind: str,
    reason: str,
    fingerprint: str,
    source_event_id: str,
    recommended_route: str,
    verification_plan: list[str],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "status": "proposed",
        "authority": "proposal_only",
        "fingerprint": fingerprint,
        "source_event_id": source_event_id,
        "recommended_route": recommended_route,
        "reason": reason,
        "verification_plan": verification_plan,
        "forbidden_direct_events": FORBIDDEN_ADVISOR_EVENTS,
    }


__all__ = ["FORBIDDEN_ADVISOR_EVENTS", "build_replan_advisor_projection"]
