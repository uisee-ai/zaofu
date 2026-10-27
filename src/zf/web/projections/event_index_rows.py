"""Prepare event index rows before acquiring SQLite's writer transaction."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from zf.core.events.segments import EventRecord


def prepare_rows(records: Iterable[EventRecord]) -> tuple[list[tuple], list[tuple], list[tuple], tuple | None]:
    from zf.web.projections import read_model as m
    events, refs, timeline = [], [], []
    last = None
    for record in records:
        event = record.event
        payload = event.payload if isinstance(event.payload, dict) else {}
        trace = m._first_nonempty(event.correlation_id, m._payload_ref(payload, "trace_id"), m._payload_ref(payload, "correlation_id"))
        feature = m._first_nonempty(m._payload_ref(payload, "feature_id"), m._feature_from_task_id(event.task_id))
        channel = m._first_nonempty(m._payload_ref(payload, "channel_id"), m._payload_ref(payload, "channel"))
        status = m._first_nonempty(m._payload_ref(payload, "status"), m._event_status(event.type))
        summary = m._event_summary(event.type, payload)
        slim = json.dumps(m._payload_slim(payload), ensure_ascii=False, default=str)
        events.append((record.seq, event.id, event.ts, event.type, event.actor, event.task_id,
                       event.correlation_id, event.causation_id, feature, trace, channel, status,
                       summary, m._payload_digest(payload), slim, record.raw_segment, record.raw_offset, record.raw_length))
        refs.extend((kind, ref, record.seq) for kind, ref in m._event_refs(payload))
        if event.task_id:
            timeline.append((event.task_id, record.seq, event.id, event.ts, event.type, event.actor,
                             status, summary, trace, slim, record.raw_segment, record.raw_offset, record.raw_length))
        last = (record.seq, record.raw_segment, record.raw_offset + record.raw_length, event.id)
    return events, refs, timeline, last


def insert_rows(conn: sqlite3.Connection, events: list[tuple], refs: list[tuple], timeline: list[tuple]) -> None:
    conn.executemany("""INSERT OR REPLACE INTO event_index (
        seq,event_id,ts,type,actor,task_id,correlation_id,causation_id,feature_id,trace_id,channel_id,
        status,summary,payload_digest,payload_slim,raw_segment,raw_offset,raw_length)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", events)
    conn.executemany("INSERT OR IGNORE INTO event_ref(ref_kind,ref_id,seq) VALUES (?,?,?)", refs)
    conn.executemany("""INSERT OR REPLACE INTO task_timeline (
        task_id,seq,event_id,ts,type,actor,status,summary,trace_id,payload_slim,raw_segment,raw_offset,raw_length)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", timeline)
