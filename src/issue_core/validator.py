"""Routing-aware validation for the final Issue Report.

Validation deliberately returns recovery information instead of a boolean so
the session worker can send the same agent back to investigation, ask the user
in one batch, reconcile conflicting evidence, or re-synthesize the body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ValidationFailure:
    type: str
    field: str
    message: str
    recoverable_by: str

    def to_dict(self) -> dict[str, str]:
        return {
            "type": self.type,
            "field": self.field,
            "message": self.message,
            "recoverable_by": self.recoverable_by,
        }


@dataclass(frozen=True)
class ValidationResult:
    status: str
    failures: tuple[ValidationFailure, ...] = ()

    def validate(self) -> None:
        if self.status not in {"pass", "fail"}:
            raise ValueError("unsupported validation status")
        for failure in self.failures:
            if not all((failure.type, failure.field, failure.message, failure.recoverable_by)):
                raise ValueError("validation failure is incomplete")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "status": self.status,
            "failures": [failure.to_dict() for failure in self.failures],
        }


def validate_issue_report(
    fields: Mapping[str, Any],
    report: Mapping[str, Any],
    evidences: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> ValidationResult:
    failures: list[ValidationFailure] = []
    for field in ("title", "description", "reproduction_steps", "actual_behavior", "expected_behavior"):
        if not str(report.get(field) or fields.get(field) or "").strip():
            failures.append(ValidationFailure(
                "malformed/incomplete_body", field,
                f"Issue body field {field} is missing.", "re_synthesize",
            ))
    if not evidences:
        failures.append(ValidationFailure(
            "missing_evidence", "evidence",
            "Issue report has no repository or runtime evidence.", "investigation",
        ))
    result = ValidationResult("fail" if failures else "pass", tuple(failures))
    result.validate()
    return result


__all__ = ["ValidationFailure", "ValidationResult", "validate_issue_report"]
