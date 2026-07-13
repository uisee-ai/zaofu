"""Machine-readable failure-kind for dispatch/candidate failures.

ZF-E2E-PRDCTL-P0-1 (2026-07-12): budget-blocked dispatches were only
distinguishable by reason text, so candidate rework treated them as quality
failures and planned paid retriggers (live: judge dispatch blocked at the
budget gate -> candidate_retrigger). The kind is stamped at the dispatch
exception site; consumers read the field first and fall back to reason-text
markers so pre-field event logs keep classifying.
"""

from __future__ import annotations

FAILURE_KIND_BUDGET = "budget"
FAILURE_KIND_QUALITY = "quality"
FAILURE_KIND_MIXED = "mixed"

_BUDGET_MARKERS = ("budget exceeded",)


def is_budget_reason(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _BUDGET_MARKERS)


def classify_dispatch_exception(exc: BaseException) -> str:
    """Failure kind for a dispatch-time exception ('' = unclassified).

    Matches BudgetExceededError by name to avoid importing the orchestrator
    module (this helper is consumed from its mixins).
    """
    if type(exc).__name__ == "BudgetExceededError" or is_budget_reason(str(exc)):
        return FAILURE_KIND_BUDGET
    return ""


def failure_kind_from_payload(payload: object) -> str:
    """Read the machine field, falling back to reason-text markers."""
    if not isinstance(payload, dict):
        return ""
    kind = str(payload.get("failure_kind") or "").strip()
    if kind:
        return kind
    if is_budget_reason(str(payload.get("reason") or "")):
        return FAILURE_KIND_BUDGET
    return ""


def aggregate_failure_kind(failed_children: list) -> str:
    """Aggregate child kinds: uniform non-quality kind, else mixed/''.

    Children without a kind count as quality (the default), so a
    quality-only aggregate yields '' and the payload stays unchanged.
    """
    kinds = {
        failure_kind_from_payload(child) or FAILURE_KIND_QUALITY
        for child in failed_children
        if isinstance(child, dict)
    }
    if not kinds or kinds == {FAILURE_KIND_QUALITY}:
        return ""
    if len(kinds) == 1:
        return next(iter(kinds))
    return FAILURE_KIND_MIXED


def budget_candidate_failure_ids(events: list, candidate_fail_events) -> set[str]:
    """Candidate failures caused by budget-blocked dispatches.

    ZF-E2E-PRDCTL-P0-1: these are runtime/funding failures, not reviewer
    findings — retriggering re-runs paid work into the same closed budget
    gate. They must route to owner escalation instead of quality rework and
    must not consume the bounded rework budget.
    """
    child_kinds_by_fanout: dict[str, list[str]] = {}
    for event in events:
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        if not str(getattr(event, "type", "")).endswith(".child.failed"):
            continue
        fanout_id = str(payload.get("fanout_id") or "").strip()
        if not fanout_id:
            continue
        child_kinds_by_fanout.setdefault(fanout_id, []).append(
            failure_kind_from_payload(payload)
        )

    out: set[str] = set()
    for event in events:
        event_id = str(getattr(event, "id", "") or "")
        if not event_id:
            continue
        etype = str(getattr(event, "type", "") or "")
        if etype not in candidate_fail_events:
            continue
        if getattr(event, "task_id", None):
            continue
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        if failure_kind_from_payload(payload) == FAILURE_KIND_BUDGET:
            out.add(event_id)
            continue
        fanout_id = str(payload.get("fanout_id") or "").strip()
        child_kinds = child_kinds_by_fanout.get(fanout_id, [])
        if child_kinds and all(
            kind == FAILURE_KIND_BUDGET for kind in child_kinds
        ):
            out.add(event_id)
    return out
