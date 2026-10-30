"""Deterministic owner contract for the normal workflow frontier."""

from __future__ import annotations

from typing import Any


NORMAL_FRONTIER_OWNERS = frozenset({"run_manager", "wrc"})


def normal_frontier_owner(config: Any) -> str:
    orchestration = getattr(
        getattr(getattr(config, "workflow", None), "orchestration", None),
        "normal_frontier_owner",
        "run_manager",
    )
    owner = str(orchestration or "run_manager").strip().lower()
    return owner if owner in NORMAL_FRONTIER_OWNERS else "run_manager"


def run_manager_may_consume_normal_frontier(config: Any) -> bool:
    return normal_frontier_owner(config) == "run_manager"


def generation_owner_error(config: Any, events: list, *, task_store: Any = None,
                           workflow_run_id: str = "") -> str:
    """Fence active generations against changing the admitted dispatch owner.

    Legacy generations can be recovered only if a matching stage dispatch
    proves their owner; absent evidence cannot authorize a transfer.
    """
    from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts

    configured = normal_frontier_owner(config)
    dispatch_owners: dict[tuple[str, str], set[str]] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        run_id = str(payload.get("workflow_run_id") or "")
        generation = str(payload.get("task_map_generation") or "")
        if event.type == "task.pipeline.stage.dispatched" and run_id and generation:
            # Stage dispatches predating the owner field used Run Manager.
            owner = str(payload.get("frontier_owner") or "run_manager")
            dispatch_owners.setdefault((run_id, generation), set()).add(owner)
    for task_id, context in task_pipeline_generation_contexts(events).items():
        if workflow_run_id and context.get("workflow_run_id") != workflow_run_id:
            continue
        if task_store is not None:
            task = task_store.get(task_id)
            if task is None or str(task.status) in {"done", "cancelled"}:
                continue
        key = (str(context.get("workflow_run_id") or ""), str(context.get("task_map_generation") or ""))
        owners = set(dispatch_owners.get(key, set()))
        if context.get("frontier_owner"):
            owners.add(str(context["frontier_owner"]))
        if len(owners) > 1:
            return "generation_frontier_owner_conflict"
        if not owners or next(iter(owners)) not in NORMAL_FRONTIER_OWNERS:
            return "generation_frontier_owner_unknown"
        if next(iter(owners)) != configured:
            return "generation_frontier_owner_changed"
    return ""


__all__ = [
    "NORMAL_FRONTIER_OWNERS",
    "normal_frontier_owner",
    "run_manager_may_consume_normal_frontier",
    "generation_owner_error",
]
