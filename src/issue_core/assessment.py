"""Provider-neutral result contract for an optional project assessment agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ASSESSMENT_RESULT_SCHEMA = "project-assessment-result.v1"
ASSESSMENT_RESULT_STATUSES = frozenset({"completed", "failed", "not_run"})


@dataclass(frozen=True)
class AssessmentFinding:
    summary: str
    kind: str = "observation"
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        if not self.summary or not self.kind:
            raise ValueError("assessment finding requires summary and kind")
        return {
            "kind": self.kind,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class ProjectAssessmentResult:
    status: str
    summary: str = ""
    findings: tuple[AssessmentFinding, ...] = ()
    reason: str = ""
    schema_version: str = ASSESSMENT_RESULT_SCHEMA

    def validate(self) -> None:
        if self.schema_version != ASSESSMENT_RESULT_SCHEMA:
            raise ValueError("unsupported project assessment schema")
        if self.status not in ASSESSMENT_RESULT_STATUSES:
            raise ValueError("unsupported project assessment status")
        if not isinstance(self.summary, str) or len(self.summary) > 12000:
            raise ValueError("assessment summary exceeds its contract")
        if not isinstance(self.reason, str) or len(self.reason) > 2000:
            raise ValueError("assessment reason exceeds its contract")
        for finding in self.findings:
            finding.to_dict()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "reason": self.reason,
        }


__all__ = [
    "ASSESSMENT_RESULT_SCHEMA",
    "ASSESSMENT_RESULT_STATUSES",
    "AssessmentFinding",
    "ProjectAssessmentResult",
]
