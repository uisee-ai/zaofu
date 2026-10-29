"""Currentness and identity helpers for read-only dynamic continuations."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.dynamic_fragment_policy import (
    CONTINUATION_ENVELOPE_SCHEMA_VERSION,
)
from zf.runtime.execution_patterns import ExecutionPattern
from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages
from zf.runtime.workflow_operation import (
    TERMINAL_OPERATION_STATUSES,
    reduce_workflow_operations,
    stable_operation_id,
)


FRAGMENT_TERMINALS = frozenset({
    "workflow.fragment.rejected",
    "workflow.fragment.superseded",
    "workflow.fragment.cancelled",
})
_OPERATOR_PROPOSALS = frozenset({
    "operator.action.proposed",
    "kanban.agent.action.proposed",
    "approval.requested",
    "plan.approval.requested",
})
_OPERATOR_TERMINALS = frozenset({
    "operator.action.completed",
    "operator.action.failed",
    "operator.action.executed",
    "operator.action.resolved",
    "kanban.agent.proposal.resolved",
    "approval.resolved",
    "approval.expired",
    "plan.approved",
    "plan.rejected",
})


def currentness_preflight(
    state_dir: Path,
    *,
    config: ZfConfig,
    events: list[ZfEvent],
    action: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    workflow_run_id = str(action.get("workflow_run_id") or "")
    current_package = reduce_plan_artifact_packages(
        events,
        workflow_run_id=workflow_run_id,
    ).get("current")
    current_package = (
        current_package if isinstance(current_package, Mapping) else {}
    )
    expected = {
        "package_id": str(action.get("plan_artifact_package_id") or ""),
        "package_ref": str(action.get("plan_artifact_package_ref") or ""),
        "package_digest": str(action.get("plan_artifact_package_digest") or ""),
        "task_map_generation": str(action.get("task_map_generation") or ""),
    }
    for key, value in expected.items():
        if str(current_package.get(key) or "") != value:
            return f"stale_plan_artifact_package_{key}", "", {}
    operations = reduce_workflow_operations(events)
    parent = operations.get(
        str(action.get("parent_operation_id") or ""),
    )
    if not parent:
        return "parent_operation_missing", "", {}
    if str(parent.get("workflow_run_id") or "") != workflow_run_id:
        return "parent_operation_run_mismatch", "", {}
    if str(parent.get("status") or "") in TERMINAL_OPERATION_STATUSES:
        return "parent_operation_terminal", "", {}
    ancestor = parent
    seen = set()
    while ancestor:
        ancestor_id = str(ancestor.get("operation_id") or "")
        if ancestor_id in seen or ancestor.get("operation_type") == "dynamic_read_only_workflow":
            return "dynamic_depth_exceeded", "", {}
        seen.add(ancestor_id)
        ancestor = operations.get(str(ancestor.get("parent_operation_id") or ""))
    from zf.runtime.dynamic_continuation_lifecycle import capacity_error

    capacity_reason = capacity_error(state_dir, operations, action, operation_id(action))
    if capacity_reason:
        return capacity_reason, "", {}
    fragment_id = str(action.get("fragment_id") or "")
    if any(
        event.type in FRAGMENT_TERMINALS
        and str((event.payload or {}).get("fragment_id") or "") == fragment_id
        for event in events
    ):
        return "fragment_no_longer_current", "", {}
    pending = _pending_operator_actions(events, workflow_run_id=workflow_run_id)
    pending_digest = _digest(pending)
    budget = _budget_snapshot(
        state_dir,
        config=config,
        proposal_budget=action.get("budgets"),
    )
    if pending:
        return "pending_operator_or_control_action", pending_digest, budget
    if not budget.get("usage_known", True):
        return "budget_usage_unknown", pending_digest, budget
    if not bool(budget.get("available", True)):
        return "budget_exhausted", pending_digest, budget
    return "", pending_digest, budget


def operation_request(
    action: Mapping[str, Any],
    *,
    pattern: ExecutionPattern | None,
    pending_action_digest: str,
    budget_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": CONTINUATION_ENVELOPE_SCHEMA_VERSION,
        "attempt_domain": "read_only_dynamic",
        "mode": "read_only",
        "workflow_run_id": str(action["workflow_run_id"]),
        "task_id": str(action["task_id"]),
        "fragment_id": str(action["fragment_id"]),
        "fragment_digest": str(action["fragment_digest"]),
        "continuation_key": str(action["continuation_key"]),
        "parent_operation_id": str(action["parent_operation_id"]),
        "pattern_id": str(action["pattern_id"]),
        "plan_artifact_package_id": str(action["plan_artifact_package_id"]),
        "plan_artifact_package_ref": str(action["plan_artifact_package_ref"]),
        "plan_artifact_package_digest": str(
            action["plan_artifact_package_digest"],
        ),
        "task_map_generation": str(action["task_map_generation"]),
        "trigger_checkpoint_ref": str(action["trigger_checkpoint_ref"]),
        "trigger_checkpoint_digest": str(action["trigger_checkpoint_digest"]),
        "pending_action_digest": pending_action_digest,
        "budget_snapshot": dict(budget_snapshot),
        "execution_binding": {
            "pattern_id": str(getattr(pattern, "pattern_id", "") or ""),
            "topology": str(getattr(pattern, "kind", "") or ""),
            "roles": list(getattr(pattern, "roles", []) or []),
            "barrier": dict(getattr(pattern, "barrier", {}) or {}),
        },
        "expected_output": str(action.get("expected_output") or ""),
        "target_ref": str(action.get("target_ref") or ""),
    }


def operation_id(action: Mapping[str, Any]) -> str:
    return stable_operation_id(
        workflow_run_id=str(action.get("workflow_run_id") or ""),
        parent_stage_id=str(action.get("pattern_id") or "dynamic-read-only"),
        operation_key=":".join((
            str(action.get("continuation_key") or ""),
            str(action.get("task_map_generation") or ""),
        )),
        operation_type="dynamic_read_only_workflow",
    )


def matching_dispatch_event(
    events: Iterable[ZfEvent],
    operation_id_value: str,
) -> ZfEvent | None:
    return next((
        event
        for event in reversed(list(events))
        if event.type == "workflow.invoke.requested"
        and str((event.payload or {}).get("workflow_operation_id") or "")
        == operation_id_value
    ), None)


def budget_digest(budget: Mapping[str, Any]) -> str:
    body = dict(budget)
    body.setdefault("usage_known", True)
    return _digest(body)


def emit_fragment_once(
    writer: EventWriter,
    events: Iterable[ZfEvent],
    event_type: str,
    action: Mapping[str, Any],
    *,
    reason: str,
    causation_id: str,
    extra: Mapping[str, Any] | None = None,
) -> ZfEvent | None:
    fragment_id = str(action.get("fragment_id") or "")
    fragment_digest = str(action.get("fragment_digest") or "")
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if (
            event.type == event_type
            and str(payload.get("fragment_id") or "") == fragment_id
            and str(payload.get("fragment_digest") or "") == fragment_digest
        ):
            return None
    return writer.emit(
        event_type,
        actor="run-manager",
        task_id=str(action.get("task_id") or "") or None,
        causation_id=causation_id or str(action.get("source_event_id") or "") or None,
        correlation_id=str(action.get("workflow_run_id") or "") or None,
        payload={
            "schema_version": "workflow-fragment-lifecycle.v1",
            "workflow_run_id": str(action.get("workflow_run_id") or ""),
            "fragment_id": fragment_id,
            "fragment_digest": fragment_digest,
            "mode": "read_only",
            "task_map_generation": str(
                action.get("task_map_generation") or ""
            ),
            "plan_artifact_package_ref": str(
                action.get("plan_artifact_package_ref") or ""
            ),
            "plan_artifact_package_digest": str(
                action.get("plan_artifact_package_digest") or ""
            ),
            "continuation_key": str(action.get("continuation_key") or ""),
            "parent_operation_id": str(
                action.get("parent_operation_id") or ""
            ),
            "reason": reason,
            "semantic_attempt_consumed": False,
            **dict(extra or {}),
        },
    )


def _pending_operator_actions(
    events: list[ZfEvent],
    *,
    workflow_run_id: str,
) -> list[str]:
    terminals_by_causation = {
        str(event.causation_id or "")
        for event in events
        if event.type in _OPERATOR_TERMINALS and event.causation_id
    }
    terminal_tokens: set[str] = set()
    for event in events:
        if event.type not in _OPERATOR_TERMINALS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        proposal = (
            payload.get("proposal")
            if isinstance(payload.get("proposal"), dict)
            else {}
        )
        terminal_tokens.update(
            str(payload.get(key) or proposal.get(key) or "")
            for key in (
                "proposal_event_id",
                "proposal_id",
                "approval_id",
                "request_id",
                "intent_id",
            )
            if str(payload.get(key) or "")
            or str(proposal.get(key) or "")
        )
    pending: list[str] = []
    for event in events:
        if event.type not in _OPERATOR_PROPOSALS:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        proposal = (
            payload.get("proposal")
            if isinstance(payload.get("proposal"), dict)
            else {}
        )
        event_run_id = str(
            payload.get("workflow_run_id")
            or payload.get("run_id")
            or event.correlation_id
            or ""
        )
        if event_run_id != workflow_run_id:
            continue
        tokens = {
            str(payload.get(key) or proposal.get(key) or "")
            for key in (
                "proposal_event_id",
                "proposal_id",
                "approval_id",
                "request_id",
                "intent_id",
            )
            if str(payload.get(key) or "")
            or str(proposal.get(key) or "")
        }
        tokens.add(event.id)
        if event.id in terminals_by_causation or tokens & terminal_tokens:
            continue
        pending.append(event.id)
    return sorted(pending)


def _budget_snapshot(
    state_dir: Path,
    *,
    config: ZfConfig,
    proposal_budget: Any,
) -> dict[str, Any]:
    cap = getattr(config, "global_budget_usd", None)
    enforcement = bool(getattr(config, "budget_enforcement_enabled", True))
    spent = 0.0
    usage_known = True
    if cap is not None:
        try:
            from zf.core.cost.tracker import CostTracker

            usage = CostTracker(state_dir / "cost.jsonl").usage_totals()
            spent = float(usage["total_usd"])
            usage_known = math.isfinite(spent) and spent >= 0 and not (
                usage.get("unpriced_entries") or usage.get("partial_entries")
            )
        except Exception:
            usage_known = False
    proposal = (
        dict(proposal_budget)
        if isinstance(proposal_budget, Mapping)
        else {}
    )
    remaining = None if cap is None or not usage_known else max(0.0, float(cap) - spent)
    available = not (
        enforcement
        and cap is not None
        and spent >= float(cap)
    )
    return {
        "schema_version": "continuation-budget-snapshot.v1",
        "global_cap_usd": None if cap is None else float(cap),
        "spent_usd": round(spent, 6) if cap is not None and usage_known else None,
        "remaining_usd": (
            round(remaining, 6) if remaining is not None else None
        ),
        "enforcement_enabled": enforcement,
        "available": available and usage_known,
        "usage_known": usage_known,
        "proposal": proposal,
    }


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "FRAGMENT_TERMINALS",
    "budget_digest",
    "currentness_preflight",
    "emit_fragment_once",
    "matching_dispatch_event",
    "operation_id",
    "operation_request",
]
