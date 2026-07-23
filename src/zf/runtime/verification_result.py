"""Typed verifier output and legacy child-report adapter."""

from __future__ import annotations

from typing import Any, Mapping


SCHEMA_VERSION = "verification-result.v1"
COMPLETED_VERDICTS = frozenset({"passed", "rejected", "blocked"})
EXECUTION_STATUSES = frozenset({"completed", "failed"})
REQUIREMENT_STATUSES = frozenset({
    "passed",
    "failed",
    "blocked",
    "waived",
    "not_applicable",
})


class VerificationResultError(ValueError):
    """A verifier report cannot be used as a product verdict."""


def normalize_verification_result(
    payload: Mapping[str, Any],
    *,
    contract_snapshot: Mapping[str, Any],
    target_snapshot: Mapping[str, Any],
    default_owner: str = "task_verify",
    default_tier: str = "runtime",
    strict: bool = True,
    require_rework_items: bool = False,
) -> dict[str, Any]:
    """Normalize v1 output or adapt a legacy fanout child report.

    A verifier process failure is represented as ``failed + abstained`` and is
    never converted into a product rejection.
    """

    raw = payload.get("verification_result")
    if isinstance(raw, Mapping):
        result = dict(raw)
    else:
        result = _legacy_result(
            payload,
            contract_snapshot=contract_snapshot,
            default_owner=default_owner,
            default_tier=default_tier,
        )
    result.setdefault("schema_version", SCHEMA_VERSION)
    for key in (
        "workflow_run_id",
        "task_id",
        "contract_revision",
        "task_map_generation",
        "base_commit",
        "task_ref",
    ):
        result.setdefault(key, str(contract_snapshot.get(key) or ""))
    for key in (
        "contract_snapshot_ref",
        "contract_snapshot_digest",
        "target_snapshot_ref",
        "target_commit",
        "target_snapshot_digest",
    ):
        result.setdefault(key, str(target_snapshot.get(key) or ""))
    result.setdefault("verification_owner", default_owner)
    result.setdefault("verification_tier", default_tier)
    result.setdefault("summary", str(payload.get("summary") or ""))
    result.setdefault("findings", _list(payload.get("findings")))
    result.setdefault("evidence_refs", _string_list(payload.get("evidence_refs")))
    result.setdefault("reproduction_commands", _string_list(
        payload.get("reproduction_commands") or payload.get("verification_commands")
    ))
    result.setdefault("failure_class", _default_failure_class(result))
    result["reused_command_receipt_ids"] = _string_list(
        result.get("reused_command_receipt_ids")
    )
    result["probe_receipts"] = _list(result.get("probe_receipts"))
    result["rework_items"] = _normalize_rework_items(result.get("rework_items"))
    result["requirement_results"] = _normalize_requirement_results(
        result.get("requirement_results"),
        contract_snapshot=contract_snapshot,
        default_owner=str(result.get("verification_owner") or default_owner),
        default_tier=str(result.get("verification_tier") or default_tier),
    )
    _aggregate_requirement_evidence(result)
    _validate_requirement_coverage(result, contract_snapshot)
    validate_verification_result(
        result,
        strict=strict,
        require_rework_items=require_rework_items,
    )
    return result


def validate_verification_result(
    result: Mapping[str, Any],
    *,
    strict: bool = True,
    require_rework_items: bool = False,
) -> None:
    if str(result.get("schema_version") or "") != SCHEMA_VERSION:
        raise VerificationResultError("unsupported verification result schema")
    identity = (
        "workflow_run_id",
        "task_id",
        "contract_revision",
        "task_map_generation",
        "base_commit",
        "task_ref",
        "contract_snapshot_ref",
        "contract_snapshot_digest",
        "target_snapshot_ref",
        "target_commit",
        "target_snapshot_digest",
        "verification_owner",
        "verification_tier",
    )
    missing = [key for key in identity if not str(result.get(key) or "").strip()]
    if missing:
        raise VerificationResultError("verification result missing: " + ", ".join(missing))
    execution_status = str(result.get("execution_status") or "")
    verdict = str(result.get("verdict") or "")
    if execution_status not in EXECUTION_STATUSES:
        raise VerificationResultError(f"invalid execution_status {execution_status!r}")
    if execution_status == "failed":
        if verdict != "abstained":
            raise VerificationResultError("failed verifier execution must abstain")
        return
    if verdict not in COMPLETED_VERDICTS:
        raise VerificationResultError("completed verifier must pass, reject, or block")
    matrix = result.get("requirement_results")
    if not isinstance(matrix, list) or not matrix:
        raise VerificationResultError("completed verdict requires a requirement matrix")
    for index, item in enumerate(matrix):
        if not isinstance(item, Mapping):
            raise VerificationResultError(f"requirement_results[{index}] must be an object")
        required = (
            "acceptance_id",
            "status",
            "verification_owner",
            "verification_tier",
        )
        missing_item = [key for key in required if not str(item.get(key) or "").strip()]
        if missing_item:
            raise VerificationResultError(
                f"requirement_results[{index}] missing: {', '.join(missing_item)}"
            )
        status = str(item.get("status") or "")
        if status not in REQUIREMENT_STATUSES:
            raise VerificationResultError(
                f"requirement_results[{index}] has invalid status"
            )
        if strict and status == "passed" and not _string_list(item.get("evidence_refs")):
            raise VerificationResultError(
                f"requirement_results[{index}] passed without evidence"
            )
        if strict and status in {"failed", "blocked"}:
            if not _list(item.get("findings")):
                raise VerificationResultError(
                    f"requirement_results[{index}] {status} without findings"
                )
            if not _string_list(item.get("evidence_refs")):
                raise VerificationResultError(
                    f"requirement_results[{index}] {status} without evidence"
                )
    statuses = {str(item.get("status") or "") for item in matrix if isinstance(item, Mapping)}
    if verdict == "passed" and statuses & {"failed", "blocked"}:
        raise VerificationResultError("passed verdict contains failed or blocked requirements")
    if verdict == "rejected" and "failed" not in statuses:
        raise VerificationResultError("rejected verdict requires a failed requirement")
    if verdict == "blocked" and "blocked" not in statuses:
        raise VerificationResultError("blocked verdict requires a blocked requirement")
    if require_rework_items and verdict in {"rejected", "blocked"}:
        if not result.get("rework_items"):
            raise VerificationResultError(
                f"{verdict} verdict requires structured rework_items"
            )


def recovery_owner(result: Mapping[str, Any]) -> str:
    if str(result.get("execution_status") or "") == "failed":
        return "run_manager"
    verdict = str(result.get("verdict") or "")
    if verdict == "rejected":
        return "implementation_owner"
    if verdict == "blocked":
        return "dependency_owner"
    return "none"


def _default_failure_class(result: Mapping[str, Any]) -> str:
    if str(result.get("execution_status") or "") == "failed":
        return "verifier_execution_failure"
    verdict = str(result.get("verdict") or "")
    if verdict == "rejected":
        return "product_rejection"
    if verdict == "blocked":
        return "dependency_blocked"
    return "none"


def failed_acceptance_ids(result: Mapping[str, Any]) -> list[str]:
    matrix = result.get("requirement_results")
    if not isinstance(matrix, list):
        return []
    return [
        str(item.get("acceptance_id") or "")
        for item in matrix
        if isinstance(item, Mapping)
        and str(item.get("status") or "") in {"failed", "blocked"}
        and str(item.get("acceptance_id") or "")
    ]


def _legacy_result(
    payload: Mapping[str, Any],
    *,
    contract_snapshot: Mapping[str, Any],
    default_owner: str,
    default_tier: str,
) -> dict[str, Any]:
    report = payload.get("report") if isinstance(payload.get("report"), Mapping) else {}
    matrix = report.get("requirement_coverage_matrix")
    if not isinstance(matrix, list):
        matrix = payload.get("requirement_coverage_matrix")
    recommendation = str(
        report.get("recommendation")
        or report.get("verdict")
        or payload.get("verdict")
        or ""
    ).strip().lower()
    report_status = str(report.get("status") or payload.get("status") or "").strip().lower()
    has_product_report = bool(isinstance(matrix, list) and matrix) or recommendation in {
        "approve", "approved", "pass", "passed", "reject", "rejected", "block", "blocked",
    }
    if not has_product_report and report_status in {"failed", "error"}:
        execution_status = "failed"
        verdict = "abstained"
    else:
        execution_status = "completed"
        if recommendation in {"reject", "rejected"} or report_status in {"rejected"}:
            verdict = "rejected"
        elif recommendation in {"block", "blocked"} or report_status == "blocked":
            verdict = "blocked"
        elif report_status in {"failed", "error"}:
            verdict = "rejected"
        else:
            verdict = "passed"
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_status": execution_status,
        "verdict": verdict,
        "verification_owner": str(report.get("verification_owner") or default_owner),
        "verification_tier": str(report.get("verification_tier") or default_tier),
        "summary": str(report.get("summary") or payload.get("summary") or payload.get("reason") or ""),
        "findings": _list(report.get("findings") or payload.get("findings")),
        "evidence_refs": _string_list(report.get("evidence_refs") or payload.get("evidence_refs")),
        "reproduction_commands": _string_list(
            report.get("reproduction_commands")
            or payload.get("reproduction_commands")
            or payload.get("verification_commands")
        ),
        "requirement_results": matrix if isinstance(matrix, list) else [],
    }


def _normalize_requirement_results(
    raw: Any,
    *,
    contract_snapshot: Mapping[str, Any],
    default_owner: str,
    default_tier: str,
) -> list[dict[str, Any]]:
    source = raw if isinstance(raw, list) else []
    criteria = contract_snapshot.get("acceptance_criteria")
    criteria = criteria if isinstance(criteria, list) else []
    by_id = {
        str(item.get("acceptance_id") or ""): item
        for item in criteria
        if isinstance(item, Mapping) and str(item.get("acceptance_id") or "")
    }
    out: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        if not isinstance(item, Mapping):
            continue
        record = dict(item)
        acceptance_id = str(
            record.get("acceptance_id")
            or record.get("criterion_id")
            or record.get("id")
            or ""
        ).strip()
        if not acceptance_id and index < len(criteria) and isinstance(criteria[index], Mapping):
            acceptance_id = str(criteria[index].get("acceptance_id") or "")
        criterion = by_id.get(acceptance_id, {})
        raw_status = str(record.get("status") or record.get("verdict") or "").lower()
        status = {
            "pass": "passed",
            "approve": "passed",
            "approved": "passed",
            "fail": "failed",
            "reject": "failed",
            "rejected": "failed",
            "block": "blocked",
        }.get(raw_status, raw_status)
        out.append({
            "acceptance_id": acceptance_id,
            "status": status,
            "verification_owner": str(
                record.get("verification_owner")
                or criterion.get("verification_owner")
                or default_owner
            ),
            "verification_tier": str(
                record.get("verification_tier")
                or criterion.get("verification_tier")
                or default_tier
            ),
            "evidence_refs": _string_list(record.get("evidence_refs")),
            "findings": _list(record.get("findings")),
            "reproduction_commands": _string_list(
                record.get("reproduction_commands") or record.get("commands")
            ),
        })
    return out


def _normalize_rework_items(raw: Any) -> list[dict[str, Any]]:
    source = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        if not isinstance(item, Mapping):
            raise VerificationResultError(f"rework_items[{index}] must be an object")
        record = dict(item)
        status = str(record.get("status") or "").strip()
        if status not in {"missing", "incomplete", "incorrect", "unverified", "blocked"}:
            raise VerificationResultError(f"rework_items[{index}] has invalid status")
        required = (
            "rework_item_id",
            "acceptance_id",
            "expected",
            "observed",
            "required_delta",
            "done_when",
            "next_gate",
            "owner",
        )
        missing = [key for key in required if not str(record.get(key) or "").strip()]
        if missing:
            raise VerificationResultError(
                f"rework_items[{index}] missing: {', '.join(missing)}"
            )
        record["reproduction_command_ids"] = _string_list(
            record.get("reproduction_command_ids")
        )
        record["allowed_scope"] = _string_list(record.get("allowed_scope"))
        out.append(record)
    return out


def _validate_requirement_coverage(
    result: Mapping[str, Any],
    contract_snapshot: Mapping[str, Any],
) -> None:
    if str(result.get("execution_status") or "") == "failed":
        return
    criteria = contract_snapshot.get("acceptance_criteria")
    criteria = criteria if isinstance(criteria, list) else []
    mandatory = {
        str(item.get("acceptance_id") or "")
        for item in criteria
        if isinstance(item, Mapping)
        and bool(item.get("mandatory", True))
        and str(item.get("acceptance_id") or "")
    }
    matrix = result.get("requirement_results")
    matrix = matrix if isinstance(matrix, list) else []
    ids = [
        str(item.get("acceptance_id") or "")
        for item in matrix
        if isinstance(item, Mapping)
    ]
    duplicates = sorted({item for item in ids if item and ids.count(item) > 1})
    if duplicates:
        raise VerificationResultError(
            "requirement matrix contains duplicate acceptance ids: "
            + ", ".join(duplicates)
        )
    missing = sorted(mandatory - set(ids))
    unknown = sorted(set(ids) - {
        str(item.get("acceptance_id") or "")
        for item in criteria
        if isinstance(item, Mapping)
    })
    if missing:
        raise VerificationResultError(
            "requirement matrix misses mandatory acceptance ids: "
            + ", ".join(missing)
        )
    if unknown:
        raise VerificationResultError(
            "requirement matrix has unknown acceptance ids: "
            + ", ".join(unknown)
        )


def _aggregate_requirement_evidence(result: dict[str, Any]) -> None:
    """Promote row evidence into the report envelope without losing detail."""

    matrix = result.get("requirement_results")
    if not isinstance(matrix, list):
        return
    for key in ("evidence_refs", "reproduction_commands"):
        if _string_list(result.get(key)):
            continue
        values: list[str] = []
        for item in matrix:
            if not isinstance(item, Mapping):
                continue
            for value in _string_list(item.get(key)):
                if value not in values:
                    values.append(value)
        result[key] = values
    if _list(result.get("findings")):
        return
    findings: list[Any] = []
    for item in matrix:
        if not isinstance(item, Mapping):
            continue
        findings.extend(_list(item.get("findings")))
    result["findings"] = findings


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _list(value) if str(item).strip()]


__all__ = [
    "COMPLETED_VERDICTS",
    "EXECUTION_STATUSES",
    "REQUIREMENT_STATUSES",
    "SCHEMA_VERSION",
    "VerificationResultError",
    "failed_acceptance_ids",
    "normalize_verification_result",
    "recovery_owner",
    "validate_verification_result",
]
