"""Incremental build, status, and recovery for the artifact catalog."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.events.segments import (
    EventManifest,
    EventRecord,
    build_event_manifest,
    count_event_records,
    hydrate_event_at,
    iter_event_records,
    iter_event_records_from_cursor,
)
from zf.core.state.locks import locked_path
from . import store as catalog_store
from .extractors import iter_catalog_descriptors


def catch_up_catalog(
    state_dir: Path,
    *,
    project_root: Path,
    config: ZfConfig | None = None,
    wait_timeout_seconds: float = catalog_store.CATALOG_CATCH_UP_WAIT_SECONDS,
) -> dict[str, Any]:
    """Incrementally project complete EventLog records after the saved cursor."""

    started_at = time.monotonic()
    state_dir = Path(state_dir)
    manifest = build_event_manifest(state_dir)
    path = catalog_store.projection_db_path(state_dir)
    lock_started_at = time.monotonic()
    with locked_path(path, timeout_seconds=wait_timeout_seconds):
        lock_wait_ms = round(
            (time.monotonic() - lock_started_at) * 1000,
            3,
        )
        with catalog_store.connect_projection_db(state_dir) as conn:
            catalog_store.ensure_catalog_schema(conn)
            meta = catalog_store._meta(conn)
            rebuild_reason = _catalog_rebuild_reason(
                state_dir,
                manifest=manifest,
                meta=meta,
                config=config,
            )
            if rebuild_reason:
                return {
                    **catalog_status(state_dir, conn=conn),
                    "projection_state": "rebuild_required",
                    "rebuild_reason": rebuild_reason,
                    "lock_wait_ms": lock_wait_ms,
                    "catch_up_duration_ms": round(
                        (time.monotonic() - started_at) * 1000,
                        3,
                    ),
                }
            if (
                meta.get("source_manifest_digest") == manifest.digest
                and meta.get("descriptor_extractor_version")
                == catalog_store.EXTRACTOR_VERSION
            ):
                return {
                    **catalog_status(state_dir, conn=conn),
                    "records_projected": 0,
                    "lock_wait_ms": lock_wait_ms,
                    "catch_up_duration_ms": round(
                        (time.monotonic() - started_at) * 1000,
                        3,
                    ),
                }

            start_seq = int(meta.get("source_seq") or 0)
            cursor_segment = str(meta.get("cursor_segment") or "")
            cursor_offset = int(meta.get("cursor_byte_offset") or 0)
            counts = _empty_insert_counts()
            last_record: EventRecord | None = None
            conn.execute("BEGIN IMMEDIATE")
            try:
                for record in iter_event_records_from_cursor(
                    state_dir,
                    segment=cursor_segment,
                    byte_offset=cursor_offset,
                    start_seq=start_seq,
                    config=config,
                ):
                    last_record = record
                    _project_record(
                        conn,
                        counts=counts,
                        project_root=project_root,
                        state_dir=state_dir,
                        record=record,
                    )
                _write_catalog_cursor(
                    conn,
                    manifest=manifest,
                    source_seq=(
                        last_record.seq if last_record is not None else start_seq
                    ),
                    record=last_record,
                    prior_meta=meta,
                )
                catalog_store._set_meta(conn, "last_lock_wait_ms", lock_wait_ms)
                catalog_store._set_meta(
                    conn,
                    "last_catch_up_duration_ms",
                    round((time.monotonic() - started_at) * 1000, 3),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    return {
        "schema_version": catalog_store.CATALOG_SCHEMA_VERSION,
        "projection_state": "ready",
        "source_seq": (
            last_record.seq if last_record is not None else start_seq
        ),
        "source_manifest_digest": manifest.digest,
        "records_projected": (
            (last_record.seq - start_seq) if last_record is not None else 0
        ),
        **counts,
        "lock_wait_ms": lock_wait_ms,
        "catch_up_duration_ms": round(
            (time.monotonic() - started_at) * 1000,
            3,
        ),
    }


def rebuild_catalog(
    state_dir: Path,
    *,
    project_root: Path,
    config: ZfConfig | None = None,
    force: bool = False,
) -> dict[str, Any]:
    started_at = time.monotonic()
    state_dir = Path(state_dir)
    manifest = build_event_manifest(state_dir)
    path = catalog_store.projection_db_path(state_dir)
    with locked_path(path):
        with catalog_store.connect_projection_db(state_dir) as conn:
            catalog_store.ensure_catalog_schema(conn)
            meta = catalog_store._meta(conn)
            if (
                not force
                and meta.get("source_manifest_digest") == manifest.digest
                and meta.get("descriptor_extractor_version")
                == catalog_store.EXTRACTOR_VERSION
            ):
                return catalog_status(state_dir, conn=conn)
            counts = _empty_insert_counts()
            last_record: EventRecord | None = None
            conn.execute("BEGIN IMMEDIATE")
            try:
                for table in (
                    "artifact_edge",
                    "artifact_occurrence",
                    "artifact_locator",
                    "artifact_object",
                ):
                    conn.execute(f"DELETE FROM {table}")
                for record in iter_event_records(state_dir, config=config):
                    last_record = record
                    _project_record(
                        conn,
                        counts=counts,
                        project_root=project_root,
                        state_dir=state_dir,
                        record=record,
                    )
                _write_catalog_cursor(
                    conn,
                    manifest=manifest,
                    source_seq=(
                        last_record.seq if last_record is not None else 0
                    ),
                    record=last_record,
                    prior_meta={},
                    full_rebuild=True,
                )
                catalog_store._set_meta(
                    conn,
                    "last_rebuild_duration_ms",
                    round((time.monotonic() - started_at) * 1000, 3),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    return {
        "schema_version": catalog_store.CATALOG_SCHEMA_VERSION,
        "projection_state": "ready",
        "source_seq": last_record.seq if last_record is not None else 0,
        "source_manifest_digest": manifest.digest,
        **counts,
        "rebuild_duration_ms": round(
            (time.monotonic() - started_at) * 1000,
            3,
        ),
    }


def recover_catalog_projection(
    state_dir: Path,
    *,
    project_root: Path,
    config: ZfConfig | None = None,
) -> dict[str, Any]:
    """Quarantine a corrupt shared projection DB and rebuild the catalog."""

    state_dir = Path(state_dir)
    path = catalog_store.projection_db_path(state_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    quarantine = path.parent / "quarantine" / stamp
    quarantined: list[str] = []
    with locked_path(path, timeout_seconds=10.0):
        quarantine.mkdir(parents=True, exist_ok=False)
        for candidate in (
            path,
            Path(str(path) + "-wal"),
            Path(str(path) + "-shm"),
        ):
            if not candidate.exists():
                continue
            destination = quarantine / candidate.name
            candidate.replace(destination)
            quarantined.append(str(destination))
    rebuilt = rebuild_catalog(
        state_dir,
        project_root=project_root,
        config=config,
        force=True,
    )
    return {
        "schema_version": "artifact-catalog-recovery.v1",
        "projection_state": rebuilt.get("projection_state", ""),
        "quarantine_dir": str(quarantine),
        "quarantined": quarantined,
        "affected_components": ["artifact-catalog", "event-index"],
        "rebuild": rebuilt,
    }


def catalog_status(
    state_dir: Path,
    *,
    conn: sqlite3.Connection | None = None,
    count_source: bool = False,
) -> dict[str, Any]:
    manifest = build_event_manifest(state_dir)
    owns_conn = conn is None
    current = conn
    try:
        current = current or catalog_store.connect_projection_db(state_dir)
        catalog_store.ensure_catalog_schema(current)
        meta = catalog_store._meta(current)
        row = current.execute(
            "SELECT COUNT(*) AS count FROM artifact_occurrence"
        ).fetchone()
        count = int(row["count"] or 0) if row else 0
    except (OSError, sqlite3.Error) as exc:
        return {
            "schema_version": catalog_store.CATALOG_SCHEMA_VERSION,
            "projection_state": "corrupt",
            "source_manifest_digest": manifest.digest,
            "projected_manifest_digest": "",
            "projected_seq": 0,
            "occurrence_count": 0,
            "db_bytes": _path_bytes(
                catalog_store.projection_db_path(state_dir)
            ),
            "wal_bytes": _path_bytes(
                Path(
                    str(catalog_store.projection_db_path(state_dir)) + "-wal"
                )
            ),
            "diagnostic": str(exc),
        }
    finally:
        if owns_conn and current is not None:
            current.close()
    projected_digest = str(meta.get("source_manifest_digest") or "")
    rebuild_reason = _catalog_rebuild_reason(
        state_dir,
        manifest=manifest,
        meta=meta,
        config=None,
    )
    if rebuild_reason:
        projection_state = "rebuild_required"
    elif projected_digest == manifest.digest:
        projection_state = "ready"
    else:
        projection_state = "stale" if projected_digest else "missing"
    projected_seq = int(meta.get("source_seq") or 0)
    source_seq = (
        count_event_records(state_dir)
        if count_source
        else (projected_seq if projection_state == "ready" else None)
    )
    projection_lag = (
        max(0, source_seq - projected_seq)
        if source_seq is not None
        else None
    )
    path = catalog_store.projection_db_path(state_dir)
    return {
        "schema_version": catalog_store.CATALOG_SCHEMA_VERSION,
        "projection_state": projection_state,
        "source_manifest_digest": manifest.digest,
        "projected_manifest_digest": projected_digest,
        "source_seq": source_seq,
        "projected_seq": projected_seq,
        "projection_lag": projection_lag,
        "occurrence_count": count,
        "cursor": {
            "segment": str(meta.get("cursor_segment") or ""),
            "byte_offset": int(meta.get("cursor_byte_offset") or 0),
            "last_event_id": str(meta.get("cursor_last_event_id") or ""),
        },
        "rebuild_reason": rebuild_reason,
        "db_bytes": _path_bytes(path),
        "wal_bytes": _path_bytes(Path(str(path) + "-wal")),
        "lock_wait_ms": _meta_float(meta, "last_lock_wait_ms"),
        "catch_up_duration_ms": _meta_float(
            meta,
            "last_catch_up_duration_ms",
        ),
        "rebuild_duration_ms": _meta_float(
            meta,
            "last_rebuild_duration_ms",
        ),
        "catalog_build_watermark": meta.get(
            "catalog_build_watermark",
            "",
        ),
        "catalog_projector_version": meta.get(
            "catalog_projector_version",
            "",
        ),
        "last_full_rebuild_at": meta.get("last_full_rebuild_at", ""),
        "updated_at": meta.get("updated_at", ""),
        "descriptor_extractor_version": meta.get(
            "descriptor_extractor_version",
            "",
        ),
    }


def _catalog_rebuild_reason(
    state_dir: Path,
    *,
    manifest: EventManifest,
    meta: Mapping[str, str],
    config: ZfConfig | None,
) -> str:
    source_seq = int(meta.get("source_seq") or 0)
    if not source_seq:
        return ""
    if (
        meta.get("catalog_schema_version")
        != catalog_store.CATALOG_SCHEMA_VERSION
    ):
        return "schema_version_changed"
    if (
        meta.get("descriptor_extractor_version")
        != catalog_store.EXTRACTOR_VERSION
    ):
        return "extractor_version_changed"
    if (
        meta.get("catalog_projector_version")
        != catalog_store.CATALOG_PROJECTOR_VERSION
    ):
        return "projector_version_changed"
    if meta.get("archive_layout_digest") != _archive_layout_digest(manifest):
        return "event_segment_layout_changed"
    segment = str(meta.get("cursor_segment") or "")
    last_event_id = str(meta.get("cursor_last_event_id") or "")
    record_offset = int(meta.get("cursor_record_offset") or -1)
    record_length = int(meta.get("cursor_record_length") or 0)
    byte_offset = int(meta.get("cursor_byte_offset") or 0)
    if not segment or not last_event_id or record_offset < 0 or record_length <= 0:
        return "event_cursor_missing"
    current_segment = next(
        (item for item in manifest.segments if item.rel_path == segment),
        None,
    )
    if current_segment is None:
        return "event_cursor_segment_missing"
    if byte_offset > current_segment.size:
        return "event_segment_truncated"
    event = hydrate_event_at(
        state_dir,
        segment=segment,
        offset=record_offset,
        length=record_length,
        config=config,
    )
    if event is None or event.id != last_event_id:
        return "event_cursor_diverged"
    return ""


def _archive_layout_digest(manifest: EventManifest) -> str:
    return catalog_store._digest([
        {
            "ordinal": segment.ordinal,
            "path": segment.rel_path,
            "kind": segment.kind,
            "size": segment.size if segment.kind != "active" else 0,
            "mtime_ns": segment.mtime_ns if segment.kind != "active" else 0,
        }
        for segment in manifest.segments
    ])


def _write_catalog_cursor(
    conn: sqlite3.Connection,
    *,
    manifest: EventManifest,
    source_seq: int,
    record: EventRecord | None,
    prior_meta: Mapping[str, str],
    full_rebuild: bool = False,
) -> None:
    if record is None:
        cursor_segment = str(prior_meta.get("cursor_segment") or "")
        cursor_byte_offset = int(
            prior_meta.get("cursor_byte_offset") or 0
        )
        cursor_last_event_id = str(
            prior_meta.get("cursor_last_event_id") or ""
        )
        cursor_record_offset = int(
            prior_meta.get("cursor_record_offset") or 0
        )
        cursor_record_length = int(
            prior_meta.get("cursor_record_length") or 0
        )
    else:
        cursor_segment = record.raw_segment
        cursor_byte_offset = record.raw_offset + record.raw_length
        cursor_last_event_id = record.event.id
        cursor_record_offset = record.raw_offset
        cursor_record_length = record.raw_length
    values = {
        "source_manifest_digest": manifest.digest,
        "source_total_bytes": manifest.total_bytes,
        "source_seq": source_seq,
        "cursor_segment": cursor_segment,
        "cursor_byte_offset": cursor_byte_offset,
        "cursor_last_event_id": cursor_last_event_id,
        "cursor_record_offset": cursor_record_offset,
        "cursor_record_length": cursor_record_length,
        "archive_layout_digest": _archive_layout_digest(manifest),
        "catalog_schema_version": catalog_store.CATALOG_SCHEMA_VERSION,
        "descriptor_extractor_version": catalog_store.EXTRACTOR_VERSION,
        "catalog_projector_version": catalog_store.CATALOG_PROJECTOR_VERSION,
        "catalog_build_watermark": catalog_store._digest({
            "schema": catalog_store.CATALOG_SCHEMA_VERSION,
            "extractor": catalog_store.EXTRACTOR_VERSION,
            "projector": catalog_store.CATALOG_PROJECTOR_VERSION,
            "source_seq": source_seq,
            "last_event_id": cursor_last_event_id,
        }),
        "updated_at": catalog_store._now(),
    }
    if full_rebuild:
        values["last_full_rebuild_at"] = catalog_store._now()
    for key, value in values.items():
        catalog_store._set_meta(conn, key, value)


def _empty_insert_counts() -> dict[str, int]:
    return {
        "objects_inserted": 0,
        "locators_inserted": 0,
        "occurrences_inserted": 0,
        "edges_inserted": 0,
        "descriptors_skipped": 0,
    }


def _project_record(
    conn: sqlite3.Connection,
    *,
    counts: dict[str, int],
    project_root: Path,
    state_dir: Path,
    record: EventRecord,
) -> None:
    payload = (
        record.event.payload
        if isinstance(record.event.payload, dict)
        else {}
    )
    for descriptor in iter_catalog_descriptors(state_dir, payload):
        from .navigation import project_descriptor_navigation

        project_descriptor_navigation(conn, state_dir, descriptor, record.seq)
        inserted = catalog_store._insert_descriptor(
            conn,
            project_root=project_root,
            state_dir=state_dir,
            event=record.event,
            payload=payload,
            descriptor=descriptor,
            source_seq=record.seq,
        )
        if not inserted:
            counts["descriptors_skipped"] += 1
            continue
        counts["objects_inserted"] += inserted["object"]
        counts["locators_inserted"] += inserted["locator"]
        counts["occurrences_inserted"] += inserted["occurrence"]
        counts["edges_inserted"] += inserted["edges"]


def _meta_float(meta: Mapping[str, str], key: str) -> float:
    try:
        return float(meta.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _path_bytes(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


__all__ = [
    "catch_up_catalog",
    "catalog_status",
    "rebuild_catalog",
    "recover_catalog_projection",
]
