"""Admit truthful input/runner failures without granting a semantic verdict."""
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def incomplete_task_input_failure(schema: str, result: Mapping[str, Any]) -> bool:
    if schema not in {"implementation-result.v1", "verification-result.v1"}:
        return False
    failure_classes = {"reader_execution_failure"}
    if schema == "verification-result.v1":
        # Reporting an unavailable verifier does not evaluate product claims.
        # The result still passes identity, evidence and supplied-report checks.
        failure_classes.add("verifier_execution_failure")
    return (
        result.get("execution_status") == "failed"
        and result.get("verdict") in {"blocked", "abstained"}
        and result.get("failure_class") in failure_classes
        and bool(str(result.get("summary") or "").strip())
        and isinstance(result.get("evidence_refs"), list)
        and any(isinstance(ref, str) and ref.strip() for ref in result["evidence_refs"])
    )


def recovery_input_issues(state_dir: Path, *, policy: Mapping[str, Any],
                          schema: str, result: Mapping[str, Any],
                          ledger_descriptor: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """A runner failure may skip product reads, but not its pinned repair input.

    This proves only complete input production, never understanding or whether
    host evidence satisfies the product contract. Input failures remain reportable.
    """
    if (schema != 'verification-result.v1'
            or result.get('failure_class') != 'verifier_execution_failure'
            or not incomplete_task_input_failure(schema, result)):
        return []
    required = [row for row in policy.get('required_reads', [])
                if isinstance(row, Mapping)
                and row.get('source_id') == 'critical-reader-repair-context']
    if not required:
        return []
    from zf.runtime.artifact_read_ledger import active_read_ledger_path
    from zf.runtime.artifact_read_ledger_validation import (
        _parse_ledger_lines, required_read_satisfied, validate_required_reads,
    )
    if ledger_descriptor is not None:
        issues = validate_required_reads(state_dir,
            policy={**policy, 'required_reads': required}, ledger_descriptor=ledger_descriptor)
    else:
        # Preflight is read-only and combines the same fragments as final seal.
        attempt = str(policy.get('attempt_id') or '')
        try:
            active = active_read_ledger_path(state_dir, attempt)
            paths = [*sorted(active.parent.glob('read-ledger-*.jsonl')),
                     *([active] if active.exists() else [])]
            rows = []
            for path in paths:
                with path.open(encoding='utf-8') as handle:
                    rows.extend(_parse_ledger_lines(handle, expected_attempt_id=attempt))
            issues = [{'field': 'input_consumption.recovery', 'code': 'required_read_missing'}
                      for requirement in required if not required_read_satisfied(rows, requirement)]
        except (OSError, ValueError) as exc:
            return [{'field': 'input_consumption.recovery', 'code': 'invalid_ledger',
                     'message': str(exc)}]
    return [{**issue, 'code': 'recovery_input_unread',
             'message': 'Read every page of critical-reader-repair-context before reporting '
                        'a runner failure. Evaluate bound receipts independently; if input '
                        'reading itself fails, report reader_execution_failure with evidence.'}
            if issue.get('code') == 'required_read_missing' else issue for issue in issues]
