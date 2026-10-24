"""Select canonical tasks eligible for candidate-level terminal settlement."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task


_ACTIVE_SETTLEMENT_STATUSES = frozenset({
    "backlog",
    "in_progress",
    "review",
    "verify",
    "testing",
    "test",
    "judge",
})
_TASK_SUCCESS_EVENTS = frozenset({
    "review.approved",
    "task.attempt.succeeded",
    "task.done.accepted",
    "test.passed",
    "verify.passed",
})


def select_candidate_terminal_tasks(
    tasks: Iterable[Task],
    event: ZfEvent,
    *,
    pdd_id: str,
    feature_id: str,
    is_bootstrap_task: Callable[[Task], bool],
    successful_task_ids: Iterable[str] = (),
    retained_task_ids: Iterable[str] = (),
) -> list[Task]:
    """Return tasks that the candidate terminal may mechanically close."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    completed_task_ids = {
        str(task_id).strip()
        for task_id in (
            payload.get("completed_task_ids")
            if isinstance(payload.get("completed_task_ids"), list)
            else []
        )
        if str(task_id).strip()
    }
    exact_goal_settlement = (
        event.type == "run.goal.completed"
        and bool(completed_task_ids)
    )
    successful_ids = {
        str(task_id).strip()
        for task_id in successful_task_ids
        if str(task_id).strip()
    }
    retained_ids = set(retained_task_ids)
    selected: list[Task] = []
    for task in tasks:
        task_feature = str(
            getattr(getattr(task, "contract", None), "feature_id", "") or ""
        ).strip()
        is_container = task.id in {pdd_id, feature_id}
        bootstrap_container = is_container and is_bootstrap_task(task)
        managed_workflow_parent = _is_current_workflow_managed_task(
            task,
            payload,
        )
        retained = task.id in retained_ids
        explicitly_completed = (
            exact_goal_settlement
            and task.id in completed_task_ids
        )
        same_generation_task = (
            exact_goal_settlement
            and _shares_task_map_generation(task, payload)
        )
        blocked_with_terminal_evidence = (
            task.status == "blocked"
            and same_generation_task
            and (
                explicitly_completed
                or task.id in successful_ids
                or _task_goal_claims_closed(task, payload)
            )
        )
        if (
            task.status not in _ACTIVE_SETTLEMENT_STATUSES
            and not blocked_with_terminal_evidence
        ):
            continue
        if (
            explicitly_completed
            or same_generation_task
            or managed_workflow_parent
        ) and not retained and not goal_terminal_matches_current_task(
            task, payload
        ):
            continue
        if exact_goal_settlement and not (
            retained
            or explicitly_completed
            or same_generation_task
            or bootstrap_container
            or managed_workflow_parent
        ):
            continue
        if task.status == "backlog" and not (
            explicitly_completed
            or bootstrap_container
            or managed_workflow_parent
        ):
            continue
        if (
            task_feature == feature_id
            or is_container
            or managed_workflow_parent
        ):
            selected.append(task)
    return selected


def _is_current_workflow_managed_task(task: Task, terminal_payload: dict) -> bool:
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", None)
    if not isinstance(evidence, dict):
        return False
    if str(evidence.get("execution_owner") or "") != "workflow":
        return False
    workflow_request_id = str(
        evidence.get("workflow_request_id") or ""
    ).strip()
    terminal_run_id = str(
        terminal_payload.get("workflow_run_id")
        or terminal_payload.get("run_id")
        or ""
    ).strip()
    return bool(
        workflow_request_id
        and terminal_run_id
        and workflow_request_id == terminal_run_id
    )


def goal_terminal_matches_current_task(
    task: Task,
    terminal_payload: dict,
) -> bool:
    """Reject a late terminal after the canonical task contract was replaced."""
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", None)
    if not isinstance(evidence, dict):
        return True
    source_refs = evidence.get("source_refs")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    current_ref = str(source_refs.get("task_map_ref") or "").strip()
    terminal_ref = str(terminal_payload.get("task_map_ref") or "").strip()
    current_generation = str(
        evidence.get("task_map_generation")
        or source_refs.get("task_map_generation")
        or ""
    ).strip()
    terminal_generation = str(
        terminal_payload.get("task_map_generation") or ""
    ).strip()
    if current_generation and terminal_generation:
        return current_generation == terminal_generation
    return not (
        current_ref
        and terminal_ref
        and current_ref != terminal_ref
    )


def _shares_task_map_generation(task: Task, terminal_payload: dict) -> bool:
    contract = getattr(task, "contract", None)
    evidence = getattr(contract, "evidence_contract", None)
    if not isinstance(evidence, dict):
        return False
    source_refs = evidence.get("source_refs")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    current_generation = str(
        evidence.get("task_map_generation")
        or source_refs.get("task_map_generation")
        or ""
    ).strip()
    terminal_generation = str(
        terminal_payload.get("task_map_generation") or ""
    ).strip()
    return bool(
        current_generation
        and terminal_generation
        and current_generation == terminal_generation
    )


def _task_goal_claims_closed(task: Task, terminal_payload: dict) -> bool:
    contract = getattr(task, "contract", None)
    task_claim_ids = {
        str(claim_id).strip()
        for claim_id in getattr(contract, "goal_claim_ids", []) or []
        if str(claim_id).strip()
    }
    if not task_claim_ids:
        return False
    closed_claim_ids = {
        str(item.get("goal_claim_id") or "").strip()
        for item in terminal_payload.get("goal_coverage") or []
        if isinstance(item, dict)
        and str(item.get("status") or item.get("verdict") or "") == "closed"
        and str(item.get("goal_claim_id") or "").strip()
    }
    return task_claim_ids <= closed_claim_ids


def successful_task_ids_before_terminal(
    events: Iterable[ZfEvent],
    terminal: ZfEvent,
) -> set[str]:
    """Return task attempts already admitted before one run terminal."""

    payload = terminal.payload if isinstance(terminal.payload, dict) else {}
    run_id = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or terminal.correlation_id
        or ""
    ).strip()
    successful: set[str] = set()
    for event in events:
        if event.id == terminal.id:
            break
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        event_run_id = str(
            event_payload.get("workflow_run_id")
            or event_payload.get("run_id")
            or event.correlation_id
            or ""
        ).strip()
        if run_id and event_run_id != run_id:
            continue
        if event.type not in _TASK_SUCCESS_EVENTS:
            continue
        task_id = str(event.task_id or event_payload.get("task_id") or "").strip()
        if task_id:
            successful.add(task_id)
    return successful


__all__ = [
    "goal_terminal_matches_current_task",
    "select_candidate_terminal_tasks",
    "successful_task_ids_before_terminal",
]
