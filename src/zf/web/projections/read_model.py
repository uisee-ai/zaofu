"""SQLite read models for high-frequency Web projections.

This store is a rebuildable projection over ``events.jsonl`` and its archive
segments. It never replaces the append-only log as business truth.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.events.segments import (
    build_event_manifest,
    count_event_records,
    current_event_cursor,
    hydrate_event_at,
    iter_event_records,
    write_event_manifest,
)
from zf.core.security.redaction import redact_obj
from zf.core.state.locks import locked_path
from zf.runtime.sidecar_refs import iter_sidecar_ref_descriptors


SCHEMA_VERSION = "event-read-model.v3"
_JOBS: dict[str, threading.Thread] = {}
_JOBS_LOCK = threading.Lock()
# Throttle background tail catch-ups: a busy dashboard calls hydrate/events/
# timeline many times per second; without this each call would spawn (after the
# previous finished) a fresh 43MB re-read. Converge within a couple seconds
# instead. Env-tunable for tests / tuning.
_LAST_CATCH_UP: dict[str, float] = {}
_CATCH_UP_MIN_INTERVAL_S = max(0.0, float(os.environ.get("ZF_READMODEL_CATCHUP_MIN_INTERVAL_S", "2.0") or 2.0))
_PAYLOAD_SLIM_SAFE_ROUTING_KEYS = {
    "decision_token",
    "response_token",
}
KANBAN_AGENT_HISTORY_TYPES = (
    "user.message",
    "kanban.agent.reply",
    "agent.session.run.started",
    "agent.session.run.completed",
    "agent.session.run.failed",
    "agent.session.run.cancelled",
    "agent.session.part.started",
    "agent.session.part.delta",
    "agent.session.part.completed",
    "agent.session.part.failed",
)
KANBAN_AGENT_HISTORY_PREFIXES = (
    "kanban.agent.turn.",
    "kanban.agent.message.",
)
KANBAN_AGENT_CONTEXT_TYPES = (
    "user.message",
    "kanban.agent.turn.created",
    "kanban.agent.turn.started",
    "agent.session.run.started",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path(state_dir: Path) -> Path:
    return Path(state_dir) / "projections" / "read_model.sqlite"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projection_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS event_index (
          seq INTEGER PRIMARY KEY,
          event_id TEXT,
          ts TEXT,
          type TEXT,
          actor TEXT,
          task_id TEXT,
          correlation_id TEXT,
          causation_id TEXT,
          feature_id TEXT,
          trace_id TEXT,
          channel_id TEXT,
          status TEXT,
          summary TEXT,
          payload_digest TEXT,
          payload_slim TEXT,
          raw_segment TEXT NOT NULL,
          raw_offset INTEGER NOT NULL,
          raw_length INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_event_index_task_seq ON event_index(task_id, seq);
        CREATE INDEX IF NOT EXISTS idx_event_index_type_seq ON event_index(type, seq);
        CREATE INDEX IF NOT EXISTS idx_event_index_actor_seq ON event_index(actor, seq);
        CREATE INDEX IF NOT EXISTS idx_event_index_trace_seq ON event_index(trace_id, seq);
        CREATE INDEX IF NOT EXISTS idx_event_index_feature_seq ON event_index(feature_id, seq);
        CREATE INDEX IF NOT EXISTS idx_event_index_channel_seq ON event_index(channel_id, seq);
        CREATE INDEX IF NOT EXISTS idx_event_index_event_id ON event_index(event_id);

        CREATE TABLE IF NOT EXISTS event_ref (
          ref_kind TEXT NOT NULL,
          ref_id TEXT NOT NULL,
          seq INTEGER NOT NULL,
          event_id TEXT,
          PRIMARY KEY (ref_kind, ref_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_event_ref_seq ON event_ref(seq);

        CREATE TABLE IF NOT EXISTS sidecar_ref (
          kind TEXT NOT NULL,
          ref TEXT NOT NULL,
          seq INTEGER NOT NULL,
          event_id TEXT,
          sha256 TEXT,
          byte_count INTEGER,
          content_type TEXT,
          schema_version TEXT,
          required INTEGER,
          access_scope_json TEXT,
          retention_json TEXT,
          source_event_id TEXT,
          preview TEXT,
          PRIMARY KEY (kind, ref, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_sidecar_ref_seq ON sidecar_ref(seq);
        CREATE INDEX IF NOT EXISTS idx_sidecar_ref_ref ON sidecar_ref(ref);
        CREATE INDEX IF NOT EXISTS idx_sidecar_ref_kind ON sidecar_ref(kind);

        CREATE TABLE IF NOT EXISTS task_timeline (
          task_id TEXT NOT NULL,
          seq INTEGER NOT NULL,
          event_id TEXT,
          ts TEXT,
          type TEXT,
          actor TEXT,
          status TEXT,
          summary TEXT,
          trace_id TEXT,
          payload_slim TEXT,
          raw_segment TEXT NOT NULL,
          raw_offset INTEGER NOT NULL,
          raw_length INTEGER NOT NULL,
          PRIMARY KEY (task_id, seq)
        );

        CREATE TABLE IF NOT EXISTS projection_cache (
          key TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          source_seq INTEGER NOT NULL,
          payload_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    meta = _meta(conn)
    if meta and meta.get("schema_version") != SCHEMA_VERSION:
        conn.executescript(
            """
            DELETE FROM event_ref;
            DELETE FROM sidecar_ref;
            DELETE FROM task_timeline;
            DELETE FROM event_index;
            DELETE FROM projection_cache;
            DELETE FROM projection_meta;
            """
        )
    _set_meta(conn, "schema_version", SCHEMA_VERSION)
    conn.commit()


def _meta(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT key, value FROM projection_meta").fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["key"]): str(row["value"]) for row in rows}


def _set_meta(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO projection_meta(key, value) VALUES (?, ?)",
        (key, str(value)),
    )


def projection_status(state_dir: Path, *, count_source: bool = False) -> dict[str, Any]:
    manifest = build_event_manifest(state_dir)
    path = db_path(state_dir)
    exists = path.exists()
    meta: dict[str, str] = {}
    row_count = 0
    if exists:
        try:
            with _connect(path) as conn:
                _ensure_schema(conn)
                meta = _meta(conn)
                row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM event_index").fetchone()
                row_count = int(row["seq"] or 0) if row else 0
        except sqlite3.Error:
            exists = False
    projected_seq = int(meta.get("source_seq") or row_count or 0)
    source_seq = count_event_records(state_dir) if count_source else projected_seq
    source_cursor: dict[str, Any]
    if count_source:
        source_cursor = current_event_cursor(state_dir).to_dict()
    else:
        source_cursor = {
            "schema_version": "event-segment-cursor.v1",
            "segment": "",
            "byte_offset": 0,
            "line_no": projected_seq,
            "last_event_id": "",
            "archive_manifest_digest": manifest.digest,
        }
    digest = str(meta.get("manifest_digest") or "")
    projected_layout = str(meta.get("segment_layout_digest") or "")
    # Layout-based freshness: a plain append to the active segment changes the
    # full manifest digest (size+mtime of the active segment, segments.py) but
    # NOT the archive layout digest (_layout_digest zeroes the active segment).
    # Treating an append as "stale" forced an in-request full rebuild on every
    # call against a live project. Stay "ready" while the archive layout is
    # stable (only rotation forces a full, seq-stable reindex) and surface tail
    # growth via `tail_behind` so callers fire a non-blocking catch-up instead.
    layout_current = bool(exists and projected_layout and projected_layout == _layout_digest(manifest))
    current = layout_current
    tail_behind = bool(layout_current and digest != manifest.digest)
    lag = max(0, source_seq - projected_seq) if count_source else (0 if current and not tail_behind else None)
    return {
        "schema_version": SCHEMA_VERSION,
        "db_path": str(path),
        "exists": exists,
        "projection_state": "ready" if current else ("stale" if exists else "missing"),
        "tail_behind": tail_behind,
        "source_seq": source_seq,
        "source_cursor": source_cursor,
        "projected_seq": projected_seq,
        "projection_lag": lag,
        "manifest_digest": manifest.digest,
        "projected_manifest_digest": digest,
        "segment_count": len(manifest.segments),
        "total_bytes": manifest.total_bytes,
        "updated_at": meta.get("updated_at", ""),
    }


def ensure_requested(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    synchronous_if_missing: bool = True,
) -> dict[str, Any]:
    status = projection_status(state_dir)
    if status["projection_state"] == "ready":
        # Archive layout is stable. If the active tail grew since the last build
        # (plain appends), incrementally catch up — this decodes ONLY the new
        # rows (old rows are skipped by start_seq), so it is read-your-writes
        # consistent without the full re-decode that the old always-stale gate
        # forced on every request. When nothing was appended (idle / repeated
        # requests) this is skipped entirely and we serve straight from sqlite.
        if status.get("tail_behind"):
            rebuild(state_dir, config=config)
            return projection_status(state_dir)
        return status
    if synchronous_if_missing and status["projection_state"] == "missing":
        rebuild(state_dir, config=config)
        return projection_status(state_dir)
    request_catch_up(state_dir, config=config)
    return status


def request_catch_up(state_dir: Path, *, config: ZfConfig | None = None) -> None:
    key = str(db_path(state_dir).resolve())
    now = time.monotonic()
    with _JOBS_LOCK:
        existing = _JOBS.get(key)
        if existing is not None and existing.is_alive():
            return
        if now - _LAST_CATCH_UP.get(key, 0.0) < _CATCH_UP_MIN_INTERVAL_S:
            return
        _LAST_CATCH_UP[key] = now
        thread = threading.Thread(
            target=_catch_up_job,
            args=(Path(state_dir), config, key),
            name=f"zf-read-model-{Path(state_dir).name}",
            daemon=True,
        )
        _JOBS[key] = thread
        thread.start()


def _catch_up_job(state_dir: Path, config: ZfConfig | None, key: str) -> None:
    try:
        rebuild(state_dir, config=config)
    finally:
        with _JOBS_LOCK:
            current = _JOBS.get(key)
            if current is threading.current_thread():
                _JOBS.pop(key, None)


def rebuild(state_dir: Path, *, config: ZfConfig | None = None) -> dict[str, Any]:
    """Rebuild/catch up the read model from event truth."""

    path = db_path(state_dir)
    manifest = build_event_manifest(state_dir)
    write_event_manifest(state_dir)
    with locked_path(path):
        with _connect(path) as conn:
            _ensure_schema(conn)
            meta = _meta(conn)
            previous_layout_digest = meta.get("segment_layout_digest", "")
            layout_digest = _layout_digest(manifest)
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM event_index").fetchone()
            start_seq = int(row["seq"] or 0) if row else 0
            if previous_layout_digest and previous_layout_digest != layout_digest:
                # Segment composition can change after lazy rotation. Rebuild
                # from row 0 so seq stays globally stable across archives+active.
                conn.executescript(
                    """
                    DELETE FROM event_ref;
                    DELETE FROM sidecar_ref;
                    DELETE FROM task_timeline;
                    DELETE FROM event_index;
                    DELETE FROM projection_cache;
                    """
                )
                start_seq = 0

            inserted = 0
            last_seq = start_seq
            for record in iter_event_records(state_dir, config=config, start_seq=start_seq):
                event = record.event
                payload = event.payload if isinstance(event.payload, dict) else {}
                slim = _payload_slim(payload)
                trace_id = _first_nonempty(
                    event.correlation_id,
                    _payload_ref(payload, "trace_id"),
                    _payload_ref(payload, "correlation_id"),
                )
                feature_id = _first_nonempty(
                    _payload_ref(payload, "feature_id"),
                    _feature_from_task_id(event.task_id),
                )
                channel_id = _first_nonempty(
                    _payload_ref(payload, "channel_id"),
                    _payload_ref(payload, "channel"),
                )
                status = _first_nonempty(_payload_ref(payload, "status"), _event_status(event.type))
                summary = _event_summary(event.type, payload)
                payload_json = json.dumps(slim, ensure_ascii=False, default=str)
                digest = _payload_digest(payload)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO event_index(
                      seq, event_id, ts, type, actor, task_id, correlation_id,
                      causation_id, feature_id, trace_id, channel_id, status,
                      summary, payload_digest, payload_slim,
                      raw_segment, raw_offset, raw_length
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.seq,
                        event.id,
                        event.ts,
                        event.type,
                        event.actor,
                        event.task_id,
                        event.correlation_id,
                        event.causation_id,
                        feature_id,
                        trace_id,
                        channel_id,
                        status,
                        summary,
                        digest,
                        payload_json,
                        record.raw_segment,
                        record.raw_offset,
                        record.raw_length,
                    ),
                )
                for kind, ref_id in _event_refs(event, payload, feature_id=feature_id, trace_id=trace_id, channel_id=channel_id):
                    conn.execute(
                        "INSERT OR IGNORE INTO event_ref(ref_kind, ref_id, seq, event_id) VALUES (?, ?, ?, ?)",
                        (kind, ref_id, record.seq, event.id),
                    )
                for descriptor in iter_sidecar_ref_descriptors(payload):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO sidecar_ref(
                          kind, ref, seq, event_id, sha256, byte_count,
                          content_type, schema_version, required,
                          access_scope_json, retention_json, source_event_id, preview
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(descriptor.get("kind") or ""),
                            str(descriptor.get("ref") or ""),
                            record.seq,
                            event.id,
                            str(descriptor.get("sha256") or ""),
                            int(descriptor.get("byte_count") or 0),
                            str(descriptor.get("content_type") or ""),
                            str(descriptor.get("schema_version") or ""),
                            1 if bool(descriptor.get("required", False)) else 0,
                            json.dumps(descriptor.get("access_scope") or {}, ensure_ascii=False, default=str),
                            json.dumps(descriptor.get("retention") or {}, ensure_ascii=False, default=str),
                            str(descriptor.get("source_event_id") or ""),
                            str(descriptor.get("preview") or "")[:500],
                        ),
                    )
                if event.task_id:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO task_timeline(
                          task_id, seq, event_id, ts, type, actor, status,
                          summary, trace_id, payload_slim,
                          raw_segment, raw_offset, raw_length
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.task_id,
                            record.seq,
                            event.id,
                            event.ts,
                            event.type,
                            event.actor,
                            status,
                            summary,
                            trace_id,
                            payload_json,
                            record.raw_segment,
                            record.raw_offset,
                            record.raw_length,
                        ),
                    )
                inserted += 1
                last_seq = record.seq

            _set_meta(conn, "source_seq", last_seq)
            _set_meta(conn, "manifest_digest", manifest.digest)
            _set_meta(conn, "segment_layout_digest", layout_digest)
            _set_meta(conn, "updated_at", _now())
            _set_meta(conn, "segment_count", len(manifest.segments))
            _set_meta(conn, "total_bytes", manifest.total_bytes)
            conn.commit()
    return {
        "schema_version": SCHEMA_VERSION,
        "projection_state": "ready",
        "inserted": inserted,
        "source_seq": last_seq,
        "manifest_digest": manifest.digest,
    }


def events_page(
    state_dir: Path,
    *,
    limit: int,
    cursor: int | None = None,
    task_id: str | None = None,
    actor: str | None = None,
    event_type: str | None = None,
    event_prefix: str | None = None,
    failed: bool = False,
    blocked: bool = False,
    config: ZfConfig | None = None,
) -> dict[str, Any] | None:
    status = ensure_requested(state_dir, config=config)
    if not db_path(state_dir).exists():
        return None
    if status.get("projection_state") != "ready":
        rebuild(state_dir, config=config)
        status = projection_status(state_dir)
    limit = max(1, min(int(limit or 100), 500))
    where: list[str] = []
    args: list[Any] = []
    if cursor is not None:
        where.append("seq > ?")
        args.append(int(cursor))
    if task_id:
        where.append("task_id = ?")
        args.append(task_id)
    if actor:
        where.append("actor = ?")
        args.append(actor)
    if event_type:
        where.append("type = ?")
        args.append(event_type)
    if event_prefix:
        where.append("type LIKE ?")
        args.append(f"{event_prefix}%")
    if failed:
        where.append("(type LIKE '%.failed' OR type LIKE '%.rejected' OR status IN ('failed', 'rejected'))")
    if blocked:
        where.append("(type LIKE '%.blocked' OR status = 'blocked')")
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    order = "ASC" if cursor is not None else "DESC"
    with _connect(db_path(state_dir)) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM event_index
            {sql_where}
            ORDER BY seq {order}
            LIMIT ?
            """,
            (*args, limit),
        ).fetchall()
    if cursor is None:
        rows = list(reversed(rows))
    items = [_row_event_dict(row) for row in rows]
    next_cursor = items[-1]["seq"] if items else cursor
    return {
        "items": items,
        "next_cursor": next_cursor,
        "current_seq": int(status.get("projected_seq") or status.get("source_seq") or 0),
        "limit": limit,
        "projection_state": status.get("projection_state", "unknown"),
        "projection_lag": status.get("projection_lag"),
        "source": "read_model.sqlite",
    }


def task_timeline(
    state_dir: Path,
    task_id: str,
    *,
    limit: int,
    config: ZfConfig | None = None,
) -> dict[str, Any] | None:
    status = ensure_requested(state_dir, config=config)
    if status.get("projection_state") != "ready":
        rebuild(state_dir, config=config)
        status = projection_status(state_dir)
    if not db_path(state_dir).exists():
        return None
    limit = max(1, min(int(limit or 200), 1000))
    with _connect(db_path(state_dir)) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT * FROM task_timeline
            WHERE task_id = ?
            ORDER BY seq DESC
            LIMIT ?
            """,
            (task_id, limit),
        ).fetchall()
    rows = list(reversed(rows))
    if not rows:
        return None
    events = [_row_event_dict(row) for row in rows]
    return {
        "schema_version": "task-timeline.v1",
        "task_id": task_id,
        "event_count": len(events),
        "timeline": events,
        "events": events,
        "current_seq": int(status.get("projected_seq") or status.get("source_seq") or 0),
        "projection_state": status.get("projection_state", "unknown"),
        "projection_lag": status.get("projection_lag"),
        "source": "read_model.sqlite",
    }


def hydrate_event_by_seq(
    state_dir: Path,
    seq: int,
    *,
    config: ZfConfig | None = None,
) -> ZfEvent | None:
    try:
        with _connect(db_path(state_dir)) as conn:
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT raw_segment, raw_offset, raw_length FROM event_index WHERE seq = ?",
                (int(seq),),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return hydrate_event_at(
        state_dir,
        segment=str(row["raw_segment"]),
        offset=int(row["raw_offset"]),
        length=int(row["raw_length"]),
        config=config,
    )


def sidecar_refs(
    state_dir: Path,
    *,
    kind: str | None = None,
    ref: str | None = None,
    limit: int = 200,
    config: ZfConfig | None = None,
) -> dict[str, Any] | None:
    status = ensure_requested(state_dir, config=config)
    if status.get("projection_state") != "ready":
        rebuild(state_dir, config=config)
        status = projection_status(state_dir)
    if not db_path(state_dir).exists():
        return None
    where: list[str] = []
    args: list[Any] = []
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if ref:
        where.append("ref = ?")
        args.append(ref)
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, min(int(limit or 200), 1000))
    with _connect(db_path(state_dir)) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT *
            FROM sidecar_ref
            {sql_where}
            ORDER BY seq ASC
            LIMIT ?
            """,
            (*args, limit),
        ).fetchall()
    return {
        "schema_version": "sidecar-ref-index.v1",
        "items": [_sidecar_ref_row(row) for row in rows],
        "limit": limit,
        "current_seq": int(status.get("projected_seq") or status.get("source_seq") or 0),
        "projection_state": status.get("projection_state", "unknown"),
        "source": "read_model.sqlite",
    }


def _sidecar_ref_row(row: sqlite3.Row) -> dict[str, Any]:
    def load_json(value: object) -> dict[str, Any]:
        try:
            loaded = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    return {
        "kind": str(row["kind"] or ""),
        "ref": str(row["ref"] or ""),
        "seq": int(row["seq"] or 0),
        "event_id": str(row["event_id"] or ""),
        "sha256": str(row["sha256"] or ""),
        "byte_count": int(row["byte_count"] or 0),
        "content_type": str(row["content_type"] or ""),
        "schema_version": str(row["schema_version"] or ""),
        "required": bool(row["required"]),
        "access_scope": load_json(row["access_scope_json"]),
        "retention": load_json(row["retention_json"]),
        "source_event_id": str(row["source_event_id"] or ""),
        "preview": str(row["preview"] or ""),
    }


def hydrate_event_by_id(
    state_dir: Path,
    event_id: str,
    *,
    config: ZfConfig | None = None,
) -> tuple[int, ZfEvent] | None:
    if not event_id:
        return None
    try:
        with _connect(db_path(state_dir)) as conn:
            _ensure_schema(conn)
            row = conn.execute(
                """
                SELECT seq, raw_segment, raw_offset, raw_length
                FROM event_index
                WHERE event_id = ?
                ORDER BY seq DESC
                LIMIT 1
                """,
                (event_id,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    event = hydrate_event_at(
        state_dir,
        segment=str(row["raw_segment"]),
        offset=int(row["raw_offset"]),
        length=int(row["raw_length"]),
        config=config,
    )
    if event is None:
        return None
    return int(row["seq"] or 0), event


_SLIM_COLUMNS = (
    "seq, event_id, ts, type, actor, task_id, correlation_id, causation_id, payload_slim"
)


def _event_from_index_row(row: sqlite3.Row) -> ZfEvent:
    """Reconstruct a ZfEvent from indexed columns + slim payload, with NO raw
    file read. Lossy on the full payload (only the slim keep-set survives) but
    sufficient for structural projections (graph topology, trace grouping) that
    read type/actor/task_id/correlation_id and a few slim refs."""
    raw_payload = row["payload_slim"]
    try:
        payload = json.loads(raw_payload) if raw_payload else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return ZfEvent(
        type=str(row["type"] or ""),
        id=str(row["event_id"] or ""),
        ts=str(row["ts"] or ""),
        actor=row["actor"],
        task_id=row["task_id"],
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
        payload=payload,
    )


def hydrate_events(
    state_dir: Path,
    *,
    types: Sequence[str] | None = None,
    type_prefixes: Sequence[str] | None = None,
    task_id: str | None = None,
    channel_id: str | None = None,
    limit: int | None = None,
    config: ZfConfig | None = None,
    require_fresh: bool = True,
    slim: bool = False,
) -> list[ZfEvent]:
    status = ensure_requested(state_dir, config=config)
    if require_fresh and status.get("projection_state") != "ready":
        rebuild(state_dir, config=config)
    where: list[str] = []
    args: list[Any] = []
    type_filters: list[str] = []
    if types:
        placeholders = ", ".join("?" for _ in types)
        type_filters.append(f"type IN ({placeholders})")
        args.extend(types)
    if type_prefixes:
        prefix_clauses = []
        for prefix in type_prefixes:
            prefix_clauses.append("type LIKE ?")
            args.append(f"{prefix}%")
        type_filters.append(f"({' OR '.join(prefix_clauses)})")
    if type_filters:
        where.append(f"({' OR '.join(type_filters)})")
    if task_id:
        where.append("task_id = ?")
        args.append(task_id)
    if channel_id:
        where.append("channel_id = ?")
        args.append(channel_id)
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    sql_limit = "LIMIT ?" if limit is not None else ""
    if limit is not None:
        args.append(max(1, int(limit)))
    rows: list[sqlite3.Row]
    columns = _SLIM_COLUMNS if slim else "raw_segment, raw_offset, raw_length"
    try:
        with _connect(db_path(state_dir)) as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT {columns}
                FROM event_index
                {sql_where}
                ORDER BY seq ASC
                {sql_limit}
                """,
                tuple(args),
            ).fetchall()
    except sqlite3.Error:
        return []
    if slim:
        # Build straight from indexed columns — no per-event raw file read,
        # which is what made graph/trace projections take tens of seconds.
        return [_event_from_index_row(row) for row in rows]
    out: list[ZfEvent] = []
    for row in rows:
        event = hydrate_event_at(
            state_dir,
            segment=str(row["raw_segment"]),
            offset=int(row["raw_offset"]),
            length=int(row["raw_length"]),
            config=config,
        )
        if event is not None:
            out.append(event)
    return out


def agent_session_history(
    state_dir: Path,
    *,
    surface: str,
    thread_id: str,
    limit: int,
    before_seq: int | None = None,
    project_id: str = "",
    conversation_id: str = "",
    backend: str = "",
    task_id: str = "",
    config: ZfConfig | None = None,
) -> dict[str, Any] | None:
    """Page full agent-session events for a conversation surface."""

    if surface != "kanban_agent":
        return None
    status = ensure_requested(state_dir, config=config)
    if status.get("projection_state") != "ready":
        rebuild(state_dir, config=config)
        status = projection_status(state_dir)
    if not db_path(state_dir).exists():
        return None
    limit = max(1, min(int(limit or 120), 500))
    before = int(before_seq) if before_seq is not None else None
    scan_limit = min(max(limit * 20, 500), 5000)
    type_placeholders = ", ".join("?" for _ in KANBAN_AGENT_HISTORY_TYPES)
    prefix_clause = " OR ".join("type LIKE ?" for _ in KANBAN_AGENT_HISTORY_PREFIXES)
    where = [f"(type IN ({type_placeholders}) OR {prefix_clause})"]
    args: list[Any] = [
        *KANBAN_AGENT_HISTORY_TYPES,
        *(f"{prefix}%" for prefix in KANBAN_AGENT_HISTORY_PREFIXES),
    ]
    if before is not None:
        where.append("seq < ?")
        args.append(before)
    sql_where = f"WHERE {' AND '.join(where)}"
    try:
        with _connect(db_path(state_dir)) as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT seq, raw_segment, raw_offset, raw_length
                FROM event_index
                {sql_where}
                ORDER BY seq DESC
                LIMIT ?
                """,
                (*args, scan_limit),
            ).fetchall()
    except sqlite3.Error:
        return None

    out: list[dict[str, Any]] = []
    primary_oldest_seq: int | None = None
    for row in rows:
        seq = int(row["seq"])
        event = hydrate_event_at(
            state_dir,
            segment=str(row["raw_segment"]),
            offset=int(row["raw_offset"]),
            length=int(row["raw_length"]),
            config=config,
        )
        if event is None:
            continue
        if not _kanban_agent_history_event_matches(
            event,
            project_id=project_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            backend=backend,
            task_id=task_id,
        ):
            continue
        out.append(_full_event_dict(seq, event))
        primary_oldest_seq = seq
        if len(out) >= limit:
            break
    primary_items = list(reversed(out))
    context_items = _kanban_agent_context_events(
        state_dir,
        primary_items,
        project_id=project_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
        backend=backend,
        task_id=task_id,
        config=config,
    )
    items = _merge_full_events_by_seq(context_items, primary_items)
    next_before_seq = primary_oldest_seq if primary_items else before
    return {
        "schema_version": "agent-session-history.v1",
        "surface": surface,
        "thread_id": thread_id,
        "items": items,
        "limit": limit,
        "next_before_seq": next_before_seq,
        "has_more": bool(primary_items and len(primary_items) >= limit),
        "context_event_count": len(context_items),
        "current_seq": int(status.get("projected_seq") or status.get("source_seq") or 0),
        "projection_state": status.get("projection_state", "unknown"),
        "projection_lag": status.get("projection_lag"),
        "source": "read_model.sqlite",
    }


def get_cached_projection(
    state_dir: Path,
    key: str,
    *,
    source_seq: int | None = None,
) -> dict[str, Any] | None:
    if not db_path(state_dir).exists():
        return None
    try:
        with _connect(db_path(state_dir)) as conn:
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT payload_json, source_seq, updated_at FROM projection_cache WHERE key = ?",
                (key,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    if source_seq is not None and int(row["source_seq"] or 0) != int(source_seq):
        return None
    try:
        payload = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        payload.setdefault("projection_cache", {
            "key": key,
            "source": "read_model.sqlite",
            "source_seq": int(row["source_seq"] or 0),
            "updated_at": str(row["updated_at"] or ""),
        })
        return payload
    return {"payload": payload}


def set_cached_projection(
    state_dir: Path,
    key: str,
    *,
    kind: str,
    source_seq: int,
    payload: dict[str, Any],
) -> None:
    with _connect(db_path(state_dir)) as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO projection_cache(
              key, kind, source_seq, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                key,
                kind,
                int(source_seq),
                json.dumps(payload, ensure_ascii=False, default=str),
                _now(),
            ),
        )
        conn.commit()


def current_projected_seq(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    require_fresh: bool = True,
) -> int:
    status = ensure_requested(state_dir, config=config)
    if require_fresh and status.get("projection_state") != "ready":
        rebuild(state_dir, config=config)
        status = projection_status(state_dir)
    return int(status.get("projected_seq") or status.get("source_seq") or 0)


def channel_summary(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
) -> dict[str, Any] | None:
    from zf.runtime.channel_projection import CHANNEL_EVENT_TYPES, project_channels

    ensure_requested(state_dir, config=config)
    events = hydrate_events(state_dir, types=sorted(CHANNEL_EVENT_TYPES), config=config)
    if not events:
        return None
    projected = project_channels(state_dir, events=events)
    projected["source"] = "read_model.sqlite"
    projected["projection_state"] = projection_status(state_dir).get("projection_state", "unknown")
    return projected


def operator_inbox(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict[str, Any] | None:
    from zf.runtime.operator_inbox import (
        GENERIC_APPROVAL_EXPIRED,
        GENERIC_APPROVAL_POLICY_REJECTED,
        GENERIC_APPROVAL_REQUESTED,
        GENERIC_APPROVAL_RESOLVED,
        HUMAN_ESCALATION_ACKNOWLEDGED,
        HUMAN_ESCALATION_REQUESTED,
        HUMAN_ESCALATION_SENT,
        RUN_MANAGER_HUMAN_DECISION_APPLIED,
        RUN_MANAGER_HUMAN_DECISION_REJECTED,
        build_operator_inbox,
    )
    from zf.runtime.operator_plan_preview import (
        PLAN_APPROVAL_REQUESTED,
        PLAN_APPROVED,
        PLAN_REJECTED,
    )

    types = [
        PLAN_APPROVAL_REQUESTED,
        PLAN_APPROVED,
        PLAN_REJECTED,
        GENERIC_APPROVAL_REQUESTED,
        GENERIC_APPROVAL_RESOLVED,
        GENERIC_APPROVAL_EXPIRED,
        GENERIC_APPROVAL_POLICY_REJECTED,
        HUMAN_ESCALATION_REQUESTED,
        HUMAN_ESCALATION_SENT,
        HUMAN_ESCALATION_ACKNOWLEDGED,
        RUN_MANAGER_HUMAN_DECISION_APPLIED,
        RUN_MANAGER_HUMAN_DECISION_REJECTED,
    ]
    events = hydrate_events(
        state_dir,
        types=types,
        type_prefixes=["runtime.attention."],
        config=config,
        require_fresh=True,
        slim=True,
    )
    if not events:
        return None
    projected = build_operator_inbox(state_dir, events, project_root=project_root)
    projected["source"] = "read_model.sqlite"
    projected["projection_state"] = projection_status(state_dir).get("projection_state", "unknown")
    return projected


def _full_event_dict(seq: int, event: ZfEvent) -> dict[str, Any]:
    data = asdict(event)
    data["seq"] = seq
    return data


def _merge_full_events_by_seq(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            seq = item.get("seq")
            key = f"seq:{seq}" if seq is not None else f"id:{item.get('id')}"
            by_key[key] = item
    return sorted(by_key.values(), key=lambda item: int(item.get("seq") or 0))


def _kanban_agent_context_events(
    state_dir: Path,
    primary_items: list[dict[str, Any]],
    *,
    project_id: str,
    conversation_id: str,
    thread_id: str,
    backend: str,
    task_id: str,
    config: ZfConfig | None,
) -> list[dict[str, Any]]:
    if not primary_items:
        return []
    turn_ids, message_event_ids = _kanban_agent_context_keys(primary_items)
    if not turn_ids and not message_event_ids:
        return []
    primary_seqs = {int(item.get("seq") or 0) for item in primary_items if item.get("seq") is not None}
    type_placeholders = ", ".join("?" for _ in KANBAN_AGENT_CONTEXT_TYPES)
    try:
        with _connect(db_path(state_dir)) as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                f"""
                SELECT seq, raw_segment, raw_offset, raw_length
                FROM event_index
                WHERE type IN ({type_placeholders})
                ORDER BY seq DESC
                LIMIT ?
                """,
                (*KANBAN_AGENT_CONTEXT_TYPES, 5000),
            ).fetchall()
    except sqlite3.Error:
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        seq = int(row["seq"])
        if seq in primary_seqs:
            continue
        event = hydrate_event_at(
            state_dir,
            segment=str(row["raw_segment"]),
            offset=int(row["raw_offset"]),
            length=int(row["raw_length"]),
            config=config,
        )
        if event is None:
            continue
        if not _kanban_agent_history_event_matches(
            event,
            project_id=project_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            backend=backend,
            task_id=task_id,
        ):
            continue
        if not _kanban_agent_is_context_event(event, turn_ids=turn_ids, message_event_ids=message_event_ids):
            continue
        out.append(_full_event_dict(seq, event))
        if len(out) >= 80:
            break
    return list(reversed(out))


def _kanban_agent_context_keys(items: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    turn_ids: set[str] = set()
    message_event_ids: set[str] = set()
    for item in items:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        for key in ("turn_id", "run_id"):
            value = str(payload.get(key) or "").strip()
            if value:
                turn_ids.add(value)
        for key in ("message_event_id", "message_id"):
            value = str(payload.get(key) or "").strip()
            if value:
                message_event_ids.add(value)
        causation_id = str(item.get("causation_id") or "").strip()
        if causation_id and str(item.get("type") or "") == "kanban.agent.turn.created":
            message_event_ids.add(causation_id)
    return turn_ids, message_event_ids


def _kanban_agent_is_context_event(
    event: ZfEvent,
    *,
    turn_ids: set[str],
    message_event_ids: set[str],
) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.id and event.id in message_event_ids:
        return True
    for key in ("message_event_id", "message_id"):
        if str(payload.get(key) or "").strip() in message_event_ids:
            return True
    for key in ("turn_id", "run_id"):
        if str(payload.get(key) or "").strip() in turn_ids:
            return True
    request = payload.get("request")
    if isinstance(request, dict) and str(request.get("turn_id") or "").strip() in turn_ids:
        return True
    if str(event.causation_id or "").strip() in message_event_ids:
        return True
    return False


def _kanban_agent_history_event_matches(
    event: ZfEvent,
    *,
    project_id: str,
    conversation_id: str,
    thread_id: str,
    backend: str,
    task_id: str,
) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    event_type = str(event.type or "")
    if event_type == "user.message":
        if str(payload.get("target") or "") != "kanban-agent":
            return False
        if str(payload.get("runtime_delivery") or "") != "headless":
            return False
    elif not (
        event_type.startswith("kanban.agent.turn.")
        or event_type.startswith("kanban.agent.message.")
        or event_type == "kanban.agent.reply"
        or event_type.startswith("agent.session.")
    ):
        return False
    payload_project = str(payload.get("project_id") or "").strip()
    if project_id and payload_project and payload_project != project_id:
        return False
    payload_conversation = str(payload.get("conversation_id") or "").strip()
    if conversation_id and payload_conversation and payload_conversation != conversation_id:
        return False
    payload_thread = str(payload.get("thread_key") or payload.get("thread_id") or "").strip() or "main"
    if thread_id and payload_thread != thread_id:
        return False
    payload_backend = str(payload.get("backend") or payload.get("provider") or "").strip()
    if backend and payload_backend and _canonical_backend(payload_backend) != _canonical_backend(backend):
        return False
    if task_id and event.task_id and event.task_id != task_id:
        return False
    return True


def _canonical_backend(value: str) -> str:
    raw = str(value or "").strip()
    if raw in {"claude", "claude-code", "claude-code-headless", "claude_headless"}:
        return "claude-headless"
    if raw in {"codex", "codex-cli", "codex-app-server", "codex_headless"}:
        return "codex-headless"
    return raw


def _row_event_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())

    def value(key: str) -> Any:
        return row[key] if key in keys else None

    payload = {}
    try:
        payload = json.loads(str(value("payload_slim") or "{}"))
    except json.JSONDecodeError:
        payload = {}
    return {
        "seq": int(value("seq") or 0),
        "id": str(value("event_id") or ""),
        "ts": str(value("ts") or ""),
        "type": str(value("type") or ""),
        "actor": value("actor"),
        "task_id": value("task_id"),
        "payload": payload,
        "payload_slim": True,
        "summary": str(value("summary") or ""),
        "status": str(value("status") or ""),
        "causation_id": value("causation_id"),
        "correlation_id": value("correlation_id") or value("trace_id"),
        "event_ref": {
            "raw_segment": str(value("raw_segment") or ""),
            "raw_offset": int(value("raw_offset") or 0),
            "raw_length": int(value("raw_length") or 0),
        },
    }


def _event_refs(
    event: ZfEvent,
    payload: dict[str, Any],
    *,
    feature_id: str,
    trace_id: str,
    channel_id: str,
) -> Iterable[tuple[str, str]]:
    refs = [
        ("event", event.id),
        ("type", event.type),
        ("actor", event.actor or ""),
        ("task", event.task_id or ""),
        ("trace", trace_id),
        ("feature", feature_id),
        ("channel", channel_id),
        ("causation", event.causation_id or ""),
        ("correlation", event.correlation_id or ""),
    ]
    for key in (
        "run_id",
        "fanout_id",
        "loop_id",
        "dispatch_id",
        "plan_id",
        "approval_id",
        "approval_ref",
        "message_id",
        "thread_id",
        "worker_id",
        "instance_id",
    ):
        value = _payload_ref(payload, key)
        if value:
            refs.append((key, value))
    seen: set[tuple[str, str]] = set()
    for kind, ref_id in refs:
        ref_id = str(ref_id or "").strip()
        if not ref_id:
            continue
        item = (kind, ref_id)
        if item in seen:
            continue
        seen.add(item)
        yield item


def _payload_slim(payload: dict[str, Any]) -> dict[str, Any]:
    keep: dict[str, Any] = {}
    for key in (
        "summary",
        "source",
        "target",
        "message",
        "message_type",
        "reason",
        "text",
        "content",
        "delta",
        "answer",
        "status",
        "backend",
        "project_id",
        "conversation_id",
        "task_id",
        "feature_id",
        "trace_id",
        "channel_id",
        "thread_key",
        "run_id",
        "turn_id",
        "fanout_id",
        "loop_id",
        "plan_id",
        "stage_id",
        "pdd_id",
        "task_count",
        "digest_ref",
        "task_map_ref",
        "approval_ref",
        "approval_id",
        "title",
        "approve_action",
        "reject_action",
        "resolution",
        "message_id",
        "thread_id",
        "attention_id",
        "fingerprint",
        "source_event_id",
        "projection_ref",
        "snooze_until",
        "decision_token",
        "response_token",
        "source_message_id",
        "escalation_event_id",
        "checkpoint_id",
        "question",
        "decision",
        "next_route",
        "id",
        "provider_session_id",
        "session_id",
        "runtime_delivery",
        "requested_action",
        "scope",
        "tool",
        "input",
        "output",
        "usage",
        "error",
        "resumed",
        "fallback_reason",
        "mutates_task_state",
        "action_proposal",
        "has_action_proposal",
        "delta_count",
        "reply_event_id",
        "provider_stop_reason",
        "stop_reason",
    ):
        value = payload.get(key)
        if value in (None, ""):
            continue
        if key == "action_proposal" and isinstance(value, dict):
            keep[key] = redact_obj(value)
            continue
        if isinstance(value, (str, int, float, bool)):
            keep[key] = value
        elif isinstance(value, list):
            keep[key] = value[:20]
        elif isinstance(value, dict):
            keep[key] = _shallow_dict(value)
    if not keep and payload:
        for key, value in list(payload.items())[:8]:
            if isinstance(value, (str, int, float, bool)):
                keep[str(key)] = value
    redacted = redact_obj(keep)
    if isinstance(redacted, dict):
        for key in _PAYLOAD_SLIM_SAFE_ROUTING_KEYS:
            if key in keep:
                redacted[key] = keep[key]
    return redacted


def _shallow_dict(value: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in list(value.items())[:20]:
        if isinstance(item, (str, int, float, bool)) or item is None:
            out[str(key)] = item
        else:
            out[str(key)] = str(item)[:240]
    return out


def _event_summary(event_type: str, payload: dict[str, Any]) -> str:
    for key in ("summary", "message", "reason", "text", "status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    if event_type == "worker.state.changed":
        from_state = payload.get("from")
        to_state = payload.get("to")
        if to_state:
            return f"{from_state or 'unknown'} -> {to_state}"[:240]
    stop_reason = _first_nonempty(payload.get("provider_stop_reason"), payload.get("stop_reason"))
    return str(stop_reason or "")[:240]


def _payload_ref(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def _first_nonempty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _feature_from_task_id(task_id: str | None) -> str:
    if not task_id or ":" not in task_id:
        return ""
    return task_id.split(":", 1)[0]


def _event_status(event_type: str) -> str:
    if event_type.endswith((".failed", ".rejected")):
        return "failed"
    if event_type.endswith((".passed", ".approved", ".completed", ".done")):
        return "done"
    if event_type.endswith(".blocked"):
        return "blocked"
    return ""


def _payload_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _layout_digest(manifest: Any) -> str:
    digest = hashlib.sha256()
    for segment in manifest.segments:
        size_part = segment.size if segment.kind != "active" else 0
        mtime_part = segment.mtime_ns if segment.kind != "active" else 0
        digest.update(
            f"{segment.ordinal}\0{segment.rel_path}\0{segment.kind}\0{size_part}\0{mtime_part}\n".encode("utf-8")
        )
    return digest.hexdigest()
