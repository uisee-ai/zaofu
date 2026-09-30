"""Host-neutral contracts for the Issue Enrichment Loop.

The records in this module describe semantics only.  They do not know where a
host stores artifacts, how an agent runs, or which Forge receives an Issue.
Large evidence content is deliberately represented by immutable URI/digest
references rather than embedded in these values.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping


CLAIM_KINDS = frozenset({"reported", "observed", "inferred", "normative"})
CLAIM_VERIFICATION_STATUSES = frozenset({
    "unverified", "verified", "contradicted", "stale", "superseded",
})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
EVIDENCE_INTEGRITIES = frozenset({"verified", "invalid"})
EVIDENCE_APPLICABILITIES = frozenset({
    "current", "stale", "historical", "superseded",
})
EVIDENCE_DISCLOSURES = frozenset({"private", "summary_only", "publishable"})
GAP_STATUSES = frozenset({"missing", "unknown", "conflicting", "stale"})
GAP_ACQUISITIONS = frozenset({
    "derive_from_report", "inspect_attachment", "search_code",
    "run_reproduction", "read_normative_source", "ask_user", "leave_unknown",
})
GAP_BLOCKING_LEVELS = frozenset({
    "informational", "blocks_readiness", "blocks_publication", "blocks_fix",
})
SUGGESTION_STATUSES = frozenset({"pending", "accepted", "merged", "stale", "withdrawn"})
CONFLICT_STATUSES = frozenset({
    "unresolved", "resolved_left", "resolved_right", "resolved_both",
    "needs_reinvestigation",
})
RESOLUTION_ACTIONS = frozenset({
    "accepted", "merged", "resolved_left", "resolved_right", "resolved_both",
    "needs_reinvestigation",
})
EVALUATION_STATUSES = frozenset({
    "running", "completed", "failed", "timed_out", "cancelled",
})
FIELD_SOURCES = frozenset({"unknown", "reporter", "structure", "investigation"})


def stable_content_digest(value: Any) -> str:
    """Return the canonical digest used by revision-bound compare-and-swap."""

    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_schema(value: Mapping[str, Any], expected: str) -> None:
    if str(value.get("schema_version") or "") != expected:
        raise ValueError(f"unsupported schema version; expected {expected}")


def _require_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _require_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{name} must be a sha256 digest")


@dataclass(frozen=True)
class DraftFieldState:
    field_path: str
    value: Any = ""
    source: str = "unknown"
    user_locked: bool = False
    confidence: str = "low"
    evidence_refs: tuple[str, ...] = ()
    last_changed_revision: int = 1
    schema_version: str = "draft-field-state.v1"

    def validate(self) -> None:
        if self.schema_version != "draft-field-state.v1":
            raise ValueError("unsupported Draft field state schema")
        _require_id(self.field_path, "field_path")
        if self.source not in FIELD_SOURCES:
            raise ValueError("unsupported Draft field source")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError("unsupported Draft field confidence")
        if self.last_changed_revision < 1:
            raise ValueError("last_changed_revision must be positive")
        if not all(isinstance(item, str) and item for item in self.evidence_refs):
            raise ValueError("evidence_refs must contain ids")

    @property
    def auto_fill_eligible(self) -> bool:
        """A field can be auto-filled only once while empty and never user-edited."""

        return not self.user_locked and self.value in {"", None}

    def user_edit(self, value: Any, *, revision: int | None = None) -> "DraftFieldState":
        updated = replace(
            self,
            value=value,
            source="reporter",
            user_locked=True,
            confidence="high",
            last_changed_revision=revision or self.last_changed_revision + 1,
        )
        updated.validate()
        return updated

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DraftFieldState":
        _require_schema(value, "draft-field-state.v1")
        item = cls(
            field_path=str(value.get("field_path") or ""),
            value=value.get("value"),
            source=str(value.get("source") or "unknown"),
            user_locked=bool(value.get("user_locked")),
            confidence=str(value.get("confidence") or "low"),
            evidence_refs=tuple(value.get("evidence_refs") or ()),
            last_changed_revision=int(value.get("last_changed_revision") or 0),
            schema_version=str(value.get("schema_version") or ""),
        )
        item.validate()
        return item


@dataclass(frozen=True)
class IssueDraftV3:
    """Immutable semantic Draft projection used by the portable v3 contract."""

    draft_id: str
    revision: int
    digest: str
    fields: Mapping[str, DraftFieldState]
    claim_refs: tuple[str, ...] = ()
    understanding_status: str = "completed"
    schema_version: str = "issue-draft.v3"

    @classmethod
    def create(
        cls,
        *,
        draft_id: str,
        fields: Mapping[str, DraftFieldState],
        understanding_status: str,
        claim_refs: tuple[str, ...] = (),
    ) -> "IssueDraftV3":
        values = {
            str(name): field
            for name, field in fields.items()
        }
        draft = cls(
            draft_id=draft_id,
            revision=1,
            digest=stable_content_digest({
                name: field.value for name, field in values.items()
            }),
            fields=values,
            claim_refs=claim_refs,
            understanding_status=understanding_status,
        )
        draft.validate()
        return draft

    def validate(self) -> None:
        if self.schema_version != "issue-draft.v3":
            raise ValueError("unsupported Issue Draft schema")
        _require_id(self.draft_id, "draft_id")
        if self.revision < 1:
            raise ValueError("Draft revision must be positive")
        if self.understanding_status not in {"completed", "fallback", "failed"}:
            raise ValueError("unsupported Draft understanding status")
        for name, field in self.fields.items():
            if name != field.field_path:
                raise ValueError("Draft field key and field_path must match")
            field.validate()
        expected = stable_content_digest({
            name: field.value for name, field in self.fields.items()
        })
        if self.digest != expected:
            raise ValueError("Draft digest does not match field content")
        if not all(isinstance(item, str) and item for item in self.claim_refs):
            raise ValueError("Draft claim_refs must contain ids")

    def user_edit(
        self, *, expected_revision: int, changes: Mapping[str, Any],
    ) -> "IssueDraftV3":
        if expected_revision != self.revision:
            raise ValueError("Draft revision changed")
        next_revision = self.revision + 1
        fields = dict(self.fields)
        for name, value in changes.items():
            current = fields.get(name) or DraftFieldState(field_path=name)
            fields[name] = current.user_edit(value, revision=next_revision)
        updated = replace(
            self,
            revision=next_revision,
            digest=stable_content_digest({
                name: field.value for name, field in fields.items()
            }),
            fields=fields,
        )
        updated.validate()
        return updated

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "draft_id": self.draft_id,
            "revision": self.revision,
            "digest": self.digest,
            "fields": {
                name: field.to_dict() for name, field in self.fields.items()
            },
            "claim_refs": list(self.claim_refs),
            "understanding_status": self.understanding_status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IssueDraftV3":
        _require_schema(value, "issue-draft.v3")
        draft = cls(
            draft_id=str(value.get("draft_id") or ""),
            revision=int(value.get("revision") or 0),
            digest=str(value.get("digest") or ""),
            fields={
                str(name): DraftFieldState.from_dict(field)
                for name, field in dict(value.get("fields") or {}).items()
            },
            claim_refs=tuple(value.get("claim_refs") or ()),
            understanding_status=str(value.get("understanding_status") or ""),
            schema_version=str(value.get("schema_version") or ""),
        )
        draft.validate()
        return draft


@dataclass(frozen=True)
class DraftRevision:
    draft_id: str
    revision: int
    parent_revision: int | None
    actor: str
    changed_fields: tuple[str, ...]
    change_reason: str
    content_digest: str
    created_at: str
    resolution_id: str = ""
    schema_version: str = "issue-draft-revision.v1"

    def validate(self) -> None:
        if self.schema_version != "issue-draft-revision.v1":
            raise ValueError("unsupported Draft revision schema")
        _require_id(self.draft_id, "draft_id")
        _require_id(self.actor, "actor")
        _require_id(self.change_reason, "change_reason")
        _require_id(self.created_at, "created_at")
        _require_digest(self.content_digest, "content_digest")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.parent_revision is not None and self.parent_revision != self.revision - 1:
            raise ValueError("parent_revision must identify the previous revision")
        if not self.changed_fields or not all(self.changed_fields):
            raise ValueError("Draft revision requires changed_fields")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {**asdict(self), "changed_fields": list(self.changed_fields)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DraftRevision":
        _require_schema(value, "issue-draft-revision.v1")
        item = cls(
            draft_id=str(value.get("draft_id") or ""),
            revision=int(value.get("revision") or 0),
            parent_revision=(
                int(value["parent_revision"])
                if value.get("parent_revision") is not None else None
            ),
            actor=str(value.get("actor") or ""),
            changed_fields=tuple(value.get("changed_fields") or ()),
            change_reason=str(value.get("change_reason") or ""),
            content_digest=str(value.get("content_digest") or ""),
            created_at=str(value.get("created_at") or ""),
            resolution_id=str(value.get("resolution_id") or ""),
            schema_version=str(value.get("schema_version") or ""),
        )
        item.validate()
        return item


@dataclass(frozen=True)
class Claim:
    claim_id: str
    draft_id: str
    field_path: str
    value: Any
    kind: str
    source: Mapping[str, str]
    confidence: str
    verification_status: str
    valid_from_revision: int
    dependency_refs: tuple[str, ...]
    created_at: str
    invalidated_at_revision: int | None = None
    schema_version: str = "claim.v1"

    def validate(self) -> None:
        if self.schema_version != "claim.v1":
            raise ValueError("unsupported Claim schema")
        for value, name in (
            (self.claim_id, "claim_id"), (self.draft_id, "draft_id"),
            (self.field_path, "field_path"), (self.created_at, "created_at"),
        ):
            _require_id(value, name)
        if self.kind not in CLAIM_KINDS:
            raise ValueError("unsupported Claim kind")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError("unsupported Claim confidence")
        if self.verification_status not in CLAIM_VERIFICATION_STATUSES:
            raise ValueError("unsupported Claim verification status")
        if self.valid_from_revision < 1:
            raise ValueError("valid_from_revision must be positive")
        if not isinstance(self.source, Mapping) or not str(self.source.get("type") or ""):
            raise ValueError("Claim source type is required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "source": dict(self.source),
            "dependency_refs": list(self.dependency_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Claim":
        _require_schema(value, "claim.v1")
        item = cls(
            claim_id=str(value.get("claim_id") or ""),
            draft_id=str(value.get("draft_id") or ""),
            field_path=str(value.get("field_path") or ""),
            value=value.get("value"),
            kind=str(value.get("kind") or ""),
            source=dict(value.get("source") or {}),
            confidence=str(value.get("confidence") or ""),
            verification_status=str(value.get("verification_status") or ""),
            valid_from_revision=int(value.get("valid_from_revision") or 0),
            dependency_refs=tuple(value.get("dependency_refs") or ()),
            created_at=str(value.get("created_at") or ""),
            invalidated_at_revision=(
                int(value["invalidated_at_revision"])
                if value.get("invalidated_at_revision") is not None else None
            ),
            schema_version=str(value.get("schema_version") or ""),
        )
        item.validate()
        return item


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    kind: str
    uri: str
    digest: str
    collected_by: str
    project_revision: str
    captured_at: str
    disclosure: str
    redaction_status: str
    integrity: str
    applicability: str
    environment_fingerprint: str
    supports: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()
    candidate_digest: str = ""
    stale_reason: str = ""
    superseded_by: str = ""
    schema_version: str = "evidence.v1"

    def validate(self) -> None:
        if self.schema_version != "evidence.v1":
            raise ValueError("unsupported Evidence schema")
        for value, name in (
            (self.evidence_id, "evidence_id"), (self.kind, "kind"),
            (self.uri, "uri"), (self.collected_by, "collected_by"),
            (self.captured_at, "captured_at"),
        ):
            _require_id(value, name)
        _require_digest(self.digest, "digest")
        if self.environment_fingerprint:
            _require_digest(self.environment_fingerprint, "environment_fingerprint")
        if self.candidate_digest:
            _require_digest(self.candidate_digest, "candidate_digest")
        if self.integrity not in EVIDENCE_INTEGRITIES:
            raise ValueError("unsupported Evidence integrity")
        if self.applicability not in EVIDENCE_APPLICABILITIES:
            raise ValueError("unsupported Evidence applicability")
        if self.disclosure not in EVIDENCE_DISCLOSURES:
            raise ValueError("unsupported Evidence disclosure")
        if self.applicability == "stale" and not self.stale_reason:
            raise ValueError("stale Evidence requires a reason")
        if self.applicability == "superseded" and not self.superseded_by:
            raise ValueError("superseded Evidence requires superseded_by")

    def mark_stale(self, reason: str) -> "Evidence":
        updated = replace(self, applicability="stale", stale_reason=str(reason).strip())
        updated.validate()
        return updated

    def mark_superseded(self, evidence_id: str) -> "Evidence":
        updated = replace(
            self, applicability="superseded", superseded_by=evidence_id,
            stale_reason="",
        )
        updated.validate()
        return updated

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "supports": list(self.supports),
            "contradicts": list(self.contradicts),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Evidence":
        _require_schema(value, "evidence.v1")
        item = cls(
            evidence_id=str(value.get("evidence_id") or ""),
            kind=str(value.get("kind") or ""),
            uri=str(value.get("uri") or ""),
            digest=str(value.get("digest") or ""),
            collected_by=str(value.get("collected_by") or ""),
            project_revision=str(value.get("project_revision") or ""),
            captured_at=str(value.get("captured_at") or ""),
            disclosure=str(value.get("disclosure") or ""),
            redaction_status=str(value.get("redaction_status") or ""),
            integrity=str(value.get("integrity") or ""),
            applicability=str(value.get("applicability") or ""),
            environment_fingerprint=str(value.get("environment_fingerprint") or ""),
            supports=tuple(value.get("supports") or ()),
            contradicts=tuple(value.get("contradicts") or ()),
            candidate_digest=str(value.get("candidate_digest") or ""),
            stale_reason=str(value.get("stale_reason") or ""),
            superseded_by=str(value.get("superseded_by") or ""),
            schema_version=str(value.get("schema_version") or ""),
        )
        item.validate()
        return item


@dataclass(frozen=True)
class Gap:
    gap_id: str
    draft_id: str
    field_path: str
    status: str
    acquisition: str
    blocking_level: str
    reason: str
    attempts: int = 0
    schema_version: str = "gap.v1"

    def validate(self) -> None:
        if self.schema_version != "gap.v1":
            raise ValueError("unsupported Gap schema")
        for value, name in (
            (self.gap_id, "gap_id"), (self.draft_id, "draft_id"),
            (self.field_path, "field_path"), (self.reason, "reason"),
        ):
            _require_id(value, name)
        if self.status not in GAP_STATUSES:
            raise ValueError("unsupported Gap status")
        if self.acquisition not in GAP_ACQUISITIONS:
            raise ValueError("unsupported Gap acquisition")
        if self.blocking_level not in GAP_BLOCKING_LEVELS:
            raise ValueError("unsupported Gap blocking level")
        if self.attempts < 0:
            raise ValueError("Gap attempts must not be negative")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Gap":
        _require_schema(value, "gap.v1")
        item = cls(**dict(value))
        item.validate()
        return item


@dataclass(frozen=True)
class Suggestion:
    suggestion_id: str
    draft_id: str
    based_on_revision: int
    based_on_digest: str
    field_path: str
    current_value: Any
    proposed_value: Any
    reason: str
    claim_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    confidence: str
    status: str = "pending"
    schema_version: str = "suggestion.v1"

    def validate(self) -> None:
        if self.schema_version != "suggestion.v1":
            raise ValueError("unsupported Suggestion schema")
        for value, name in (
            (self.suggestion_id, "suggestion_id"), (self.draft_id, "draft_id"),
            (self.field_path, "field_path"), (self.reason, "reason"),
        ):
            _require_id(value, name)
        if self.based_on_revision < 1:
            raise ValueError("Suggestion based_on_revision must be positive")
        _require_digest(self.based_on_digest, "based_on_digest")
        if self.status not in SUGGESTION_STATUSES:
            raise ValueError("unsupported suggestion status")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError("unsupported Suggestion confidence")
        if not self.claim_refs:
            raise ValueError("Suggestion requires at least one Claim")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            **asdict(self),
            "claim_refs": list(self.claim_refs),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Suggestion":
        _require_schema(value, "suggestion.v1")
        item = cls(
            **{
                **dict(value),
                "claim_refs": tuple(value.get("claim_refs") or ()),
                "evidence_refs": tuple(value.get("evidence_refs") or ()),
            },
        )
        item.validate()
        return item


@dataclass(frozen=True)
class SuggestionDismissal:
    """Content-free audit value; never store the dismissed candidate text."""

    suggestion_id: str
    draft_id: str
    actor: str
    revision: int
    dismissed_at: str
    status: str = "dismissed"
    schema_version: str = "suggestion-dismissal.v1"

    def validate(self) -> None:
        if self.schema_version != "suggestion-dismissal.v1" or self.status != "dismissed":
            raise ValueError("unsupported Suggestion dismissal schema")
        for value, name in (
            (self.suggestion_id, "suggestion_id"), (self.draft_id, "draft_id"),
            (self.actor, "actor"), (self.dismissed_at, "dismissed_at"),
        ):
            _require_id(value, name)
        if self.revision < 1:
            raise ValueError("dismissal revision must be positive")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "suggestion_id": self.suggestion_id,
            "draft_id": self.draft_id,
            "actor": self.actor,
            "revision": self.revision,
            "status": self.status,
            "dismissed_at": self.dismissed_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SuggestionDismissal":
        _require_schema(value, "suggestion-dismissal.v1")
        item = cls(**dict(value))
        item.validate()
        return item


def dismiss_suggestion(
    suggestion: Suggestion,
    *,
    actor: str,
    current_revision: int,
    dismissed_at: str,
) -> SuggestionDismissal:
    suggestion.validate()
    dismissal = SuggestionDismissal(
        suggestion_id=suggestion.suggestion_id,
        draft_id=suggestion.draft_id,
        actor=actor,
        revision=current_revision,
        dismissed_at=dismissed_at,
    )
    dismissal.validate()
    return dismissal


@dataclass(frozen=True)
class Conflict:
    conflict_id: str
    draft_id: str
    based_on_revision: int
    based_on_digest: str
    field_path: str
    left_claim_ref: str
    right_claim_ref: str
    reason: str
    status: str = "unresolved"
    schema_version: str = "conflict.v1"

    def validate(self) -> None:
        if self.schema_version != "conflict.v1":
            raise ValueError("unsupported Conflict schema")
        for value, name in (
            (self.conflict_id, "conflict_id"), (self.draft_id, "draft_id"),
            (self.field_path, "field_path"), (self.left_claim_ref, "left_claim_ref"),
            (self.right_claim_ref, "right_claim_ref"), (self.reason, "reason"),
        ):
            _require_id(value, name)
        if self.based_on_revision < 1:
            raise ValueError("Conflict based_on_revision must be positive")
        _require_digest(self.based_on_digest, "based_on_digest")
        if self.status not in CONFLICT_STATUSES:
            raise ValueError("unsupported Conflict status")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Conflict":
        _require_schema(value, "conflict.v1")
        item = cls(**dict(value))
        item.validate()
        return item


@dataclass(frozen=True)
class Resolution:
    resolution_id: str
    suggestion_or_conflict_id: str
    action: str
    resolved_by: str
    based_on_revision: int
    resulting_revision: int
    created_at: str
    schema_version: str = "resolution.v1"

    def validate(self) -> None:
        if self.schema_version != "resolution.v1":
            raise ValueError("unsupported Resolution schema")
        for value, name in (
            (self.resolution_id, "resolution_id"),
            (self.suggestion_or_conflict_id, "suggestion_or_conflict_id"),
            (self.resolved_by, "resolved_by"), (self.created_at, "created_at"),
        ):
            _require_id(value, name)
        if self.action not in RESOLUTION_ACTIONS:
            raise ValueError("unsupported Resolution action")
        if self.based_on_revision < 1 or self.resulting_revision <= self.based_on_revision:
            raise ValueError("Resolution must produce a later Draft revision")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Resolution":
        _require_schema(value, "resolution.v1")
        item = cls(**dict(value))
        item.validate()
        return item


@dataclass(frozen=True)
class EvaluationRun:
    run_id: str
    draft_id: str
    draft_revision: int
    draft_digest: str
    status: str
    commit_capability: str
    started_at: str
    completed_at: str = ""
    failure_reason: str = ""
    schema_version: str = "evaluation-run.v1"

    @classmethod
    def start(
        cls,
        *,
        run_id: str,
        draft_id: str,
        draft_revision: int,
        draft_digest: str,
        commit_capability: str,
        started_at: str,
    ) -> "EvaluationRun":
        run = cls(
            run_id=run_id,
            draft_id=draft_id,
            draft_revision=draft_revision,
            draft_digest=draft_digest,
            status="running",
            commit_capability=commit_capability,
            started_at=started_at,
        )
        run.validate()
        return run

    def validate(self) -> None:
        if self.schema_version != "evaluation-run.v1":
            raise ValueError("unsupported Evaluation Run schema")
        for value, name in (
            (self.run_id, "run_id"), (self.draft_id, "draft_id"),
            (self.started_at, "started_at"),
        ):
            _require_id(value, name)
        if self.draft_revision < 1:
            raise ValueError("Evaluation Run draft_revision must be positive")
        _require_digest(self.draft_digest, "draft_digest")
        if self.status not in EVALUATION_STATUSES:
            raise ValueError("unsupported Evaluation Run status")
        if self.status == "running" and not self.commit_capability:
            raise ValueError("running Evaluation Run requires commit capability")
        if self.status != "running" and self.commit_capability:
            raise ValueError("terminal Evaluation Run must revoke commit capability")

    def can_commit(
        self, *, capability: str, draft_revision: int, draft_digest: str,
    ) -> bool:
        return (
            self.status == "running"
            and bool(self.commit_capability)
            and capability == self.commit_capability
            and draft_revision == self.draft_revision
            and draft_digest == self.draft_digest
        )

    def finish(self, *, status: str, completed_at: str, reason: str = "") -> "EvaluationRun":
        if status not in EVALUATION_STATUSES - {"running"}:
            raise ValueError("Evaluation Run finish requires a terminal status")
        updated = replace(
            self,
            status=status,
            commit_capability="",
            completed_at=completed_at,
            failure_reason=reason,
        )
        updated.validate()
        return updated

    def cancel(self, *, cancelled_at: str) -> "EvaluationRun":
        return self.finish(status="cancelled", completed_at=cancelled_at)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvaluationRun":
        _require_schema(value, "evaluation-run.v1")
        item = cls(**dict(value))
        item.validate()
        return item


__all__ = [
    "CLAIM_KINDS", "CLAIM_VERIFICATION_STATUSES", "CONFIDENCE_LEVELS",
    "CONFLICT_STATUSES", "DraftFieldState", "DraftRevision", "EVALUATION_STATUSES",
    "EVIDENCE_APPLICABILITIES", "EVIDENCE_INTEGRITIES", "EvaluationRun", "Evidence",
    "FIELD_SOURCES", "GAP_ACQUISITIONS", "GAP_STATUSES", "Gap", "IssueDraftV3",
    "Claim", "Conflict",
    "RESOLUTION_ACTIONS", "Resolution", "SUGGESTION_STATUSES", "Suggestion",
    "SuggestionDismissal", "dismiss_suggestion", "stable_content_digest",
]
