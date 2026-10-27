"""Incremental, disposable metric facts; canonical files remain authoritative.

Only scalar metric inputs and raw refs are persisted, never provider bodies.
The occurrence cursor and its derived facts commit in one transaction.
"""
from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from zf.core.events.factory import event_log_from_project
from zf.core.events.segments import build_event_manifest, iter_event_records_from_cursor
from zf.core.state.locks import locked_path
from zf.web.projections.event_index_cursor import active_identity, can_resume
from zf.web.projections.read_model import _connect, db_path
from zf.web.projections.sqlite_reader import connect_readonly

VERSION = "metric-facts.v2"
PULSE_KEYS = {"signal", "status", "to", "task_id", "role", "instance_id", "state",
              "current_task_id", "assignee", "assigned_to", "tier", "route", "rework_kind", "backend",
              "run_id", "workflow_run_id", "trace_id", "stage_id", "child_id",
              "role_instance", "route_id", "pattern_id"}


@contextmanager
def writer(state_dir: Path):
    conn = _connect(db_path(state_dir))
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS metric_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS metric_event (
            seq INTEGER PRIMARY KEY, valid INTEGER NOT NULL, event_id TEXT, ts TEXT, type TEXT, actor TEXT,
            task_id TEXT, correlation_id TEXT, causation_id TEXT, payload TEXT,
            trace_key TEXT, raw_segment TEXT, raw_offset INTEGER, raw_length INTEGER,
            run_id TEXT, attempt_id TEXT, operation_id TEXT
        );
        CREATE INDEX IF NOT EXISTS metric_event_type ON metric_event(type, seq);
        CREATE INDEX IF NOT EXISTS metric_event_trace ON metric_event(trace_key, seq);
        CREATE INDEX IF NOT EXISTS metric_event_id ON metric_event(event_id);
        CREATE INDEX IF NOT EXISTS metric_event_segment ON metric_event(raw_segment, seq);
        CREATE TABLE IF NOT EXISTS metric_trace (
            trace_key TEXT PRIMARY KEY, last_seq INTEGER, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS metric_trace_last ON metric_trace(last_seq DESC);
        CREATE TABLE IF NOT EXISTS metric_task_trace (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        """)
        yield conn
    finally:
        conn.close()


def meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM metric_meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put_meta(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO metric_meta VALUES (?,?)", (key, json.dumps(value, allow_nan=False)))


def load_meta(state_dir: Path, key: str, default=None):
    try:
        with connect_readonly(db_path(state_dir)) as conn:
            return meta(conn, key, default)
    except sqlite3.OperationalError:
        return default


def refresh_events(state_dir: Path, config=None) -> dict:
    from zf.web.projections.metric_reducer import empty_state, consume
    from zf.web.projections.trace_identity import event_trace_id
    from zf.web.projections.trace_pages import _accumulate_trace_summary
    from zf.web.projections.loop_view_source import _decoder_identity

    manifest = build_event_manifest(state_dir)
    identities = []
    for segment in manifest.segments:
        stat = segment.path.stat()
        identities.append([segment.rel_path, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    source = hashlib.sha256(json.dumps(identities).encode()).hexdigest()
    layout = hashlib.sha256(json.dumps([item for item in identities if item[0] != "events.jsonl"]).encode()).hexdigest()
    decoder = _decoder_identity(config)
    cached = load_meta(state_dir, "events", {})
    if cached.get("version") == VERSION and cached.get("decoder") == decoder and cached.get("manifest") == source:
        return cached
    with locked_path(state_dir / "projections" / "metric-events"):
        with writer(state_dir) as conn:
            previous = meta(conn, "events", {})
            identity = active_identity(state_dir)
            resume = (previous.get("version") == VERSION and previous.get("layout") == layout
                      and previous.get("decoder") == decoder and can_resume(previous, identity))
            if resume and previous.get("manifest") == source:
                return previous
            state = meta(conn, "core", empty_state()) if resume else empty_state()
            task_changes = {}
            trace_changes = {}
            rows = []
            cursor = dict(previous) if resume else {}
            log = event_log_from_project(state_dir, config=config, warn=False)
            for record in iter_event_records_from_cursor(
                state_dir, config=config,
                start_seq=int(cursor.get("source_seq", 0)),
                segment=cursor.get("tail_segment", ""), byte_offset=int(cursor.get("tail_offset", 0)),
            ):
                cursor.update(source_seq=record.seq, tail_segment=record.raw_segment,
                              tail_offset=record.raw_offset + record.raw_length)
                event = record.event
                # The segment reader emits placeholders for undecodable physical
                # lines. Collector/EventLog semantics exclude those occurrences.
                valid = not (event.type == "event.malformed" and event.actor == "segments"
                             and log.decode_line(record.raw_line) is None)
                task = None
                if valid and event.task_id:
                    if event.task_id not in task_changes:
                        old = conn.execute("SELECT payload FROM metric_task_trace WHERE task_id=?", (event.task_id,)).fetchone() if resume else None
                        task_changes[event.task_id] = json.loads(old[0]) if old else {}
                    task = task_changes[event.task_id]
                if valid:
                    consume(state, event, task)
                payload = event.payload if isinstance(event.payload, dict) else {}
                small = {key: payload[key] for key in PULSE_KEYS if key in payload}
                trace_key = event_trace_id(event)
                if trace_key:
                    if trace_key not in trace_changes and resume:
                        old_trace = conn.execute("SELECT payload FROM metric_trace WHERE trace_key=?", (trace_key,)).fetchone()
                        if old_trace:
                            trace_changes[trace_key] = json.loads(old_trace[0])
                            for field in ("task_ids", "actors", "backends"):
                                trace_changes[trace_key][field] = set(trace_changes[trace_key][field])
                    _accumulate_trace_summary(trace_changes, record.seq, event, trace_key=trace_key)
                rows.append((record.seq, int(valid), event.id, event.ts, event.type, event.actor, event.task_id,
                             event.correlation_id, event.causation_id, json.dumps(small), trace_key,
                             record.raw_segment, record.raw_offset, record.raw_length,
                             str(payload.get("workflow_run_id") or payload.get("run_id") or ""),
                             str(payload.get("attempt_id") or ""), str(payload.get("operation_id") or "")))
            cursor.update(version=VERSION, layout=layout, decoder=decoder, manifest=source,
                          byte_cursor_version=1, active_identity=json.dumps(identity) if identity else "")
            with conn:
                if not resume:
                    conn.execute("DELETE FROM metric_event")
                    conn.execute("DELETE FROM metric_task_trace")
                    conn.execute("DELETE FROM metric_trace")
                conn.executemany("INSERT INTO metric_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                conn.executemany("INSERT OR REPLACE INTO metric_task_trace VALUES (?,?)",
                                 [(key, json.dumps(value)) for key, value in task_changes.items()])
                conn.executemany("INSERT OR REPLACE INTO metric_trace VALUES (?,?,?)",
                    [(key, value["last_seq"], json.dumps(value, default=lambda item: sorted(item)))
                     for key, value in trace_changes.items()])
                put_meta(conn, "core", state)
                put_meta(conn, "events", cursor)
            return cursor


def events(state_dir: Path, *, days: int | None = None, full: bool = False,
           types: tuple[str, ...] = (), prefixes: tuple[str, ...] = (), config=None,
           include_malformed: bool = False):
    """Read bounded metadata, or hydrate only selected semantic refs in batches."""
    from zf.core.events.model import ZfEvent
    from zf.core.state.rotation import list_archives
    clauses, args = ([] if include_malformed else ["valid=1"]), []
    if days is not None:
        segments = ["events.jsonl"]
        if days > 1:
            segments += ["events/" + p.name for p in list_archives(state_dir / "events", last_days=days - 1, suffix=".jsonl")]
        clauses.append("raw_segment IN (" + ",".join("?" for _ in segments) + ")")
        args.extend(segments)
    if types or prefixes:
        filters = ["type=?" for _ in types] + ["type LIKE ?" for _ in prefixes]
        clauses.append("(" + " OR ".join(filters) + ")")
        args.extend(types); args.extend(p + "%" for p in prefixes)
    with connect_readonly(db_path(state_dir)) as conn:
        rows = conn.execute("SELECT * FROM metric_event" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY seq", args).fetchall()
    if not full:
        return [(r["seq"], ZfEvent(id=r["event_id"], ts=r["ts"], type=r["type"], actor=r["actor"],
                                  task_id=r["task_id"], correlation_id=r["correlation_id"],
                                  causation_id=r["causation_id"], payload=json.loads(r["payload"]))) for r in rows]
    return hydrate_rows(state_dir, rows, config=config)


def hydrate_rows(state_dir: Path, rows, *, config=None):
    """Hydrate selected canonical refs, validating their identities."""
    log = event_log_from_project(state_dir, config=config, warn=False)
    from contextlib import ExitStack
    out = []
    with ExitStack() as stack:
        handles = {}
        for row in rows:
            segment = row["raw_segment"]
            if segment not in handles:
                path = (state_dir / segment).resolve()
                if not path.is_relative_to(state_dir.resolve()):
                    raise ValueError("metric event reference escapes state directory")
                handles[segment] = stack.enter_context(path.open("rb"))
            handle = handles[segment]
            handle.seek(row["raw_offset"])
            event = log.decode_line(handle.read(row["raw_length"]).decode("utf-8", "replace"))
            if event is None or event.id != row["event_id"] or event.type != row["type"] or event.ts != row["ts"]:
                raise ValueError("metric event reference changed during hydration")
            out.append((row["seq"], event))
    return out
