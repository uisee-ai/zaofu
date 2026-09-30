"""Immutable, host-neutral Draft value object for the generic ``/issue`` flow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping


class DraftRevisionConflict(ValueError):
    """Raised when an edit targets an older Draft revision."""


@dataclass(frozen=True)
class IssueDraft:
    draft_id: str
    title: str
    bug_description: str
    reproduction_steps: str
    expected_behavior: str
    attachment_context: str
    environment: Mapping[str, str]
    project_version: str
    additional_context: str = ""
    actual_behavior: str = ""
    revision: int = 1

    def validate(self) -> None:
        if not self.draft_id or self.revision < 1:
            raise ValueError("Draft requires an id and positive revision")
        limits = {
            "title": (self.title, 240),
            "bug_description": (self.bug_description, 8000),
            "reproduction_steps": (self.reproduction_steps, 8000),
            "actual_behavior": (self.actual_behavior, 8000),
            "expected_behavior": (self.expected_behavior, 8000),
            "attachment_context": (self.attachment_context, 8000),
            "additional_context": (self.additional_context, 8000),
            "project_version": (self.project_version, 120),
        }
        for field, (value, limit) in limits.items():
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError(f"Draft field {field} exceeds its contract")
        if not isinstance(self.environment, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.environment.items()
        ):
            raise ValueError("Draft environment must be a string map")

    def edit(self, *, expected_revision: int, **changes: Any) -> "IssueDraft":
        """Apply user edits with compare-and-swap revision semantics."""

        if expected_revision != self.revision:
            raise DraftRevisionConflict("Draft revision changed")
        allowed = {
            "title", "bug_description", "reproduction_steps", "actual_behavior",
            "expected_behavior", "attachment_context", "environment",
            "project_version", "additional_context",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported Draft fields: {sorted(unknown)}")
        updated = replace(self, **changes, revision=self.revision + 1)
        updated.validate()
        return updated

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": "issue-draft.v2",
            "draft_id": self.draft_id,
            "title": self.title,
            "bug_description": self.bug_description,
            "reproduction_steps": self.reproduction_steps,
            "actual_behavior": self.actual_behavior,
            "expected_behavior": self.expected_behavior,
            "attachment_context": self.attachment_context,
            "environment": dict(self.environment),
            "project_version": self.project_version,
            "additional_context": self.additional_context,
            "revision": self.revision,
        }


__all__ = ["DraftRevisionConflict", "IssueDraft"]
