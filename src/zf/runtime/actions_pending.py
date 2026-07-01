"""Pending worker action requests projection.

Worker action requests (``worker.reply.requested`` /
``worker.respawn.requested`` / ``worker.drain.requested``) are operator
intents persisted in ``events.jsonl``. The orchestrator must process each
exactly once and react to transient failures with bounded retries.

Without this projection the orchestrator must scan ``events.jsonl`` every
tick to find unhandled requests, which:
  * misses requests that fall outside the 24h read window,
  * silently retries forever when failures aren't recorded,
  * has no idempotency story across reactor and action handler paths.

This module owns a persistent ``actions/pending.json`` projection. The
reactor enqueues new requests; the orchestrator drains the queue and
records the outcome. Cold-start rebuild from ``events.jsonl`` is
supported but optional — the projection is the source of truth at
runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from zf.core.state.atomic_io import atomic_write_text


REQUEST_TYPES = frozenset({
    "worker.reply.requested",
    "worker.respawn.requested",
    "worker.drain.requested",
})

# RESULT_TYPES is the closed-loop set: when the orchestrator emits any of
# these with causation_id == <request.id>, the request is considered
# terminally handled.
RESULT_TYPES = frozenset({
    "worker.reply.sent",
    "worker.reply.failed",
    "worker.respawn.completed",
    "worker.respawn.failed",
    "role.instance.draining",
    "worker.drain.failed",
    "worker.action.permanently_failed",
})

PERMANENT_FAILURE_EVENT = "worker.action.permanently_failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PendingAction:
    request_id: str
    type: str
    instance_id: str
    payload: dict
    received_at: str
    status: str = "pending"  # pending | in_flight | completed | failed
    retries: int = 0
    last_error: str = ""
    correlation_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "type": self.type,
            "instance_id": self.instance_id,
            "payload": self.payload,
            "received_at": self.received_at,
            "status": self.status,
            "retries": self.retries,
            "last_error": self.last_error,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PendingAction":
        return cls(
            request_id=str(data.get("request_id", "")),
            type=str(data.get("type", "")),
            instance_id=str(data.get("instance_id", "")),
            payload=dict(data.get("payload", {}) or {}),
            received_at=str(data.get("received_at", "")),
            status=str(data.get("status", "pending")),
            retries=int(data.get("retries", 0) or 0),
            last_error=str(data.get("last_error", "")),
            correlation_id=data.get("correlation_id"),
        )


@dataclass
class PendingActionsStore:
    """Persistent queue of pending worker action requests.

    Backed by a single JSON file under ``<state_dir>/actions/pending.json``.
    Single-writer (Orchestrator under the loop lock). Bounded to
    ``max_entries`` so a runaway producer cannot exhaust disk; oldest
    entries are evicted with a ``warning`` flag in the result.
    """

    path: Path
    max_entries: int = 1000
    max_retries: int = 3
    _entries: dict[str, PendingAction] = field(default_factory=dict)
    _loaded: bool = False

    # ---- persistence ----

    def load(self) -> None:
        if self._loaded:
            return
        self._entries = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            if isinstance(data, dict):
                for key, raw in data.items():
                    if not isinstance(raw, dict):
                        continue
                    self._entries[str(key)] = PendingAction.from_dict(raw)
        self._loaded = True

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            request_id: entry.to_dict()
            for request_id, entry in self._entries.items()
        }
        atomic_write_text(
            self.path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    # ---- API ----

    def upsert_pending(
        self,
        *,
        request_id: str,
        type: str,
        instance_id: str,
        payload: dict,
        correlation_id: str | None = None,
    ) -> PendingAction:
        self.load()
        if request_id in self._entries:
            return self._entries[request_id]
        if len(self._entries) >= self.max_entries:
            # Evict oldest pending entry to make room.
            oldest_key = next(iter(self._entries))
            self._entries.pop(oldest_key, None)
        entry = PendingAction(
            request_id=request_id,
            type=type,
            instance_id=instance_id,
            payload=dict(payload or {}),
            received_at=_now_iso(),
            correlation_id=correlation_id,
        )
        self._entries[request_id] = entry
        self._flush()
        return entry

    def take_pending(self) -> list[PendingAction]:
        """Return the snapshot of entries the orchestrator should process.

        Entries already ``completed``/``failed`` past max_retries are
        filtered out. Order is insertion order so older requests run
        first.
        """
        self.load()
        return [
            entry for entry in self._entries.values()
            if entry.status in {"pending", "in_flight"}
        ]

    def mark_in_flight(self, request_id: str) -> None:
        self.load()
        entry = self._entries.get(request_id)
        if entry is None:
            return
        if entry.status != "in_flight":
            entry.status = "in_flight"
            self._flush()

    def mark_completed(self, request_id: str) -> None:
        self.load()
        if request_id in self._entries:
            del self._entries[request_id]
            self._flush()

    def mark_failed(self, request_id: str, *, error: str) -> bool:
        """Record a failure. Returns True if the entry hit max_retries."""
        self.load()
        entry = self._entries.get(request_id)
        if entry is None:
            return False
        entry.retries += 1
        entry.last_error = error
        entry.status = "failed" if entry.retries >= self.max_retries else "pending"
        permanent = entry.status == "failed"
        if permanent:
            # Permanent failure: drop from the queue so we don't keep
            # retrying. Callers should emit ``worker.action.permanently
            # _failed`` so the operator sees the terminal outcome.
            del self._entries[request_id]
        self._flush()
        return permanent

    # ---- diagnostics ----

    def all_entries(self) -> list[PendingAction]:
        self.load()
        return list(self._entries.values())

    def rebuild_from_events(
        self,
        events: Iterable,
        *,
        window_days: int = 7,
    ) -> int:
        """Reconstruct the pending queue from an event iterable.

        Used during cold-start when the JSON projection is missing or
        corrupt. Folds the iterable: requests become pending; matching
        result events with the request's id as ``causation_id`` cancel
        the entry. Returns the count of pending entries after rebuild.
        """
        self._entries = {}
        results_seen: set[str] = set()
        request_payloads: dict[str, dict] = {}
        for event in events:
            causation = getattr(event, "causation_id", None) or ""
            event_type = getattr(event, "type", "")
            if event_type in RESULT_TYPES and causation:
                results_seen.add(causation)
                self._entries.pop(causation, None)
                continue
            if event_type not in REQUEST_TYPES:
                continue
            request_id = getattr(event, "id", "")
            if not request_id or request_id in results_seen:
                continue
            payload = getattr(event, "payload", None) or {}
            if not isinstance(payload, dict):
                payload = {}
            instance_id = str(
                payload.get("instance_id")
                or payload.get("worker")
                or payload.get("role")
                or ""
            )
            request_payloads[request_id] = {
                "type": event_type,
                "instance_id": instance_id,
                "payload": payload,
                "received_at": getattr(event, "ts", "") or _now_iso(),
                "correlation_id": getattr(event, "correlation_id", None),
            }
        for request_id, data in request_payloads.items():
            self._entries[request_id] = PendingAction(
                request_id=request_id,
                type=data["type"],
                instance_id=data["instance_id"],
                payload=data["payload"],
                received_at=data["received_at"],
                correlation_id=data["correlation_id"],
            )
        self._loaded = True
        self._flush()
        return len(self._entries)
