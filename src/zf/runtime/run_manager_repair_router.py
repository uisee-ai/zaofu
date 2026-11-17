"""Run Manager routing contracts for repair closeout and aggregate rebuild."""

from __future__ import annotations

from typing import Any


REPAIR_CLOSEOUT_ACTIONS = frozenset({"repair-closeout-validate"})
REPAIR_CLOSEOUT_APPLY_ACTIONS = frozenset({"repair-closeout-apply"})
FANOUT_AGGREGATE_ACTIONS = frozenset({"fanout-aggregate-rebuild"})


def special_action_policy(
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the bounded policy for repair-specific actions."""

    preflight = special_action_preflight(action, payload)
    if preflight is None:
        return None
    if action in REPAIR_CLOSEOUT_ACTIONS:
        if preflight["status"] == "blocked":
            return {
                "decision": "needs_diagnosis",
                "executable": True,
                "preflight": preflight,
                "reason": "repair closeout validation is missing required evidence",
            }
        return {
            "decision": "auto_decide",
            "executable": True,
            "preflight": preflight,
            "reason": "repair closeout validation is read-only and allowlisted",
        }
    if action in REPAIR_CLOSEOUT_APPLY_ACTIONS:
        if preflight["status"] == "blocked":
            return {
                "decision": "needs_approval",
                "executable": False,
                "preflight": preflight,
                "reason": "verified repair apply failed a mutation safety precondition",
            }
        return {
            "decision": "auto_decide",
            "executable": True,
            "preflight": preflight,
            "reason": "verified checkpoint apply is explicitly enabled and fail-closed",
        }
    if preflight["status"] == "blocked":
        return {
            "decision": "needs_diagnosis",
            "executable": True,
            "preflight": preflight,
            "reason": "fanout aggregate rebuild is missing immutable source evidence",
        }
    return {
        "decision": "auto_decide",
        "executable": True,
        "preflight": preflight,
        "reason": "fanout aggregate rebuild recomputes durable child facts",
    }


def special_action_preflight(
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate repair-specific actions without importing the main router."""

    if action not in (
        REPAIR_CLOSEOUT_ACTIONS
        | REPAIR_CLOSEOUT_APPLY_ACTIONS
        | FANOUT_AGGREGATE_ACTIONS
    ):
        return None
    failures: list[str] = []
    checkpoint_id = str(payload.get("checkpoint_id") or "")
    safe_action = str(payload.get("safe_resume_action") or "")
    if action in REPAIR_CLOSEOUT_ACTIONS:
        if not checkpoint_id:
            failures.append("missing_checkpoint_id")
        if not str(payload.get("worktree_path") or payload.get("worktree") or ""):
            failures.append("missing_worktree_path")
        plan = payload.get("verification_plan")
        if not isinstance(plan, list) or not plan:
            failures.append("missing_verification_plan")
        if bool(payload.get("repair_contract_required", False)):
            if not isinstance(payload.get("repair_contract_ref"), dict) or not payload.get(
                "repair_contract_ref"
            ):
                failures.append("missing_repair_contract_ref")
            if not str(payload.get("repair_contract_digest") or ""):
                failures.append("missing_repair_contract_digest")
            if not str(payload.get("base_commit") or ""):
                failures.append("missing_repair_contract_base_commit")
        return _preflight_result(
            payload=payload,
            failures=failures,
            checkpoint_id=checkpoint_id,
            safe_action=safe_action or "repair_closeout_validate",
            expected=["run.manager.action.applied"],
        )
    if action in REPAIR_CLOSEOUT_APPLY_ACTIONS:
        if not checkpoint_id:
            failures.append("missing_apply_checkpoint_id")
        if payload.get("restart_policy") != "operator_approved":
            failures.append("repair_restart_not_authorized")
        if str(payload.get("apply_policy") or "") != "verified_checkpoint_apply":
            failures.append("verified_checkpoint_apply_not_enabled")
        if not str(payload.get("worktree_path") or payload.get("worktree") or ""):
            failures.append("missing_worktree_path")
        if not str(payload.get("base_commit") or ""):
            failures.append("missing_base_commit")
        if not str(payload.get("repair_commit") or payload.get("source_commit") or ""):
            failures.append("missing_repair_commit")
        if str(payload.get("validation_status") or "") != "passed":
            failures.append("repair_validation_not_passed")
        if not str(payload.get("validation_event_id") or ""):
            failures.append("missing_validation_event_id")
        if bool(payload.get("repair_contract_required", False)):
            if not isinstance(payload.get("repair_contract_ref"), dict) or not payload.get(
                "repair_contract_ref"
            ):
                failures.append("missing_repair_contract_ref")
            contract_digest = str(payload.get("repair_contract_digest") or "")
            if not contract_digest:
                failures.append("missing_repair_contract_digest")
            validation = payload.get("validation_result")
            validation = validation if isinstance(validation, dict) else {}
            if str(validation.get("repair_contract_digest") or "") != contract_digest:
                failures.append("repair_validation_contract_mismatch")
        if not str(payload.get("continuation_checkpoint_id") or ""):
            failures.append("missing_continuation_checkpoint_id")
        if not str(payload.get("continuation_safe_resume_action") or ""):
            failures.append("missing_continuation_safe_resume_action")
        stale_reason = str(payload.get("continuation_stale_reason") or "").strip()
        if stale_reason:
            failures.append("continuation_checkpoint_stale:" + stale_reason)
        risk = payload.get("risk_classification")
        risk = risk if isinstance(risk, dict) else {}
        if str(risk.get("risk") or "") == "high":
            failures.append("repair_risk_high")
        allow_paths = payload.get("allow_paths")
        if not isinstance(allow_paths, list) or not allow_paths:
            failures.append("missing_allow_paths")
        return _preflight_result(
            payload=payload,
            failures=failures,
            checkpoint_id=checkpoint_id,
            safe_action=safe_action or "repair_closeout_apply",
            expected=["run.manager.repair.merge.merged"],
        )
    if not checkpoint_id:
        failures.append("missing_checkpoint_id")
    if not str(payload.get("source_event_id") or ""):
        failures.append("missing_source_event_id")
    return _preflight_result(
        payload=payload,
        failures=failures,
        checkpoint_id=checkpoint_id,
        safe_action=safe_action or "fanout_aggregate_rebuild",
        expected=sorted(special_expected_downstream_events("fanout_aggregate_rebuild") or ()),
    )


def special_expected_downstream_events(safe_action: str) -> set[str] | None:
    if safe_action == "repair_closeout_validate":
        return {"run.manager.action.applied"}
    if safe_action == "fanout_aggregate_rebuild":
        return {
            "fanout.aggregate.rebuild.requested",
            "flow.discovery.completed",
            "goal.closure.synthesized",
            "flow.goal.closed",
        }
    return None


def _preflight_result(
    *,
    payload: dict[str, Any],
    failures: list[str],
    checkpoint_id: str,
    safe_action: str,
    expected: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "run-manager.action-preflight.v1",
        "status": "blocked" if failures else "passed",
        "failures": failures,
        "warnings": [],
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": safe_action,
        "expected_downstream_events": expected,
        "verify_condition": str(payload.get("verify_condition") or "")
        or "expected_downstream_event:" + ",".join(expected),
    }


__all__ = [
    "FANOUT_AGGREGATE_ACTIONS",
    "REPAIR_CLOSEOUT_ACTIONS",
    "REPAIR_CLOSEOUT_APPLY_ACTIONS",
    "special_action_policy",
    "special_action_preflight",
    "special_expected_downstream_events",
]
