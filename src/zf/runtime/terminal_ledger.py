"""Idempotency ledger for dispatch-scoped terminal events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


TERMINAL_SUCCESS_EVENTS = frozenset({
    "impl.child.completed",
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
})


def is_terminal_success_event(event_type: str) -> bool:
    return event_type in TERMINAL_SUCCESS_EVENTS


@dataclass(frozen=True)
class TerminalLedgerRecord:
    task_id: str
    dispatch_id: str
    event_type: str
    event_id: str
    actor: str
    status: str
    recorded_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "dispatch_id": self.dispatch_id,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "actor": self.actor,
            "status": self.status,
            "recorded_at": self.recorded_at,
        }


class TerminalLedger:
    """Small rebuildable projection used to suppress terminal replays."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "dispatch-terminal-ledger.json"

    def accepted_record(
        self,
        *,
        task_id: str,
        dispatch_id: str,
        event_type: str,
    ) -> dict[str, Any] | None:
        if not task_id or not dispatch_id or not is_terminal_success_event(event_type):
            return None
        key = self._key(task_id, dispatch_id, event_type)
        data = self._read()
        record = data.get(key)
        return record if isinstance(record, dict) else None

    def record_accepted(
        self,
        *,
        task_id: str,
        dispatch_id: str,
        event_type: str,
        event_id: str,
        actor: str,
    ) -> TerminalLedgerRecord | None:
        if not task_id or not dispatch_id or not is_terminal_success_event(event_type):
            return None
        key = self._key(task_id, dispatch_id, event_type)
        with locked_path(self.path):
            data = self._read()
            existing = data.get(key)
            if isinstance(existing, dict):
                return TerminalLedgerRecord(
                    task_id=str(existing.get("task_id") or task_id),
                    dispatch_id=str(existing.get("dispatch_id") or dispatch_id),
                    event_type=str(existing.get("event_type") or event_type),
                    event_id=str(existing.get("event_id") or event_id),
                    actor=str(existing.get("actor") or actor),
                    status=str(existing.get("status") or "accepted"),
                    recorded_at=str(existing.get("recorded_at") or _now()),
                )
            record = TerminalLedgerRecord(
                task_id=task_id,
                dispatch_id=dispatch_id,
                event_type=event_type,
                event_id=event_id,
                actor=actor,
                status="accepted",
                recorded_at=_now(),
            )
            data[key] = record.to_payload()
            atomic_write_text(
                self.path,
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
            return record

    @staticmethod
    def _key(task_id: str, dispatch_id: str, event_type: str) -> str:
        return f"{task_id}|{dispatch_id}|{event_type}"

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


def terminal_dispatch_id(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("dispatch_id") or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
