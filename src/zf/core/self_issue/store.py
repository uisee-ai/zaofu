"""Atomic canonical stores for Self-Issue drafts and publication intents."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Callable, Generic, TypeVar

from zf.core.self_issue.models import (
    AttachmentPreparationIntent,
    IssueDraft,
    PublicationBatch,
    PublicationIntent,
    SelfIssueIntake,
    utc_now,
)
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path

T = TypeVar(
    "T", SelfIssueIntake, IssueDraft, PublicationIntent, PublicationBatch,
    AttachmentPreparationIntent,
)


def _record_key(record_type: type | object) -> str:
    if record_type is SelfIssueIntake or isinstance(record_type, SelfIssueIntake):
        return "intake_id"
    if record_type is IssueDraft or isinstance(record_type, IssueDraft):
        return "draft_id"
    if record_type is PublicationBatch or isinstance(record_type, PublicationBatch):
        return "batch_id"
    if (
        record_type is AttachmentPreparationIntent
        or isinstance(record_type, AttachmentPreparationIntent)
    ):
        return "preparation_id"
    return "intent_id"


class _JsonRecordStore(Generic[T]):
    def __init__(self, path: Path, record_type: type[T]) -> None:
        self.path = Path(path)
        self.record_type = record_type

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        if not isinstance(data, list):
            raise ValueError(f"canonical store must contain a list: {self.path}")
        return [dict(item) for item in data if isinstance(item, dict)]

    def list(self) -> list[T]:
        return [self.record_type.from_dict(item) for item in self._load()]

    def get(self, record_id: str) -> T | None:
        key = _record_key(self.record_type)
        return next((item for item in self.list() if getattr(item, key) == record_id), None)

    def save(self, record: T) -> T:
        record.validate()
        key = _record_key(record)
        with locked_path(self.path):
            rows = self._load()
            value = record.to_dict()
            for index, row in enumerate(rows):
                if str(row.get(key) or "") == str(getattr(record, key)):
                    rows[index] = value
                    break
            else:
                rows.append(value)
            atomic_write_text(
                self.path,
                json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        return record

    def delete(self, record_id: str) -> bool:
        key = _record_key(self.record_type)
        with locked_path(self.path):
            rows = self._load()
            retained = [row for row in rows if str(row.get(key) or "") != record_id]
            if len(retained) == len(rows):
                return False
            atomic_write_text(
                self.path,
                json.dumps(retained, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            return True


class IssueDraftStore(_JsonRecordStore[IssueDraft]):
    def __init__(
        self,
        path: Path,
        *,
        on_save: Callable[[IssueDraft], None] | None = None,
    ) -> None:
        super().__init__(path, IssueDraft)
        self.on_save = on_save

    def save(self, record: IssueDraft) -> IssueDraft:
        saved = super().save(record)
        if self.on_save is not None:
            self.on_save(saved)
        return saved

    def list(self) -> list[IssueDraft]:
        """Read only the current Draft schema; historical diagnosis rows are ignored."""
        current_fields = {item.name for item in fields(IssueDraft)}
        return [
            IssueDraft.from_dict(item)
            for item in self._load()
            if "incident_fingerprint" in item and set(item) <= current_fields
        ]

    def find_fingerprint(self, fingerprint: str) -> IssueDraft | None:
        matches = [item for item in self.list() if item.incident_fingerprint == fingerprint]
        return max(matches, key=lambda item: item.updated_at, default=None)

    def latest(self) -> IssueDraft | None:
        """Return the most recently updated canonical Draft, if one exists."""
        return max(self.list(), key=lambda item: item.updated_at, default=None)


class SelfIssueIntakeStore(_JsonRecordStore[SelfIssueIntake]):
    def __init__(self, path: Path) -> None:
        super().__init__(path, SelfIssueIntake)

    def latest(self) -> SelfIssueIntake | None:
        return max(
            (item for item in self.list() if item.status in {
                "collecting", "awaiting_user_review", "submitted",
            }),
            key=lambda item: item.updated_at,
            default=None,
        )

    def find_fingerprint(self, fingerprint: str) -> SelfIssueIntake | None:
        matches = [
            item for item in self.list()
            if item.incident_fingerprint == fingerprint
            and item.status in {"collecting", "awaiting_user_review", "submitted"}
        ]
        return max(matches, key=lambda item: item.updated_at, default=None)


class PublicationIntentStore(_JsonRecordStore[PublicationIntent]):
    def __init__(self, path: Path) -> None:
        super().__init__(path, PublicationIntent)

    def for_draft(self, draft_id: str) -> list[PublicationIntent]:
        return [item for item in self.list() if item.draft_id == draft_id]

    def delete_for_draft(self, draft_id: str) -> int:
        return self._delete_for_draft(draft_id)

    def _delete_for_draft(self, draft_id: str) -> int:
        with locked_path(self.path):
            rows = self._load()
            retained = [row for row in rows if str(row.get("draft_id") or "") != draft_id]
            removed = len(rows) - len(retained)
            if removed:
                atomic_write_text(
                    self.path,
                    json.dumps(retained, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
            return removed

    def locked_for_draft(self, draft_id: str) -> PublicationIntent | None:
        return next(
            (
                item for item in self.for_draft(draft_id)
                if item.status in {"publishing", "outcome_unknown"}
            ),
            None,
        )

    def invalidate_unpublished(
        self,
        draft_id: str,
        *,
        reason: str,
        except_intent_ids: frozenset[str] = frozenset(),
    ) -> list[str]:
        """Atomically invalidate previews and confirmations after snapshot changes."""
        invalidated: list[str] = []
        with locked_path(self.path):
            rows = self._load()
            for index, row in enumerate(rows):
                current = PublicationIntent.from_dict(row)
                if (
                    current.draft_id != draft_id
                    or current.intent_id in except_intent_ids
                    or current.status not in {"previewed", "confirmed"}
                ):
                    continue
                current.status = "invalidated"
                current.confirmation_id = ""
                current.confirmation_expires_at = ""
                current.failure_reason = reason
                current.updated_at = utc_now()
                rows[index] = current.to_dict()
                invalidated.append(current.intent_id)
            if invalidated:
                atomic_write_text(
                    self.path,
                    json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
        return invalidated

    def claim_publish(
        self, intent_id: str, confirmation_id: str,
    ) -> tuple[PublicationIntent | None, bool]:
        """Atomically consume a confirmation and enter ``publishing``."""
        with locked_path(self.path):
            rows = self._load()
            for index, row in enumerate(rows):
                if str(row.get("intent_id") or "") != intent_id:
                    continue
                current = PublicationIntent.from_dict(row)
                if (
                    current.status != "confirmed"
                    or current.confirmation_id != confirmation_id
                ):
                    return current, False
                current.status = "publishing"
                current.confirmation_id = ""
                current.updated_at = utc_now()
                rows[index] = current.to_dict()
                atomic_write_text(
                    self.path,
                    json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
                return current, True
        return None, False


class PublicationBatchStore(_JsonRecordStore[PublicationBatch]):
    def __init__(self, path: Path) -> None:
        super().__init__(path, PublicationBatch)

    def for_draft(self, draft_id: str) -> list[PublicationBatch]:
        return [item for item in self.list() if item.draft_id == draft_id]

    def latest_for_draft(self, draft_id: str) -> PublicationBatch | None:
        return max(self.for_draft(draft_id), key=lambda item: item.updated_at, default=None)

    def delete_for_draft(self, draft_id: str) -> int:
        with locked_path(self.path):
            rows = self._load()
            retained = [row for row in rows if str(row.get("draft_id") or "") != draft_id]
            removed = len(rows) - len(retained)
            if removed:
                atomic_write_text(
                    self.path,
                    json.dumps(retained, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
            return removed

    def invalidate_unpublished(
        self,
        draft_id: str,
        *,
        reason: str,
        except_batch_ids: frozenset[str] = frozenset(),
    ) -> list[str]:
        invalidated: list[str] = []
        with locked_path(self.path):
            rows = self._load()
            for index, row in enumerate(rows):
                current = PublicationBatch.from_dict(row)
                if (
                    current.draft_id != draft_id
                    or current.batch_id in except_batch_ids
                    or current.status not in {
                    "previewed", "confirmed", "publish_failed",
                    }
                ):
                    continue
                current.status = "invalidated"
                current.confirmation_id = ""
                current.confirmation_expires_at = ""
                current.failure_reason = reason
                current.updated_at = utc_now()
                rows[index] = current.to_dict()
                invalidated.append(current.batch_id)
            if invalidated:
                atomic_write_text(
                    self.path,
                    json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
        return invalidated


class AttachmentPreparationStore(_JsonRecordStore[AttachmentPreparationIntent]):
    def __init__(self, path: Path) -> None:
        super().__init__(path, AttachmentPreparationIntent)

    def for_draft(self, draft_id: str) -> list[AttachmentPreparationIntent]:
        return [item for item in self.list() if item.draft_id == draft_id]

    def delete_for_draft(self, draft_id: str) -> int:
        with locked_path(self.path):
            rows = self._load()
            retained = [row for row in rows if str(row.get("draft_id") or "") != draft_id]
            removed = len(rows) - len(retained)
            if removed:
                atomic_write_text(
                    self.path,
                    json.dumps(retained, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
            return removed

    def locked_for_draft(self, draft_id: str) -> AttachmentPreparationIntent | None:
        return next((
            item for item in self.for_draft(draft_id)
            if item.status in {"preparing", "outcome_unknown"}
        ), None)

    def invalidate_unprepared(self, draft_id: str, *, reason: str) -> list[str]:
        invalidated: list[str] = []
        with locked_path(self.path):
            rows = self._load()
            for index, row in enumerate(rows):
                current = AttachmentPreparationIntent.from_dict(row)
                if current.draft_id != draft_id or current.status not in {
                    "previewed", "confirmed",
                }:
                    continue
                current.status = "invalidated"
                current.confirmation_id = ""
                current.confirmation_expires_at = ""
                current.failure_reason = reason
                current.updated_at = utc_now()
                rows[index] = current.to_dict()
                invalidated.append(current.preparation_id)
            if invalidated:
                atomic_write_text(
                    self.path,
                    json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
        return invalidated

    def claim_prepare(
        self, preparation_id: str, confirmation_id: str,
    ) -> tuple[AttachmentPreparationIntent | None, bool]:
        with locked_path(self.path):
            rows = self._load()
            for index, row in enumerate(rows):
                if str(row.get("preparation_id") or "") != preparation_id:
                    continue
                current = AttachmentPreparationIntent.from_dict(row)
                if (
                    current.status != "confirmed"
                    or current.confirmation_id != confirmation_id
                ):
                    return current, False
                current.status = "preparing"
                current.confirmation_id = ""
                current.updated_at = utc_now()
                rows[index] = current.to_dict()
                atomic_write_text(
                    self.path,
                    json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
                return current, True
        return None, False
