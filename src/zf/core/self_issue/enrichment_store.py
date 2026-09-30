"""Atomic host adapter for Issue Enrichment semantic records.

Draft field values remain owned by :class:`IssueDraftStore`.  This store keeps
only field governance metadata and revision-bound semantic records, avoiding a
second canonical copy of user-visible Draft content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from issue_core.enrichment import (
    Claim,
    Conflict,
    DraftRevision,
    EvaluationRun,
    Evidence,
    Gap,
    Resolution,
    Suggestion,
    SuggestionDismissal,
    dismiss_suggestion,
    stable_content_digest,
)
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


STORE_SCHEMA = "issue-enrichment-store.v1"
PROJECTION_SCHEMA = "issue-enrichment-projection.v1"
FIELD_METADATA_KEYS = frozenset({
    "source", "user_locked", "confidence", "evidence_refs", "last_changed_revision",
})
DRAFT_CONTENT_FIELDS = (
    "title", "summary", "bug_description", "reproduction_steps", "actual_behavior",
    "expected_behavior", "environment", "project_version", "additional_context",
    "reported_expected_behavior", "canonical_expected_behavior",
    "attachment_context", "attachment_refs", "target_binding",
)


class EnrichmentRevisionConflict(ValueError):
    """Raised when semantic records target a stale Draft revision or digest."""


@dataclass(frozen=True)
class EnrichmentProjection:
    draft_id: str
    draft_revision: int
    draft_digest: str
    field_metadata: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    revisions: tuple[DraftRevision, ...] = ()
    claims: tuple[Claim, ...] = ()
    evidences: tuple[Evidence, ...] = ()
    gaps: tuple[Gap, ...] = ()
    suggestions: tuple[Suggestion, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    resolutions: tuple[Resolution, ...] = ()
    evaluation_runs: tuple[EvaluationRun, ...] = ()
    dismissals: tuple[SuggestionDismissal, ...] = ()
    schema_version: str = PROJECTION_SCHEMA

    def validate(self) -> None:
        if self.schema_version != PROJECTION_SCHEMA:
            raise ValueError("unsupported Issue Enrichment projection schema")
        if not self.draft_id or self.draft_revision < 1:
            raise ValueError("Enrichment projection must identify a Draft revision")
        if not self.draft_digest.startswith("sha256:"):
            raise ValueError("Enrichment projection requires a Draft digest")
        for field_path, metadata in self.field_metadata.items():
            if not field_path or not isinstance(metadata, Mapping):
                raise ValueError("field metadata must be keyed by field path")
            if set(metadata) - FIELD_METADATA_KEYS:
                raise ValueError("field metadata cannot contain Draft values")
            if "value" in metadata:
                raise ValueError("Draft values belong to IssueDraftStore")
        for collection in (
            self.revisions, self.claims, self.evidences, self.gaps, self.suggestions,
            self.conflicts, self.resolutions, self.evaluation_runs, self.dismissals,
        ):
            for item in collection:
                item.validate()
                item_draft_id = getattr(item, "draft_id", self.draft_id)
                if item_draft_id != self.draft_id:
                    raise ValueError("Enrichment record belongs to another Draft")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "draft_id": self.draft_id,
            "draft_revision": self.draft_revision,
            "draft_digest": self.draft_digest,
            "field_metadata": {
                str(name): dict(metadata)
                for name, metadata in self.field_metadata.items()
            },
            "revisions": [item.to_dict() for item in self.revisions],
            "claims": [item.to_dict() for item in self.claims],
            "evidences": [item.to_dict() for item in self.evidences],
            "gaps": [item.to_dict() for item in self.gaps],
            "suggestions": [item.to_dict() for item in self.suggestions],
            "conflicts": [item.to_dict() for item in self.conflicts],
            "resolutions": [item.to_dict() for item in self.resolutions],
            "evaluation_runs": [item.to_dict() for item in self.evaluation_runs],
            "dismissals": [item.to_dict() for item in self.dismissals],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EnrichmentProjection":
        if str(value.get("schema_version") or "") != PROJECTION_SCHEMA:
            raise ValueError("unsupported Issue Enrichment projection schema")
        projection = cls(
            draft_id=str(value.get("draft_id") or ""),
            draft_revision=int(value.get("draft_revision") or 0),
            draft_digest=str(value.get("draft_digest") or ""),
            field_metadata={
                str(name): dict(metadata)
                for name, metadata in dict(value.get("field_metadata") or {}).items()
            },
            revisions=tuple(DraftRevision.from_dict(item) for item in value.get("revisions") or ()),
            claims=tuple(Claim.from_dict(item) for item in value.get("claims") or ()),
            evidences=tuple(Evidence.from_dict(item) for item in value.get("evidences") or ()),
            gaps=tuple(Gap.from_dict(item) for item in value.get("gaps") or ()),
            suggestions=tuple(
                Suggestion.from_dict(item) for item in value.get("suggestions") or ()
            ),
            conflicts=tuple(Conflict.from_dict(item) for item in value.get("conflicts") or ()),
            resolutions=tuple(
                Resolution.from_dict(item) for item in value.get("resolutions") or ()
            ),
            evaluation_runs=tuple(
                EvaluationRun.from_dict(item)
                for item in value.get("evaluation_runs") or ()
            ),
            dismissals=tuple(
                SuggestionDismissal.from_dict(item)
                for item in value.get("dismissals") or ()
            ),
            schema_version=str(value.get("schema_version") or ""),
        )
        projection.validate()
        return projection


class IssueEnrichmentStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": STORE_SCHEMA, "drafts": {}}
        value = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        if not isinstance(value, dict) or value.get("schema_version") != STORE_SCHEMA:
            raise ValueError("unsupported Issue Enrichment store schema")
        if not isinstance(value.get("drafts"), dict):
            raise ValueError("Issue Enrichment store drafts must be an object")
        return value

    def get(self, draft_id: str) -> EnrichmentProjection | None:
        value = self._load_unlocked().get("drafts", {}).get(draft_id)
        return EnrichmentProjection.from_dict(value) if isinstance(value, dict) else None

    def delete(self, draft_id: str) -> bool:
        with locked_path(self.path):
            store = self._load_unlocked()
            drafts = dict(store["drafts"])
            if draft_id not in drafts:
                return False
            drafts.pop(draft_id, None)
            atomic_write_text(
                self.path,
                json.dumps(
                    {"schema_version": STORE_SCHEMA, "drafts": drafts},
                    ensure_ascii=False, indent=2, sort_keys=True,
                ) + "\n",
            )
            return True

    def save(
        self,
        projection: EnrichmentProjection,
        *,
        expected_draft_revision: int | None = None,
        expected_draft_digest: str | None = None,
    ) -> EnrichmentProjection:
        projection.validate()
        with locked_path(self.path):
            store = self._load_unlocked()
            drafts = dict(store["drafts"])
            current_value = drafts.get(projection.draft_id)
            current = (
                EnrichmentProjection.from_dict(current_value)
                if isinstance(current_value, dict) else None
            )
            if expected_draft_revision is not None:
                if current is None or current.draft_revision != expected_draft_revision:
                    raise EnrichmentRevisionConflict("Draft revision changed")
            if expected_draft_digest is not None:
                if current is None or current.draft_digest != expected_draft_digest:
                    raise EnrichmentRevisionConflict("Draft digest changed")
            drafts[projection.draft_id] = projection.to_dict()
            atomic_write_text(
                self.path,
                json.dumps(
                    {"schema_version": STORE_SCHEMA, "drafts": drafts},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
            )
        return projection

    @staticmethod
    def draft_digest(draft: Any) -> str:
        """Digest only user-visible Draft content, never transient run state."""

        value = draft.to_dict() if hasattr(draft, "to_dict") else dict(draft)
        return stable_content_digest({
            name: value.get(name)
            for name in DRAFT_CONTENT_FIELDS
        })

    def sync_draft(self, draft: Any) -> EnrichmentProjection:
        """Keep semantic metadata bound to the single canonical Draft store.

        This callback never copies field values.  Later domain actions add
        provenance/revision semantics, while this safety net prevents any
        existing Draft mutation path from leaving records bound to an older
        revision or digest.
        """

        draft_id = str(getattr(draft, "draft_id", "") or "")
        revision = int(getattr(draft, "revision", 0) or 0)
        digest = self.draft_digest(draft)
        with locked_path(self.path):
            store = self._load_unlocked()
            raw = store["drafts"].get(draft_id)
            current = (
                EnrichmentProjection.from_dict(raw)
                if isinstance(raw, dict) else None
            )
            updated = (
                replace(
                    current,
                    draft_revision=revision,
                    draft_digest=digest,
                )
                if current is not None
                else EnrichmentProjection(
                    draft_id=draft_id,
                    draft_revision=revision,
                    draft_digest=digest,
                )
            )
            updated.validate()
            drafts = dict(store["drafts"])
            drafts[draft_id] = updated.to_dict()
            atomic_write_text(
                self.path,
                json.dumps(
                    {"schema_version": STORE_SCHEMA, "drafts": drafts},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
            )
            return updated

    def dismiss_suggestion(
        self,
        *,
        draft_id: str,
        suggestion_id: str,
        actor: str,
        expected_draft_revision: int,
        expected_draft_digest: str,
        dismissed_at: str,
    ) -> tuple[EnrichmentProjection, SuggestionDismissal]:
        """Atomically erase candidate semantics and retain a content-free audit row."""

        with locked_path(self.path):
            store = self._load_unlocked()
            raw = store["drafts"].get(draft_id)
            if not isinstance(raw, dict):
                raise ValueError("Issue Enrichment projection not found")
            current = EnrichmentProjection.from_dict(raw)
            if (
                current.draft_revision != expected_draft_revision
                or current.draft_digest != expected_draft_digest
            ):
                raise EnrichmentRevisionConflict("Draft revision or digest changed")
            existing = next(
                (item for item in current.dismissals if item.suggestion_id == suggestion_id),
                None,
            )
            if existing is not None:
                return current, existing
            suggestion = next(
                (item for item in current.suggestions if item.suggestion_id == suggestion_id),
                None,
            )
            if suggestion is None:
                raise ValueError("Suggestion not found")
            dismissal = dismiss_suggestion(
                suggestion,
                actor=actor,
                current_revision=current.draft_revision,
                dismissed_at=dismissed_at,
            )
            updated = replace(
                current,
                suggestions=tuple(
                    item for item in current.suggestions
                    if item.suggestion_id != suggestion_id
                ),
                dismissals=(*current.dismissals, dismissal),
            )
            updated.validate()
            drafts = dict(store["drafts"])
            drafts[draft_id] = updated.to_dict()
            atomic_write_text(
                self.path,
                json.dumps(
                    {"schema_version": STORE_SCHEMA, "drafts": drafts},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n",
            )
            return updated, dismissal


__all__ = [
    "DRAFT_CONTENT_FIELDS",
    "EnrichmentProjection", "EnrichmentRevisionConflict", "IssueEnrichmentStore",
    "PROJECTION_SCHEMA", "STORE_SCHEMA",
]
