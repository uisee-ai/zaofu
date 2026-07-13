"""Deterministic routing metadata for task-level semantic replans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore


SEMANTIC_REPLAN_ACTION = "semantic-replan-request"
SEMANTIC_REPLAN_SAFE_ACTION = "request_semantic_replan"

_PREFERRED_TRIGGER_ORDER = (
    "flow.discovery.requested",
    "verify.parity_scan.requested",
)
_SEMANTIC_REPLAN_SKILLS = {
    "zf-gap-task-synth",
}
_ANCHOR_EVENT_TYPES = {
    "task_map.ready",
    "task_map.amended",
    "product_delivery.task_map.adopted",
    "candidate.ready",
    "fanout.started",
}


@dataclass(frozen=True)
class SemanticReplanRoute:
    trigger_event: str
    stage_id: str
    role: str


def resolve_semantic_replan_route(config: ZfConfig) -> SemanticReplanRoute | None:
    """Find the declared gap-planning stage without hard-coding flow kinds."""

    stages = list(getattr(config.workflow, "stages", []) or [])
    for trigger in _PREFERRED_TRIGGER_ORDER:
        for stage in stages:
            if str(getattr(stage, "trigger", "") or "") == trigger:
                roles = list(getattr(stage, "roles", []) or [])
                return SemanticReplanRoute(
                    trigger_event=trigger,
                    stage_id=str(getattr(stage, "id", "") or ""),
                    role=str(roles[0] if roles else ""),
                )

    skills_by_role = {
        str(getattr(role, "name", "") or ""): set(
            str(value) for value in getattr(role, "skills", []) or []
        )
        for role in getattr(config, "roles", []) or []
    }
    for stage in stages:
        roles = list(getattr(stage, "roles", []) or [])
        for role in roles:
            if skills_by_role.get(str(role), set()) & _SEMANTIC_REPLAN_SKILLS:
                trigger = str(getattr(stage, "trigger", "") or "")
                if trigger:
                    return SemanticReplanRoute(
                        trigger_event=trigger,
                        stage_id=str(getattr(stage, "id", "") or ""),
                        role=str(role),
                    )
    return None


def enrich_semantic_replan_action(
    action: dict[str, Any],
    *,
    state_dir: Path,
    events: list[ZfEvent],
    config: ZfConfig,
) -> dict[str, Any]:
    """Attach stage and current task-map anchors, or fall back to diagnosis."""

    if str(action.get("action") or "") != SEMANTIC_REPLAN_ACTION:
        return action
    route = resolve_semantic_replan_route(config)
    anchor = _semantic_replan_anchor(
        state_dir,
        events,
        task_id=str(action.get("task_id") or ""),
    )
    if route is None or not anchor.get("task_map_ref") or not anchor.get("pdd_id"):
        reason = "semantic replan requires a declared gap-planning stage and current task-map anchor"
        return {
            **action,
            "action": "diagnose-attention",
            "safe_resume_action": "diagnose_attention",
            "failure_class": "semantic_replan_route_unavailable",
            "action_policy": "needs_diagnosis",
            "intervention_class": "diagnose",
            "summary": reason,
            "expected_downstream_events": [
                "run.manager.autoresearch.requested",
                "run.manager.resident.prompted",
            ],
            "verify_condition": (
                "expected_downstream_event:run.manager.autoresearch.requested,"
                "run.manager.resident.prompted"
            ),
        }
    return {
        **action,
        **anchor,
        "semantic_replan_trigger": route.trigger_event,
        "semantic_replan_stage_id": route.stage_id,
        "semantic_replan_role": route.role,
        "stage_id": route.stage_id,
        "action_policy": "auto_decide",
        "owner_route": "run_manager",
        "intervention_class": "semantic_replan",
        "expected_downstream_events": [route.trigger_event],
        "verify_condition": f"expected_downstream_event:{route.trigger_event}",
    }


def _semantic_replan_anchor(
    state_dir: Path,
    events: list[ZfEvent],
    *,
    task_id: str,
) -> dict[str, Any]:
    task = TaskStore(Path(state_dir) / "kanban.json").get(task_id) if task_id else None
    pdd_id = ""
    feature_id = ""
    source_index_ref = ""
    if task is not None:
        feature_id = str(task.contract.feature_id or "")
        pdd_id = feature_id
        source_index_ref = str(task.contract.source_index_ref or "")
    anchor: dict[str, Any] = {
        "pdd_id": pdd_id,
        "feature_id": feature_id or pdd_id,
        "source_index_ref": source_index_ref,
    }
    keys = (
        "task_map_ref",
        "source_index_ref",
        "source_commit",
        "candidate_base_commit",
        "candidate_ref",
        "target_ref",
        "trace_id",
    )
    for event in events:
        if event.type not in _ANCHOR_EVENT_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_pdd = str(payload.get("pdd_id") or payload.get("feature_id") or "")
        if pdd_id and event_pdd and event_pdd != pdd_id:
            continue
        if not pdd_id and event_pdd:
            pdd_id = event_pdd
            anchor["pdd_id"] = pdd_id
            anchor["feature_id"] = str(payload.get("feature_id") or pdd_id)
        for key in keys:
            value = str(payload.get(key) or "")
            if value:
                anchor[key] = value
    if pdd_id and not anchor.get("task_map_ref"):
        fallback = Path(state_dir) / "artifacts" / pdd_id / "task_map.json"
        if fallback.exists():
            anchor["task_map_ref"] = str(fallback)
    anchor["supersedes_task_ids"] = [task_id] if task_id else []
    anchor["affected_task_ids"] = [task_id] if task_id else []
    return anchor


__all__ = [
    "SEMANTIC_REPLAN_ACTION",
    "SEMANTIC_REPLAN_SAFE_ACTION",
    "SemanticReplanRoute",
    "enrich_semantic_replan_action",
    "resolve_semantic_replan_route",
]
