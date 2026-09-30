"""Immutable, provider-neutral snapshot for a confirmed Issue Report.

The snapshot is deliberately separate from the mutable Working Set.  It is the
exact object a host may hand to a publisher after the user confirms the final
report.  Creating it has no remote side effect.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

from .session import AttachmentRef, IssueSession, IssueWorkingSet, stable_digest


REPORT_SNAPSHOT_SCHEMA = "issue-report-publication-snapshot.v1"


@dataclass(frozen=True)
class IssueReportSnapshot:
    session_id: str
    session_revision: int
    working_set_digest: str
    report_digest: str
    evidence_digest: str
    attachment_manifest_digest: str
    report: Mapping[str, Any]
    provider: str = ""
    target: str = ""
    disclosure_policy: str = "handoff_private"
    selected_claim_refs: tuple[str, ...] = ()
    selected_evidence_refs: tuple[str, ...] = ()
    confirmed_by: str = ""
    confirmed_at: str = ""
    schema_version: str = REPORT_SNAPSHOT_SCHEMA
    digest: str = ""

    @classmethod
    def from_session(
        cls,
        session: IssueSession,
        working_set: IssueWorkingSet,
        *,
        confirmed_by: str,
        confirmed_at: str,
        provider: str = "",
        target: str = "",
        disclosure_policy: str = "handoff_private",
    ) -> "IssueReportSnapshot":
        if session.session_id != working_set.session_id:
            raise ValueError("Issue Report snapshot session mismatch")
        if session.revision != working_set.revision:
            raise ValueError("Issue Report snapshot revision mismatch")
        report = dict(working_set.report)
        return cls(
            session_id=session.session_id,
            session_revision=session.revision,
            working_set_digest=working_set.with_digest().digest,
            report_digest=stable_digest(report),
            evidence_digest=stable_digest(list(working_set.evidences)),
            attachment_manifest_digest=stable_digest(
                [item.to_dict() for item in session.attachments]
            ),
            report=report,
            provider=str(provider or ""),
            target=str(target or ""),
            disclosure_policy=str(disclosure_policy or "handoff_private"),
            selected_claim_refs=tuple(
                str(item["ref"])
                for item in working_set.claims
                if isinstance(item, Mapping) and item.get("ref")
            ),
            selected_evidence_refs=tuple(
                str(item["ref"])
                for item in working_set.evidences
                if isinstance(item, Mapping) and item.get("ref")
            ),
            confirmed_by=str(confirmed_by or ""),
            confirmed_at=str(confirmed_at or ""),
        ).with_digest()

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "session_revision": self.session_revision,
            "working_set_digest": self.working_set_digest,
            "report_digest": self.report_digest,
            "evidence_digest": self.evidence_digest,
            "attachment_manifest_digest": self.attachment_manifest_digest,
            "report": dict(self.report),
            "provider": self.provider,
            "target": self.target,
            "disclosure_policy": self.disclosure_policy,
            "selected_claim_refs": list(self.selected_claim_refs),
            "selected_evidence_refs": list(self.selected_evidence_refs),
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
        }

    def with_digest(self) -> "IssueReportSnapshot":
        return replace(self, digest=stable_digest(self._payload()))

    def validate(self) -> None:
        if self.schema_version != REPORT_SNAPSHOT_SCHEMA:
            raise ValueError("unsupported Issue Report snapshot schema")
        if not self.session_id or self.session_revision < 1:
            raise ValueError("Issue Report snapshot requires a session and revision")
        for name in (
            "working_set_digest", "report_digest", "evidence_digest",
            "attachment_manifest_digest", "digest",
        ):
            if not str(getattr(self, name) or "").startswith("sha256:"):
                raise ValueError(f"{name} must be a sha256 digest")
        if self.report_digest != stable_digest(dict(self.report)):
            raise ValueError("Issue Report snapshot report digest mismatch")
        if self.disclosure_policy not in {"handoff_private", "block", "public"}:
            raise ValueError("unsupported disclosure policy")
        if not self.confirmed_by or not self.confirmed_at:
            raise ValueError("confirmed Issue Report snapshot requires actor and time")
        if not all(self.selected_claim_refs) or not all(self.selected_evidence_refs):
            raise ValueError("snapshot refs must contain ids")
        if self.with_digest().digest != self.digest:
            raise ValueError("Issue Report snapshot digest mismatch")

    def to_dict(self) -> dict[str, Any]:
        value = self.with_digest()
        value.validate()
        return {
            **asdict(value),
            "selected_claim_refs": list(value.selected_claim_refs),
            "selected_evidence_refs": list(value.selected_evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IssueReportSnapshot":
        snapshot = cls(
            **{
                **dict(value),
                "selected_claim_refs": tuple(value.get("selected_claim_refs") or ()),
                "selected_evidence_refs": tuple(value.get("selected_evidence_refs") or ()),
            },
        )
        snapshot.validate()
        return snapshot


def attachment_manifest_digest(attachments: Sequence[AttachmentRef]) -> str:
    """Return the canonical digest for an attachment manifest."""

    return stable_digest([item.to_dict() for item in attachments])


__all__ = ["IssueReportSnapshot", "REPORT_SNAPSHOT_SCHEMA", "attachment_manifest_digest"]
