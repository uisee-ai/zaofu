"""Replan contract evaluation helpers.

The evaluator is intentionally pure: it reads candidate artifacts supplied by
the caller and returns a structured decision. Product Delivery, Supervisor, or
Autoresearch may consume the result, but this module never mutates runtime
truth.
"""

from __future__ import annotations

from typing import Any

from zf.runtime.replan_contract_checks import (
    _contract_completeness_check,
    _contract_delta,
    _decision,
    _done_evidence_check,
    _failure_binding_check,
    _old_tasks_by_id,
    _profile_check,
    _resume_safety_check,
    _schema_check,
    _scope_concurrency_check,
    _source_coverage_check,
    _task_map_items_by_id,
)
from zf.runtime.replan_contract_types import (
    ReplanContractCheck,
    ReplanContractEvalResult,
)

_VALID_PROFILES = {"baseline", "strict", "release"}


def evaluate_replan_contract(
    *,
    new_task_map: dict[str, Any],
    old_task_map: dict[str, Any] | None = None,
    old_tasks: list[dict[str, Any]] | dict[str, dict[str, Any]] | None = None,
    source_index: dict[str, Any] | None = None,
    failure_evidence: dict[str, Any] | None = None,
    progress_state: dict[str, Any] | None = None,
    profile: str = "baseline",
    strict_review_evidence: dict[str, Any] | None = None,
    release_evidence: dict[str, Any] | None = None,
    eval_id: str = "",
    trigger_event_id: str = "",
    old_task_map_ref: str = "",
    new_task_map_ref: str = "",
    expected_current_task_map_ref: str = "",
    idempotency_key: str = "",
    artifact_ref: str = "",
) -> ReplanContractEvalResult:
    profile = str(profile or "baseline").strip() or "baseline"
    if profile not in _VALID_PROFILES:
        profile = "baseline"
    old_task_map = old_task_map if isinstance(old_task_map, dict) else {}
    source_index = source_index if isinstance(source_index, dict) else {}
    old_by_id = _old_tasks_by_id(old_tasks)
    new_by_id = _task_map_items_by_id(new_task_map)
    old_map_by_id = _task_map_items_by_id(old_task_map)

    checks = [
        _schema_check(new_task_map),
        _contract_completeness_check(new_task_map),
        _scope_concurrency_check(new_task_map),
        _source_coverage_check(new_task_map, source_index),
        _done_evidence_check(new_by_id, old_by_id),
        _resume_safety_check(
            new_by_id=new_by_id,
            old_by_id=old_by_id,
            old_map_by_id=old_map_by_id,
            progress_state=progress_state if isinstance(progress_state, dict) else {},
        ),
        _failure_binding_check(
            new_by_id=new_by_id,
            failure_evidence=failure_evidence if isinstance(failure_evidence, dict) else {},
        ),
        _profile_check(
            profile=profile,
            strict_review_evidence=(
                strict_review_evidence if isinstance(strict_review_evidence, dict) else {}
            ),
            release_evidence=release_evidence if isinstance(release_evidence, dict) else {},
        ),
    ]
    failed = [check for check in checks if not check.passed]
    decision = _decision(profile=profile, failed=failed)
    delta = _contract_delta(new_by_id=new_by_id, old_by_id=old_by_id, old_map_by_id=old_map_by_id)
    required_fixes = [
        error
        for check in failed
        for error in check.errors
    ]
    refs = {
        key: value
        for key, value in {
            "artifact_ref": artifact_ref,
            "old_task_map_ref": old_task_map_ref,
            "new_task_map_ref": new_task_map_ref,
            "trigger_event_id": trigger_event_id,
        }.items()
        if str(value).strip()
    }
    return ReplanContractEvalResult(
        eval_id=eval_id,
        profile=profile,
        decision=decision,
        old_task_map_ref=old_task_map_ref,
        new_task_map_ref=new_task_map_ref,
        expected_current_task_map_ref=expected_current_task_map_ref,
        trigger_event_id=trigger_event_id,
        idempotency_key=idempotency_key,
        checks=checks,
        contract_delta=delta,
        required_fixes=required_fixes,
        refs=refs,
        summary={
            "check_count": len(checks),
            "failed_check_count": len(failed),
            "failed_checks": [check.name for check in failed],
            "task_count": len(new_by_id),
        },
    )


def event_payload_for_eval(result: ReplanContractEvalResult | dict[str, Any]) -> dict[str, Any]:
    """Return the compact refs-only event/Web payload for an eval result."""
    data = result.to_dict() if isinstance(result, ReplanContractEvalResult) else dict(result)
    checks = data.get("checks") if isinstance(data.get("checks"), list) else []
    failed_checks = [
        str(check.get("name") or "")
        for check in checks
        if isinstance(check, dict) and not bool(check.get("passed"))
    ]
    delta = data.get("contract_delta") if isinstance(data.get("contract_delta"), dict) else {}
    return {
        "schema_version": data.get("schema_version", "replan-contract-eval.v1"),
        "eval_id": str(data.get("eval_id") or ""),
        "profile": str(data.get("profile") or ""),
        "decision": str(data.get("decision") or ""),
        "old_task_map_ref": str(data.get("old_task_map_ref") or ""),
        "new_task_map_ref": str(data.get("new_task_map_ref") or ""),
        "expected_current_task_map_ref": str(
            data.get("expected_current_task_map_ref") or ""
        ),
        "trigger_event_id": str(data.get("trigger_event_id") or ""),
        "idempotency_key": str(data.get("idempotency_key") or ""),
        "failed_checks": [item for item in failed_checks if item],
        "check_summary": dict(data.get("summary") or {}),
        "contract_delta_counts": {
            "preserve": len(delta.get("preserve_task_ids") or []),
            "cancel": len(delta.get("cancel_task_ids") or []),
            "rewrite": len(delta.get("rewrite_task_ids") or []),
            "new": len(delta.get("new_task_ids") or []),
        },
        "refs": dict(data.get("refs") or {}),
    }


__all__ = [
    "ReplanContractCheck",
    "ReplanContractEvalResult",
    "evaluate_replan_contract",
    "event_payload_for_eval",
]
