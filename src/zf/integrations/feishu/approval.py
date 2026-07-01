"""Feishu approval lifecycle store."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from zf.core.events import EventWriter
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


TERMINAL_STATUSES = {"approved", "denied", "expired", "superseded"}


@dataclass
class ApprovalRecord:
    approval_id: str
    kind: str
    status: str = "requested"
    task_id: str = ""
    reason: str = ""
    requested_by: str = ""
    requested_at: str = ""
    expires_at: str = ""
    decided_by: str = ""
    decided_at: str = ""
    metadata: dict = field(default_factory=dict)


class ApprovalStore:
    """Small JSON store for Feishu approval objects."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def request(
        self,
        *,
        approval_id: str,
        kind: str,
        task_id: str = "",
        reason: str = "",
        requested_by: str = "",
        expires_at: str = "",
        metadata: dict | None = None,
        writer: EventWriter | None = None,
    ) -> ApprovalRecord:
        now = _now_iso()
        record = ApprovalRecord(
            approval_id=approval_id,
            kind=kind,
            task_id=task_id,
            reason=reason,
            requested_by=requested_by,
            requested_at=now,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        with locked_path(self.path):
            records = self._load_unlocked()
            records[approval_id] = record
            self._save_unlocked(records)
        if writer is not None:
            writer.emit(
                "feishu.approval.requested",
                actor=requested_by or "feishu",
                task_id=task_id or None,
                payload=asdict(record),
            )
        return record

    def transition(
        self,
        *,
        approval_id: str,
        status: str,
        actor: str,
        writer: EventWriter | None = None,
    ) -> tuple[bool, str, ApprovalRecord | None]:
        if status not in {"approved", "denied", "superseded"}:
            return False, f"unsupported approval status: {status}", None
        with locked_path(self.path):
            records = self._load_unlocked()
            record = records.get(approval_id)
            if record is None:
                return False, f"approval {approval_id!r} not found", None
            if record.status in TERMINAL_STATUSES:
                return True, f"approval {approval_id} already {record.status}", record
            if _is_expired(record):
                record.status = "expired"
                record.decided_by = actor
                record.decided_at = _now_iso()
                records[approval_id] = record
                self._save_unlocked(records)
                if writer is not None:
                    writer.emit(
                        "feishu.approval.expired",
                        actor=actor,
                        task_id=record.task_id or None,
                        payload=asdict(record),
                    )
                return False, f"approval {approval_id} expired", record
            record.status = status
            record.decided_by = actor
            record.decided_at = _now_iso()
            records[approval_id] = record
            self._save_unlocked(records)
        if writer is not None:
            writer.emit(
                f"feishu.approval.{status}",
                actor=actor,
                task_id=record.task_id or None,
                payload=asdict(record),
            )
        return True, f"approval {approval_id} {status}", record

    def get(self, approval_id: str) -> ApprovalRecord | None:
        return self._load_unlocked().get(approval_id)

    def _load_unlocked(self) -> dict[str, ApprovalRecord]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        raw_records = data.get("approvals") if isinstance(data, dict) else {}
        if not isinstance(raw_records, dict):
            return {}
        records: dict[str, ApprovalRecord] = {}
        for key, value in raw_records.items():
            if isinstance(value, dict):
                records[str(key)] = ApprovalRecord(**value)
        return records

    def _save_unlocked(self, records: dict[str, ApprovalRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path,
            json.dumps(
                {"approvals": {key: asdict(value) for key, value in records.items()}},
                ensure_ascii=False,
                indent=2,
            ) + "\n",
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_expired(record: ApprovalRecord) -> bool:
    if not record.expires_at:
        return False
    try:
        expires_at = datetime.fromisoformat(record.expires_at)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)
