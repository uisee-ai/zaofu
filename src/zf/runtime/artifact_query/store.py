"""SQLite storage for rebuildable artifact metadata and lineage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.artifact_query.extractors import (
    semantic_kind_for_descriptor,
)
from zf.runtime.artifact_query.store_rows import (
    catalog_row as _catalog_row,
    is_result_event as _is_result_event,
    relation as _relation,
    subjects as _subjects,
)


CATALOG_SCHEMA_VERSION = "artifact-catalog.v2"
EXTRACTOR_VERSION = "sidecar-descriptor-extractor.v4"
CATALOG_PROJECTOR_VERSION = "artifact-catalog-projector.v4"
MAX_REDUCER_PROJECTIONS = 256
MAX_REDUCER_PAYLOAD_BYTES = 2_000_000
MAX_WAL_BYTES = 8 * 1024 * 1024
CATALOG_CATCH_UP_WAIT_SECONDS = 2.0


def projection_db_path(state_dir: Path) -> Path:
    return Path(state_dir) / "projections" / "read_model.sqlite"


def connect_projection_db(state_dir: Path) -> sqlite3.Connection:
    path = projection_db_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    conn.execute(f"PRAGMA journal_size_limit={MAX_WAL_BYTES}")
    return conn


def ensure_catalog_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS artifact_query_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifact_object (
          object_id TEXT PRIMARY KEY,
          sha256 TEXT NOT NULL,
          byte_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifact_locator (
          locator_id TEXT PRIMARY KEY,
          project_scope TEXT NOT NULL,
          state_scope TEXT NOT NULL,
          ref TEXT NOT NULL,
          object_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          schema_version TEXT,
          content_type TEXT,
          encoding TEXT,
          health TEXT NOT NULL,
          last_verified_at TEXT,
          last_verified_digest TEXT,
          extractor_version TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_artifact_locator_ref
          ON artifact_locator(ref);
        CREATE INDEX IF NOT EXISTS idx_artifact_locator_object
          ON artifact_locator(object_id);
        CREATE INDEX IF NOT EXISTS idx_artifact_locator_kind
          ON artifact_locator(kind);

        CREATE TABLE IF NOT EXISTS artifact_occurrence (
          occurrence_id TEXT PRIMARY KEY,
          locator_id TEXT NOT NULL,
          storage_kind TEXT NOT NULL,
          semantic_kind TEXT NOT NULL,
          event_id TEXT,
          source_event_id TEXT,
          source_seq INTEGER NOT NULL,
          source_kind TEXT NOT NULL,
          producer_actor TEXT,
          status TEXT,
          run_id TEXT,
          task_id TEXT,
          claim_id TEXT,
          stage_id TEXT,
          attempt_id TEXT,
          attempt_domain TEXT,
          operation_id TEXT,
          package_id TEXT,
          required INTEGER NOT NULL,
          access_scope_json TEXT NOT NULL,
          retention_json TEXT NOT NULL,
          created_by TEXT,
          preview TEXT,
          extractor_version TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_artifact_occurrence_seq
          ON artifact_occurrence(source_seq);
        CREATE INDEX IF NOT EXISTS idx_artifact_occurrence_task
          ON artifact_occurrence(task_id, source_seq);
        CREATE INDEX IF NOT EXISTS idx_artifact_occurrence_run
          ON artifact_occurrence(run_id, source_seq);
        CREATE INDEX IF NOT EXISTS idx_artifact_occurrence_attempt
          ON artifact_occurrence(attempt_id, source_seq);
        CREATE INDEX IF NOT EXISTS idx_artifact_occurrence_operation
          ON artifact_occurrence(operation_id, source_seq);
        CREATE INDEX IF NOT EXISTS idx_artifact_occurrence_package
          ON artifact_occurrence(package_id, source_seq);
        CREATE TABLE IF NOT EXISTS artifact_edge (
          edge_id TEXT PRIMARY KEY,
          subject_kind TEXT NOT NULL,
          subject_id TEXT NOT NULL,
          relation TEXT NOT NULL,
          occurrence_id TEXT NOT NULL,
          locator_id TEXT NOT NULL,
          source_event_id TEXT,
          causation_event_id TEXT,
          result_event_id TEXT,
          source_seq INTEGER NOT NULL,
          attempt_domain TEXT,
          extractor_version TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_artifact_edge_subject
          ON artifact_edge(subject_kind, subject_id, source_seq);
        CREATE INDEX IF NOT EXISTS idx_artifact_edge_locator
          ON artifact_edge(locator_id, source_seq);

        CREATE TABLE IF NOT EXISTS artifact_reducer_projection (
          projection_kind TEXT NOT NULL,
          subject_id TEXT NOT NULL,
          source_snapshot_key TEXT NOT NULL,
          source_seq INTEGER NOT NULL,
          reducer_version TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (projection_kind, subject_id)
        );
        """
    )
    columns = {
        str(row["name"])
        for row in conn.execute(
            "PRAGMA table_info(artifact_occurrence)"
        ).fetchall()
    }
    if "source_event_id" not in columns:
        conn.execute(
            "ALTER TABLE artifact_occurrence ADD COLUMN source_event_id TEXT"
        )
    if "storage_kind" not in columns:
        conn.execute(
            "ALTER TABLE artifact_occurrence "
            "ADD COLUMN storage_kind TEXT NOT NULL DEFAULT 'sidecar'"
        )
    if "semantic_kind" not in columns:
        conn.execute(
            "ALTER TABLE artifact_occurrence "
            "ADD COLUMN semantic_kind TEXT NOT NULL DEFAULT 'untyped'"
        )
    if "claim_id" not in columns:
        conn.execute(
            "ALTER TABLE artifact_occurrence ADD COLUMN claim_id TEXT"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifact_occurrence_semantic_kind "
        "ON artifact_occurrence(semantic_kind, source_seq)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifact_occurrence_claim "
        "ON artifact_occurrence(claim_id, source_seq)"
    )
    _set_meta_if_missing(conn, "catalog_schema_version", CATALOG_SCHEMA_VERSION)
    conn.commit()


def catch_up_catalog(
    state_dir: Path,
    *,
    project_root: Path,
    config: ZfConfig | None = None,
    wait_timeout_seconds: float = CATALOG_CATCH_UP_WAIT_SECONDS,
) -> dict[str, Any]:
    from .maintenance import catch_up_catalog as _catch_up_catalog

    return _catch_up_catalog(
        state_dir,
        project_root=project_root,
        config=config,
        wait_timeout_seconds=wait_timeout_seconds,
    )


def rebuild_catalog(
    state_dir: Path,
    *,
    project_root: Path,
    config: ZfConfig | None = None,
    force: bool = False,
) -> dict[str, Any]:
    from .maintenance import rebuild_catalog as _rebuild_catalog

    return _rebuild_catalog(
        state_dir,
        project_root=project_root,
        config=config,
        force=force,
    )


def recover_catalog_projection(
    state_dir: Path,
    *,
    project_root: Path,
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    from .maintenance import (
        recover_catalog_projection as _recover_catalog_projection,
    )

    return _recover_catalog_projection(
        state_dir,
        project_root=project_root,
        config=config,
    )


def catalog_status(
    state_dir: Path,
    *,
    conn: sqlite3.Connection | None = None,
    count_source: bool = False,
) -> dict[str, Any]:
    from .maintenance import catalog_status as _catalog_status

    return _catalog_status(
        state_dir,
        conn=conn,
        count_source=count_source,
    )


def catalog_rows(
    state_dir: Path,
    *,
    kind: str = "",
    semantic_kind: str = "",
    ref: str = "",
    task_id: str = "",
    claim_id: str = "",
    run_id: str = "",
    attempt_id: str = "",
    operation_id: str = "",
    package_id: str = "",
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    bounded = max(1, min(int(limit or 200), 1000))
    return _select_catalog_rows(
        state_dir,
        kind=kind,
        semantic_kind=semantic_kind,
        ref=ref,
        task_id=task_id,
        claim_id=claim_id,
        run_id=run_id,
        attempt_id=attempt_id,
        operation_id=operation_id,
        package_id=package_id,
        limit=bounded,
        offset=max(0, int(offset or 0)),
    )


def catalog_matching_rows(
    state_dir: Path,
    *,
    kind: str = "",
    semantic_kind: str = "",
    ref: str = "",
    task_id: str = "",
    claim_id: str = "",
    run_id: str = "",
    attempt_id: str = "",
    operation_id: str = "",
    package_id: str = "",
    max_rows: int = 100_000,
) -> tuple[list[dict[str, Any]], bool]:
    """Return occurrence rows for authorization-aware object aggregation."""

    bounded = max(1, min(int(max_rows or 100_000), 100_000))
    return _select_catalog_rows(
        state_dir,
        kind=kind,
        semantic_kind=semantic_kind,
        ref=ref,
        task_id=task_id,
        claim_id=claim_id,
        run_id=run_id,
        attempt_id=attempt_id,
        operation_id=operation_id,
        package_id=package_id,
        limit=bounded,
        offset=0,
    )


def _select_catalog_rows(
    state_dir: Path,
    *,
    kind: str,
    semantic_kind: str,
    ref: str,
    task_id: str,
    claim_id: str,
    run_id: str,
    attempt_id: str,
    operation_id: str,
    package_id: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], bool]:
    where: list[str] = []
    args: list[Any] = []
    filters = {
        "l.kind": kind,
        "o.semantic_kind": semantic_kind,
        "l.ref": ref,
        "o.task_id": task_id,
        "o.claim_id": claim_id,
        "o.run_id": run_id,
        "o.attempt_id": attempt_id,
        "o.operation_id": operation_id,
        "o.package_id": package_id,
    }
    for column, value in filters.items():
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    with connect_projection_db(state_dir) as conn:
        ensure_catalog_schema(conn)
        rows = conn.execute(
            f"""
            SELECT
              b.object_id, b.sha256, b.byte_count,
              l.locator_id, l.project_scope, l.state_scope, l.ref, l.kind,
              l.schema_version, l.content_type, l.encoding, l.health,
              o.occurrence_id, o.event_id, o.source_event_id, o.source_seq,
              o.source_kind, o.storage_kind, o.semantic_kind,
              o.producer_actor, o.status, o.run_id, o.task_id, o.claim_id,
              o.stage_id,
              o.attempt_id, o.attempt_domain, o.operation_id, o.package_id,
              o.required, o.access_scope_json, o.retention_json, o.created_by,
              o.preview
            FROM artifact_occurrence AS o
            JOIN artifact_locator AS l ON l.locator_id = o.locator_id
            JOIN artifact_object AS b ON b.object_id = l.object_id
            {sql_where}
            ORDER BY o.source_seq DESC, o.occurrence_id
            LIMIT ? OFFSET ?
            """,
            (*args, limit + 1, offset),
        ).fetchall()
    return [_catalog_row(row) for row in rows[:limit]], len(rows) > limit


def catalog_show_rows(
    state_dir: Path,
    identity: str,
) -> tuple[list[dict[str, Any]], str]:
    identity = str(identity or "").strip()
    if not identity:
        return [], ""
    with connect_projection_db(state_dir) as conn:
        ensure_catalog_schema(conn)
        rows = conn.execute(
            """
            SELECT
              b.object_id, b.sha256, b.byte_count,
              l.locator_id, l.project_scope, l.state_scope, l.ref, l.kind,
              l.schema_version, l.content_type, l.encoding, l.health,
              o.occurrence_id, o.event_id, o.source_event_id, o.source_seq,
              o.source_kind, o.storage_kind, o.semantic_kind,
              o.producer_actor, o.status, o.run_id, o.task_id, o.claim_id,
              o.stage_id,
              o.attempt_id, o.attempt_domain, o.operation_id, o.package_id,
              o.required, o.access_scope_json, o.retention_json, o.created_by,
              o.preview
            FROM artifact_occurrence AS o
            JOIN artifact_locator AS l ON l.locator_id = o.locator_id
            JOIN artifact_object AS b ON b.object_id = l.object_id
            WHERE o.occurrence_id = ? OR l.locator_id = ? OR b.object_id = ?
               OR b.sha256 = ? OR l.ref = ?
            ORDER BY o.source_seq DESC, o.occurrence_id
            """,
            (identity, identity, identity, identity, identity),
        ).fetchall()
    items = [_catalog_row(row) for row in rows]
    matched_by = ""
    for field in ("occurrence_id", "locator_id", "object_id", "sha256", "ref"):
        if any(str(row.get(field) or "") == identity for row in items):
            matched_by = field.removesuffix("_id")
            break
    if matched_by == "occurrence":
        items = [
            row for row in items
            if str(row.get("occurrence_id") or "") == identity
        ]
    elif matched_by == "locator":
        items = [
            row for row in items
            if str(row.get("locator_id") or "") == identity
        ]
    elif matched_by in {"object", "sha256"} and items:
        object_id = next(
            str(row.get("object_id") or "")
            for row in items
            if str(row.get(
                "object_id" if matched_by == "object" else "sha256"
            ) or "") == identity
        )
        items = [
            row for row in items
            if str(row.get("object_id") or "") == object_id
        ]
    elif matched_by == "ref":
        items = [
            row for row in items
            if str(row.get("ref") or "") == identity
        ]
    return items, matched_by


def lineage_rows(
    state_dir: Path,
    *,
    subject_kind: str,
    subject_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit or 200), 1000))
    with connect_projection_db(state_dir) as conn:
        ensure_catalog_schema(conn)
        rows = conn.execute(
            """
            SELECT
              e.edge_id, e.subject_kind, e.subject_id, e.relation,
              e.occurrence_id, e.locator_id, e.source_event_id,
              e.causation_event_id, e.result_event_id, e.source_seq,
              e.attempt_domain, l.ref, l.kind, b.object_id, b.sha256,
              o.semantic_kind, o.access_scope_json
            FROM artifact_edge AS e
            JOIN artifact_locator AS l ON l.locator_id = e.locator_id
            JOIN artifact_object AS b ON b.object_id = l.object_id
            JOIN artifact_occurrence AS o ON o.occurrence_id = e.occurrence_id
            WHERE e.subject_kind = ? AND e.subject_id = ?
            ORDER BY e.source_seq DESC, e.edge_id
            LIMIT ?
            """,
            (subject_kind, subject_id, bounded),
        ).fetchall()
    return [
        {
            **dict(row),
            "access_scope": _json_object(row["access_scope_json"]),
        }
        for row in rows
    ]


def get_reducer_projection(
    state_dir: Path,
    *,
    projection_kind: str,
    subject_id: str,
    source_snapshot_key: str,
) -> dict[str, Any] | None:
    with connect_projection_db(state_dir) as conn:
        ensure_catalog_schema(conn)
        row = conn.execute(
            """
            SELECT payload_json, source_seq, reducer_version, updated_at
            FROM artifact_reducer_projection
            WHERE projection_kind = ? AND subject_id = ?
              AND source_snapshot_key = ?
            """,
            (projection_kind, subject_id, source_snapshot_key),
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def set_reducer_projection(
    state_dir: Path,
    *,
    projection_kind: str,
    subject_id: str,
    source_snapshot_key: str,
    source_seq: int,
    reducer_version: str,
    payload: Mapping[str, Any],
) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded.encode("utf-8")) > MAX_REDUCER_PAYLOAD_BYTES:
        return
    with connect_projection_db(state_dir) as conn:
        ensure_catalog_schema(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO artifact_reducer_projection(
              projection_kind, subject_id, source_snapshot_key, source_seq,
              reducer_version, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                projection_kind,
                subject_id,
                source_snapshot_key,
                int(source_seq),
                reducer_version,
                encoded,
                _now(),
            ),
        )
        conn.execute(
            """
            DELETE FROM artifact_reducer_projection
            WHERE rowid IN (
              SELECT rowid
              FROM artifact_reducer_projection
              ORDER BY updated_at DESC, rowid DESC
              LIMIT -1 OFFSET ?
            )
            """,
            (MAX_REDUCER_PROJECTIONS,),
        )
        conn.commit()


def descriptor_record(
    *,
    project_root: Path,
    state_dir: Path,
    event: ZfEvent,
    descriptor: Mapping[str, Any],
    source_seq: int,
) -> dict[str, Any] | None:
    ref = str(descriptor.get("ref") or "").strip()
    storage_kind = str(
        descriptor.get("storage_kind")
        or descriptor.get("kind")
        or "sidecar"
    ).strip()
    semantic_kind = str(
        descriptor.get("semantic_kind")
        or semantic_kind_for_descriptor(descriptor)
    ).strip()
    if not ref:
        return None
    sha256 = str(descriptor.get("sha256") or "").strip()
    object_id = (
        f"sha256:{sha256}"
        if sha256
        else "legacy-ref:" + _digest([str(state_dir.resolve()), ref])
    )
    locator_id = "locator:" + _digest(
        [str(project_root.resolve()), str(state_dir.resolve()), ref, object_id]
    )
    payload = event.payload if isinstance(event.payload, dict) else {}
    occurrence_id = "occurrence:" + _digest(
        [
            event.id,
            str(source_seq),
            locator_id,
            storage_kind,
            semantic_kind,
            json.dumps(descriptor.get("access_scope") or {}, sort_keys=True),
            json.dumps(descriptor.get("retention") or {}, sort_keys=True),
        ]
    )
    run_id = _first(
        payload.get("workflow_run_id"),
        payload.get("run_id"),
        event.correlation_id,
    )
    task_id = _first(event.task_id, payload.get("task_id"))
    claim_id = _first(
        payload.get("claim_id"),
        payload.get("goal_claim_id"),
    )
    attempt_id = _first(
        payload.get("attempt_id"),
        payload.get("active_attempt_id"),
        payload.get("dispatch_id"),
    )
    operation_id = _first(
        payload.get("operation_id"),
        payload.get("workflow_operation_id"),
        payload.get("parent_operation_id"),
    )
    package_id = _first(
        payload.get("plan_artifact_package_id"),
        payload.get("package_id"),
    )
    return {
        "object_id": object_id,
        "sha256": sha256,
        "byte_count": int(descriptor.get("byte_count") or 0),
        "locator_id": locator_id,
        "project_scope": str(project_root.resolve()),
        "state_scope": str(state_dir.resolve()),
        "ref": ref,
        "kind": storage_kind,
        "storage_kind": storage_kind,
        "semantic_kind": semantic_kind,
        "schema_version": str(descriptor.get("schema_version") or ""),
        "content_type": str(descriptor.get("content_type") or ""),
        "encoding": str(descriptor.get("encoding") or "utf-8"),
        "health": "unknown",
        "occurrence_id": occurrence_id,
        "event_id": event.id,
        "source_seq": source_seq,
        "source_kind": "event",
        "producer_actor": event.actor or "",
        "status": str(payload.get("status") or ""),
        "run_id": run_id,
        "task_id": task_id,
        "claim_id": claim_id,
        "stage_id": str(payload.get("stage_id") or ""),
        "attempt_id": attempt_id,
        "attempt_domain": str(payload.get("attempt_domain") or ""),
        "operation_id": operation_id,
        "package_id": package_id,
        "required": bool(descriptor.get("required", False)),
        "access_scope": dict(descriptor.get("access_scope") or {}),
        "retention": dict(descriptor.get("retention") or {}),
        "created_by": str(descriptor.get("created_by") or ""),
        "preview": str(descriptor.get("preview") or "")[:500],
        "source_event_id": str(descriptor.get("source_event_id") or event.id),
        "causation_event_id": str(event.causation_id or ""),
        "result_event_id": (
            event.id if _is_result_event(event.type) else
            str(payload.get("result_event_id") or "")
        ),
        "relation": _relation(
            event=event,
            descriptor=descriptor,
        ),
    }


def _insert_descriptor(
    conn: sqlite3.Connection,
    *,
    project_root: Path,
    state_dir: Path,
    event: ZfEvent,
    payload: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    source_seq: int,
) -> dict[str, int] | None:
    del payload
    row = descriptor_record(
        project_root=project_root,
        state_dir=state_dir,
        event=event,
        descriptor=descriptor,
        source_seq=source_seq,
    )
    if row is None:
        return None
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO artifact_object(object_id, sha256, byte_count)
        VALUES (?, ?, ?)
        """,
        (row["object_id"], row["sha256"], row["byte_count"]),
    )
    object_inserted = conn.total_changes - before
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO artifact_locator(
          locator_id, project_scope, state_scope, ref, object_id, kind,
          schema_version, content_type, encoding, health, last_verified_at,
          last_verified_digest, extractor_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["locator_id"],
            row["project_scope"],
            row["state_scope"],
            row["ref"],
            row["object_id"],
            row["kind"],
            row["schema_version"],
            row["content_type"],
            row["encoding"],
            row["health"],
            "",
            "",
            EXTRACTOR_VERSION,
        ),
    )
    locator_inserted = conn.total_changes - before
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO artifact_occurrence(
          occurrence_id, locator_id, storage_kind, semantic_kind, event_id,
          source_seq, source_kind, source_event_id, producer_actor, status,
          run_id, task_id, claim_id, stage_id, attempt_id,
          attempt_domain, operation_id, package_id, required,
          access_scope_json, retention_json, created_by, preview,
          extractor_version
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
          ?
        )
        """,
        (
            row["occurrence_id"],
            row["locator_id"],
            row["storage_kind"],
            row["semantic_kind"],
            row["event_id"],
            row["source_seq"],
            row["source_kind"],
            row["source_event_id"],
            row["producer_actor"],
            row["status"],
            row["run_id"],
            row["task_id"],
            row["claim_id"],
            row["stage_id"],
            row["attempt_id"],
            row["attempt_domain"],
            row["operation_id"],
            row["package_id"],
            1 if row["required"] else 0,
            json.dumps(row["access_scope"], ensure_ascii=False, sort_keys=True),
            json.dumps(row["retention"], ensure_ascii=False, sort_keys=True),
            row["created_by"],
            row["preview"],
            EXTRACTOR_VERSION,
        ),
    )
    occurrence_inserted = conn.total_changes - before
    edges = 0
    for subject_kind, subject_id in _subjects(row):
        edge_id = "edge:" + _digest(
            [
                subject_kind,
                subject_id,
                row["relation"],
                row["occurrence_id"],
            ]
        )
        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO artifact_edge(
              edge_id, subject_kind, subject_id, relation, occurrence_id,
              locator_id, source_event_id, causation_event_id, result_event_id,
              source_seq, attempt_domain, extractor_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                subject_kind,
                subject_id,
                row["relation"],
                row["occurrence_id"],
                row["locator_id"],
                row["source_event_id"],
                row["causation_event_id"],
                row["result_event_id"],
                row["source_seq"],
                row["attempt_domain"],
                EXTRACTOR_VERSION,
            ),
        )
        edges += conn.total_changes - before
    return {
        "object": object_inserted,
        "locator": locator_inserted,
        "occurrence": occurrence_inserted,
        "edges": edges,
    }


def _meta(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute(
            "SELECT key, value FROM artifact_query_meta"
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["key"]): str(row["value"]) for row in rows}


def _set_meta(conn: sqlite3.Connection, key: str, value: object) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO artifact_query_meta(key, value) VALUES (?, ?)",
        (key, str(value)),
    )


def _set_meta_if_missing(
    conn: sqlite3.Connection,
    key: str,
    value: object,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO artifact_query_meta(key, value) VALUES (?, ?)",
        (key, str(value)),
    )


def _json_object(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _first(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "CATALOG_PROJECTOR_VERSION",
    "EXTRACTOR_VERSION",
    "MAX_REDUCER_PAYLOAD_BYTES",
    "MAX_REDUCER_PROJECTIONS",
    "MAX_WAL_BYTES",
    "catch_up_catalog",
    "catalog_matching_rows",
    "catalog_rows",
    "catalog_show_rows",
    "catalog_status",
    "connect_projection_db",
    "descriptor_record",
    "ensure_catalog_schema",
    "get_reducer_projection",
    "lineage_rows",
    "projection_db_path",
    "recover_catalog_projection",
    "rebuild_catalog",
    "set_reducer_projection",
]
