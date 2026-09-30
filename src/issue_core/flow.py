"""Small, host-neutral orchestration for the generic ``/issue`` skill.

This is intentionally a pure in-process flow.  Persistence, permissions,
assessment execution, and Forge writes are injected by the installing host.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import AssessmentDecision, IssueUnderstandingAgent
from .draft import IssueDraft
from .environment import detect_environment, detect_project_version
from .normalizer import normalize_unstructured_report
from .publication import PublicationSnapshot, PublicationState


@dataclass(frozen=True)
class IssueFlow:
    """Compose the generic stages without assuming a host runtime."""

    understanding_agent: IssueUnderstandingAgent | None = None

    def create_draft(
        self,
        *,
        draft_id: str,
        description: str,
        project_root: Path | None = None,
        environment: Mapping[str, str] | None = None,
        project_version: str = "",
    ) -> IssueDraft:
        environment_values = dict(environment or {})
        if not str(environment_values.get("os") or "").strip() or not str(
            environment_values.get("version") or ""
        ).strip():
            environment_values = detect_environment()
        detected_version = ""
        if not project_version and project_root is not None:
            detected_version = str(
                detect_project_version(Path(project_root)).get("value") or ""
            )
        effective_version = project_version or detected_version
        if self.understanding_agent is None:
            proposal = normalize_unstructured_report(
                description,
                environment=environment_values,
                project_version=effective_version,
            )
        else:
            proposal = dict(self.understanding_agent.understand(
                description,
                environment=environment_values,
                project_version=effective_version,
            ))
        draft = IssueDraft(
            draft_id=draft_id,
            title=str(proposal.get("title") or "Untitled Issue"),
            bug_description=str(proposal.get("bug_description") or ""),
            reproduction_steps=str(proposal.get("reproduction_steps") or ""),
            actual_behavior=str(proposal.get("actual_behavior") or ""),
            expected_behavior=str(proposal.get("expected_behavior") or ""),
            attachment_context=str(proposal.get("attachment_context") or ""),
            environment={
                str(key): str(value)
                for key, value in (proposal.get("environment") or {}).items()
            },
            project_version=str(proposal.get("project_version") or effective_version or ""),
            additional_context=str(proposal.get("additional_context") or ""),
        )
        draft.validate()
        return draft

    @staticmethod
    def record_assessment_decision(
        draft: IssueDraft,
        *,
        decision: str,
        actor_capability: str,
    ) -> AssessmentDecision:
        value = AssessmentDecision(
            draft_id=draft.draft_id,
            draft_revision=draft.revision,
            decision=decision,
            actor_capability=actor_capability,
        )
        value.validate()
        return value

    @staticmethod
    def assessment_is_dispatchable(
        draft: IssueDraft, decision: AssessmentDecision,
    ) -> bool:
        """Return true only for a matching explicit project-assessment choice."""

        decision.validate()
        return (
            decision.draft_id == draft.draft_id
            and decision.draft_revision == draft.revision
            and decision.decision == "project_assessment"
        )

    @staticmethod
    def publication(
        draft: IssueDraft, *, provider: str, payload_digest: str,
    ) -> PublicationState:
        snapshot = PublicationSnapshot(
            draft_id=draft.draft_id,
            draft_revision=draft.revision,
            provider=provider,
            payload_digest=payload_digest,
        )
        return PublicationState(snapshot)


__all__ = ["IssueFlow"]
