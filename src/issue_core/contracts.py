"""Host-neutral contracts for the downloadable ``/issue`` skill.

The concrete ZaoFu implementation is only one host adapter.  This module is
intentionally limited to stdlib types and immutable value objects so another
project can implement the same flow without importing ``zf`` or knowing about
ZaoFu state directories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


ASSESSMENT_DECISION_SCHEMA = "assessment-decision.v1"


@dataclass(frozen=True)
class AssessmentDecision:
    """An explicit user decision bound to one exact Draft revision."""

    draft_id: str
    draft_revision: int
    decision: str
    actor_capability: str
    schema_version: str = ASSESSMENT_DECISION_SCHEMA

    def validate(self) -> None:
        if self.schema_version != ASSESSMENT_DECISION_SCHEMA:
            raise ValueError("unsupported assessment decision schema")
        if not self.draft_id or self.draft_revision < 1:
            raise ValueError("assessment decision must identify a Draft revision")
        if self.decision not in {"project_assessment", "limited_report"}:
            raise ValueError("unsupported assessment decision")
        if not self.actor_capability:
            raise ValueError("assessment decision requires actor capability")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "draft_id": self.draft_id,
            "draft_revision": self.draft_revision,
            "decision": self.decision,
            "actor_capability": self.actor_capability,
        }


class EnvironmentDetector(Protocol):
    def detect_environment(self) -> Mapping[str, str]: ...


class ProjectInspector(Protocol):
    def detect_project_version(self, project_root: Path) -> Mapping[str, str]: ...


class IssueUnderstandingAgent(Protocol):
    def understand(
        self,
        description: str,
        *,
        environment: Mapping[str, str],
        project_version: str,
    ) -> Mapping[str, Any]: ...


class ProjectAssessmentAgent(Protocol):
    """Read-only assessment agent supplied by the installing host."""

    def assess(
        self,
        *,
        project_root: Path,
        draft: Mapping[str, Any],
        allowed_paths: Sequence[str] = (),
    ) -> Mapping[str, Any]: ...


class HostAdapter(EnvironmentDetector, ProjectInspector, Protocol):
    """Capabilities a project supplies to the generic skill."""

    def source_revision(self, project_root: Path) -> str: ...

    def run_assessment(
        self,
        agent: ProjectAssessmentAgent,
        *,
        project_root: Path,
        draft: Mapping[str, Any],
        allowed_paths: Sequence[str] = (),
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ForgeIssuePayload:
    """Provider-neutral Issue write request; no provider SDK is required."""

    project: str
    title: str
    body: str
    labels: tuple[str, ...] = ()
    marker: str = ""
    attachments: tuple[Mapping[str, str], ...] = ()

    def validate(self) -> None:
        if not self.project or not self.title or not self.body:
            raise ValueError("Forge Issue payload requires project, title, and body")
        if not self.marker:
            raise ValueError("Forge Issue payload requires an idempotency marker")


@dataclass(frozen=True)
class PublishedIssueRef:
    provider: str
    project: str
    number: str
    url: str


FORGE_PUBLICATION_STATUSES = frozenset({
    "published", "partially_published", "publish_failed", "outcome_unknown",
})


@dataclass(frozen=True)
class ForgePublishResult:
    """Provider-neutral result for an idempotent Forge publication attempt."""

    status: str
    issue: PublishedIssueRef | None = None
    reason: str = ""

    def validate(self) -> None:
        if self.status not in FORGE_PUBLICATION_STATUSES:
            raise ValueError(f"unsupported Forge publication status: {self.status}")
        if self.status in {"published", "partially_published"} and self.issue is None:
            raise ValueError("published result requires the remote Issue reference")
        if self.issue is not None:
            if not self.issue.provider or not self.issue.project or not self.issue.number:
                raise ValueError("published Issue reference is incomplete")
            if not self.issue.url.startswith(("https://", "http://")):
                raise ValueError("published Issue reference must contain an absolute URL")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "status": self.status,
            "issue": (
                {
                    "provider": self.issue.provider,
                    "project": self.issue.project,
                    "number": self.issue.number,
                    "url": self.issue.url,
                }
                if self.issue is not None else None
            ),
            "reason": self.reason,
        }


class ForgeAdapter(Protocol):
    """Minimal publication/readback surface for GitHub, GitLab, or another Forge."""

    name: str

    def publish(
        self, payload: ForgeIssuePayload, *, credential: str,
    ) -> ForgePublishResult: ...

    def find_by_marker(
        self, project: str, marker: str, *, credential: str,
    ) -> Sequence[PublishedIssueRef]: ...

    def reconcile_issue_labels(
        self,
        project: str,
        issue_number: int,
        labels: Sequence[str],
        *,
        credential: str,
    ) -> ForgePublishResult: ...


__all__ = [
    "ASSESSMENT_DECISION_SCHEMA",
    "AssessmentDecision",
    "EnvironmentDetector",
    "ForgeAdapter",
    "FORGE_PUBLICATION_STATUSES",
    "ForgeIssuePayload",
    "ForgePublishResult",
    "HostAdapter",
    "IssueUnderstandingAgent",
    "ProjectAssessmentAgent",
    "ProjectInspector",
    "PublishedIssueRef",
]
