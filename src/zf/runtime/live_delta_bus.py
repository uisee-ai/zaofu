"""Ephemeral live-delta transport (doc 106 B axis).

Streaming ``*.delta`` payloads are UI transport, not truth
(``transcript_is_truth = False``): they never enter ``events.jsonl``.
They live as short-TTL scratch JSONL under ``<state_dir>/live/deltas/``,
readable across processes (the orchestrator or a web background thread
publishes; the web SSE tail merges and pushes to browsers). Every file
here is deletable at any moment without state loss — the final text is
guaranteed by committed events (``channel.message.posted`` body,
``kanban.agent.reply`` answer, ``agent.session.run.completed``
``final_text``).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj

LIVE_DELTA_TTL_SECONDS = 900
_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class LiveDeltaBus:
    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "live" / "deltas"

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        key: str,
        actor: str = "",
        task_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        row = {
            "id": f"live-{uuid.uuid4().hex[:12]}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "actor": actor,
            "task_id": task_id,
            "causation_id": causation_id,
            "correlation_id": correlation_id,
            # Ephemeral path must keep redaction — otherwise the bus becomes
            # a redaction bypass (doc 106 §6.1).
            "payload": redact_obj(payload),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{_safe_key(key)}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_since(
        self,
        cursors: dict[str, int] | None = None,
    ) -> tuple[list[ZfEvent], dict[str, int]]:
        """Return rows appended after the per-file byte cursors, as ZfEvent
        objects so SSE consumers see the exact wire shape of ledger events."""
        cursors = dict(cursors or {})
        rows: list[ZfEvent] = []
        if not self.root.is_dir():
            return rows, cursors
        for path in sorted(self.root.glob("*.jsonl")):
            name = path.name
            offset = cursors.get(name, 0)
            try:
                size = path.stat().st_size
                if size <= offset:
                    continue
                with path.open("rb") as handle:
                    handle.seek(offset)
                    chunk = handle.read(size - offset)
                cursors[name] = size
            except OSError:
                continue
            for raw in chunk.splitlines():
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict) or not str(data.get("type") or ""):
                    continue
                rows.append(ZfEvent(
                    id=str(data.get("id") or ""),
                    ts=str(data.get("ts") or ""),
                    type=str(data.get("type") or ""),
                    actor=str(data.get("actor") or ""),
                    task_id=data.get("task_id"),
                    causation_id=data.get("causation_id"),
                    correlation_id=data.get("correlation_id"),
                    payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
                ))
        return rows, cursors

    def current_cursors(self) -> dict[str, int]:
        """Byte cursors at the current end of every scratch file. A new SSE
        subscriber starts here so it only sees deltas published AFTER it
        connected — replaying the backlog of already-finished turns fed the
        frontend stale streaming state (2026-07-16 P1). Final text is
        guaranteed by committed events, so skipping history loses nothing."""
        cursors: dict[str, int] = {}
        if not self.root.is_dir():
            return cursors
        for path in self.root.glob("*.jsonl"):
            try:
                cursors[path.name] = path.stat().st_size
            except OSError:
                continue
        return cursors

    def discard(self, key: str) -> None:
        """Drop a finished run's scratch file. Ephemeral by contract —
        the committed terminal event carries the aggregate text."""
        try:
            (self.root / f"{_safe_key(key)}.jsonl").unlink(missing_ok=True)
        except OSError:
            pass

    def sweep(self, *, ttl_seconds: int = LIVE_DELTA_TTL_SECONDS) -> int:
        """Delete scratch files idle past the TTL. Safe at any time."""
        if not self.root.is_dir():
            return 0
        deadline = time.time() - ttl_seconds
        removed = 0
        for path in self.root.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < deadline:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed


def _safe_key(key: str) -> str:
    text = _KEY_SAFE.sub("-", str(key or "").strip()) or "run"
    return text[:120]


def live_delta_bus_for_writer(writer: Any) -> LiveDeltaBus | None:
    """Derive the bus from an EventWriter's ledger path (same state-dir
    derivation the emitters already use for the output contract)."""
    path = getattr(getattr(writer, "event_log", None), "path", None)
    if path is None:
        return None
    return LiveDeltaBus(Path(path).parent)
