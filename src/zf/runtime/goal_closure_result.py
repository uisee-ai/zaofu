"""Typed Thin Judge result and deterministic completeness checks."""

from __future__ import annotations

from typing import Any, Mapping


SCHEMA_VERSION = "goal-closure-result.v1"
FLOW_KINDS = frozenset({"issue", "prd", "refactor"})
VERDICTS = frozenset({"passed", "rejected", "blocked"})
CLAIM_STATUSES = frozenset({"closed", "open", "blocked", "waived"})


class GoalClosureResultError(ValueError):
    """A Thin Judge result cannot be admitted as goal truth."""


def normalize_goal_closure_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("goal_closure_result")
    if not isinstance(raw, Mapping):
        report = payload.get("report")
        if isinstance(report, Mapping):
            raw = report.get("goal_closure_result")
    if not isinstance(raw, Mapping):
        raise GoalClosureResultError("goal_closure_result object is required")
    result = dict(raw)
    result.setdefault("schema_version", SCHEMA_VERSION)
    validate_goal_closure_result(result)
    return result


def validate_goal_closure_result(result: Mapping[str, Any]) -> None:
    if str(result.get("schema_version") or "") != SCHEMA_VERSION:
        raise GoalClosureResultError("unsupported goal closure result schema")
    required = (
        "workflow_run_id",
        "goal_id",
        "flow_kind",
        "task_map_generation",
        "target_commit",
        "objective_ref",
        "goal_claim_set_ref",
        "goal_claim_set_digest",
        "planning_result_ref",
        "candidate_ref",
        "closure_fact_ref",
        "closure_fact_digest",
        "verdict",
        "summary",
    )
    missing = [field for field in required if not str(result.get(field) or "").strip()]
    if missing:
        raise GoalClosureResultError("goal closure result missing: " + ", ".join(missing))
    flow_kind = str(result.get("flow_kind") or "").lower()
    if flow_kind not in FLOW_KINDS:
        raise GoalClosureResultError(f"invalid flow_kind {flow_kind!r}")
    verdict = str(result.get("verdict") or "").lower()
    if verdict not in VERDICTS:
        raise GoalClosureResultError(f"invalid verdict {verdict!r}")
    coverage = result.get("goal_coverage")
    if not isinstance(coverage, list) or not coverage:
        raise GoalClosureResultError("goal_coverage must be a non-empty list")
    seen: set[str] = set()
    statuses: set[str] = set()
    for index, raw in enumerate(coverage):
        if not isinstance(raw, Mapping):
            raise GoalClosureResultError(f"goal_coverage[{index}] must be an object")
        claim_id = str(raw.get("goal_claim_id") or "").strip()
        status = str(raw.get("status") or "").strip().lower()
        if not claim_id:
            raise GoalClosureResultError(f"goal_coverage[{index}] missing goal_claim_id")
        if claim_id in seen:
            raise GoalClosureResultError(f"duplicate goal claim {claim_id!r}")
        seen.add(claim_id)
        if status not in CLAIM_STATUSES:
            raise GoalClosureResultError(
                f"goal_coverage[{index}] has invalid status {status!r}"
            )
        statuses.add(status)
        refs = _strings(raw.get("supporting_result_refs"))
        waiver_ref = str(raw.get("waiver_ref") or "").strip()
        if status == "closed" and not refs:
            raise GoalClosureResultError(
                f"goal_coverage[{index}] closed without supporting_result_refs"
            )
        if status == "waived" and not waiver_ref:
            raise GoalClosureResultError(
                f"goal_coverage[{index}] waived without waiver_ref"
            )
    gaps = _strings(result.get("open_gap_refs"))
    inputs = _strings(result.get("input_result_refs"))
    if not inputs:
        raise GoalClosureResultError("input_result_refs must reference admitted results")
    if verdict == "passed" and statuses - {"closed", "waived"}:
        raise GoalClosureResultError("passed verdict contains open or blocked claims")
    if verdict == "passed" and gaps:
        raise GoalClosureResultError("passed verdict cannot contain open_gap_refs")
    if verdict == "rejected" and not gaps:
        raise GoalClosureResultError("rejected verdict requires open_gap_refs")
    if verdict == "rejected" and not statuses.intersection({"open", "blocked"}):
        raise GoalClosureResultError("rejected verdict requires an open claim")
    action = str(result.get("recommended_action") or "").strip().lower()
    allowed_actions = {
        "passed": {"complete"},
        "rejected": {"gap_plan", "replan", "candidate_verify"},
        "blocked": {"human", "hold"},
    }
    if action not in allowed_actions[verdict]:
        raise GoalClosureResultError(
            f"recommended_action {action!r} is invalid for {verdict}"
        )


def claim_set_issues(
    result: Mapping[str, Any],
    claim_set: Mapping[str, Any],
    *,
    admitted_result_refs: set[str] | None = None,
    claim_set_descriptor_digest: str = "",
) -> list[dict[str, str]]:
    """Compare Judge coverage to the immutable canonical claim set."""

    claims = claim_set.get("claims")
    claims = claims if isinstance(claims, list) else []
    known = {
        str(item.get("goal_claim_id") or "").strip()
        for item in claims
        if isinstance(item, Mapping)
        and str(item.get("goal_claim_id") or "").strip()
    }
    expected = {
        str(item.get("goal_claim_id") or "").strip()
        for item in claims
        if isinstance(item, Mapping)
        and bool(item.get("mandatory", True))
        and str(item.get("goal_claim_id") or "").strip()
    }
    coverage = result.get("goal_coverage")
    coverage = coverage if isinstance(coverage, list) else []
    actual = [
        str(item.get("goal_claim_id") or "").strip()
        for item in coverage
        if isinstance(item, Mapping)
    ]
    issues: list[dict[str, str]] = []
    missing = sorted(expected - set(actual))
    unknown = sorted(set(actual) - known)
    duplicates = sorted({item for item in actual if actual.count(item) > 1 and item})
    for code, values in (
        ("missing_claim", missing),
        ("unknown_claim", unknown),
        ("duplicate_claim", duplicates),
    ):
        for value in values:
            issues.append({"field": "goal_coverage", "code": code, "message": value})
    expected_digest = str(
        claim_set_descriptor_digest or claim_set.get("claim_set_digest") or ""
    )
    result_digest = str(result.get("goal_claim_set_digest") or "")
    if expected_digest and result_digest != expected_digest:
        issues.append({
            "field": "goal_claim_set_digest",
            "code": "identity_mismatch",
            "message": f"expected {expected_digest}, got {result_digest}",
        })
    if admitted_result_refs is not None:
        for index, item in enumerate(coverage):
            if not isinstance(item, Mapping) or str(item.get("status") or "") != "closed":
                continue
            for ref in _strings(item.get("supporting_result_refs")):
                if ref not in admitted_result_refs:
                    issues.append({
                        "field": f"goal_coverage[{index}].supporting_result_refs",
                        "code": "result_not_admitted",
                        "message": ref,
                    })
    return issues


def _strings(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


__all__ = [
    "CLAIM_STATUSES",
    "FLOW_KINDS",
    "GoalClosureResultError",
    "SCHEMA_VERSION",
    "claim_set_issues",
    "normalize_goal_closure_result",
    "validate_goal_closure_result",
]
