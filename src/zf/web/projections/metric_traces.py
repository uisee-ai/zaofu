"""Trace list selection from indexed scalar facts, with stable cursor windows."""
from __future__ import annotations

import json
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.web.projections import metric_facts as facts
from zf.web.projections.read_model import db_path
from zf.web.projections.sqlite_reader import connect_readonly


def current_seq(state_dir: Path, config=None) -> int:
    return int(facts.refresh_events(state_dir, config).get("source_seq", 0))


def list_events(state_dir: Path, *, as_of_seq: int, before_seq: int | None, limit: int):
    """Select the requested traces before loading their bounded scalar inputs.

    Trace identity is resolved once from the complete canonical payload during
    ingestion. A nested trace id therefore has the same membership as before.
    The snapshot cutoff applies before grouping, including subsequent appends
    to a trace that already appeared on an earlier page.
    """
    with connect_readonly(db_path(state_dir)) as conn:
        keys = [row[0] for row in conn.execute(
            "SELECT trace_key FROM metric_event WHERE seq<=? AND trace_key!='' "
            "GROUP BY trace_key HAVING MAX(seq)<? ORDER BY MAX(seq) DESC LIMIT ?",
            (as_of_seq, before_seq if before_seq is not None else as_of_seq + 1, limit))]
        if not keys:
            return []
        rows = conn.execute(
            "SELECT * FROM metric_event WHERE seq<=? AND trace_key IN ("
            + ",".join("?" for _ in keys) + ") ORDER BY seq", [as_of_seq, *keys]).fetchall()
    return [(r["seq"], ZfEvent(id=r["event_id"], ts=r["ts"], type=r["type"], actor=r["actor"],
                              task_id=r["task_id"], correlation_id=r["trace_key"],
                              causation_id=r["causation_id"], payload=json.loads(r["payload"]))) for r in rows]
