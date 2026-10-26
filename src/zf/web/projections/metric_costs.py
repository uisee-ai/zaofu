"""Incremental admitted cost receipts and role/backend/hour rollups.

Accounting admission/deduplication/pricing stays in CostTracker. Reprocessing
the same file cursor never charges it twice; corrections replace projections.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from zf.core.cost.tracker import CostTracker, CostSummary, _entry_datetime
from zf.core.state.rotation import list_archives
from zf.core.state.locks import locked_path
from zf.web.projections.metric_facts import writer, meta, put_meta, load_meta
from zf.web.projections.read_model import db_path
from zf.web.projections.sqlite_reader import connect_readonly


def _identity(path: Path) -> list[int]:
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns]


def refresh(state_dir: Path) -> None:
    paths = list_archives(state_dir / "cost", suffix=".jsonl")
    active = state_dir / "cost.jsonl"
    if active.exists(): paths.append(active)
    source = {p.relative_to(state_dir).as_posix(): _identity(p) for p in paths}
    event_cursor = load_meta(state_dir, "events", {})
    previous = load_meta(state_dir, "costs", {})
    if previous.get("source") == source and previous.get("event_manifest") == event_cursor.get("manifest"): return
    with locked_path(state_dir / "projections" / "metric-costs"), writer(state_dir) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS metric_usage (ordinal INTEGER PRIMARY KEY, segment TEXT,
            byte_offset INTEGER, receipt_id TEXT, run_id TEXT, attempt_id TEXT,
            operation_id TEXT, bucket TEXT, payload TEXT);
        CREATE INDEX IF NOT EXISTS metric_usage_receipt ON metric_usage(receipt_id);
        CREATE INDEX IF NOT EXISTS metric_usage_bucket ON metric_usage(bucket, ordinal);
        CREATE TABLE IF NOT EXISTS metric_usage_rollup (kind TEXT, scope TEXT, ordinal INTEGER,
            payload TEXT, PRIMARY KEY(kind,scope));
        """)
        previous = meta(conn, "costs", {})
        if previous.get("source") == source:
            with conn:
                _bind_receipts(conn, previous, event_cursor)
                put_meta(conn, "costs", previous)
            return
        old = previous.get("source", {})
        resume = bool(previous) and old.keys() == source.keys()
        for path, identity in source.items():
            if old.get(path) == identity: continue
            prior = old.get(path, [])
            if path != "cost.jsonl" or not prior or identity[:2] != prior[:2] or identity[2] <= prior[2]:
                resume = False
        groups = {}
        ordinal = int(previous.get("ordinal", 0)) if resume else 0
        offsets = dict(previous.get("offsets", {})) if resume else {}
        new_rows, dirty = [], set()
        for path in paths:
            segment = path.relative_to(state_dir).as_posix()
            if resume and source[segment] == old[segment]: continue
            start = offsets.get(segment, 0)
            with path.open("rb") as handle:
                handle.seek(start)
                for raw in handle:
                    offset = handle.tell() - len(raw)
                    # A complete JSON tail without newline has always been
                    # admitted by CostTracker; partial JSON waits for append.
                    try: entry = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        if raw.endswith(b"\n"): offsets[segment] = handle.tell()
                        continue
                    offsets[segment] = handle.tell()
                    if entry.get("accounting_baseline") is True: continue
                    ordinal += 1
                    receipt = str(entry.get("source_event_id") or entry.get("usage_sample_id") or "")
                    mapped = conn.execute("SELECT run_id,attempt_id,operation_id FROM metric_event WHERE event_id=? ORDER BY seq LIMIT 1", (receipt,)).fetchone()
                    dims = [str(entry.get(k) or (mapped[i] if mapped else "")) for i, k in enumerate(("run_id", "attempt_id", "operation_id"))]
                    occurred = _entry_datetime(entry)
                    hour = occurred.replace(minute=0, second=0, microsecond=0).isoformat() if occurred else "unknown"
                    bucket = json.dumps([hour, entry.get("role"), entry.get("instance_id") or entry.get("role"),
                                         entry.get("backend") or "unknown", entry.get("provider") or "unknown",
                                         entry.get("model") or "unknown", *dims])
                    new_rows.append((ordinal, segment, offset, receipt, *dims, bucket, json.dumps(entry)))
                    role, backend = str(entry["role"]), str(entry.get("backend") or "unknown")
                    for key in (("role", role), ("backend", backend), ("hour", bucket), ("all", "")):
                        if key not in groups:
                            stored = conn.execute("SELECT payload FROM metric_usage_rollup WHERE kind=? AND scope=?", key).fetchone() if resume else None
                            groups[key] = json.loads(stored[0]) if stored else {
                                "summary": asdict(CostSummary(role=key[1])), "statuses": {}, "catalogs": {}, "ordinal": ordinal}
                        value = groups[key]
                        summary = CostSummary(**value["summary"])
                        CostTracker._accumulate_summary(summary, entry)
                        value["summary"] = asdict(summary)
                        status = str(entry.get("cost_status") or "legacy")
                        value["statuses"][status] = value["statuses"].get(status, 0) + 1
                        digest = str(entry.get("pricing_catalog_digest") or entry.get("catalog_digest") or "")
                        if digest: value["catalogs"][digest] = {"catalog_version": str(entry.get("catalog_version") or ""), "digest": digest}
                        dirty.add(key)
        with conn:
            if not resume:
                conn.execute("DELETE FROM metric_usage")
                conn.execute("DELETE FROM metric_usage_rollup")
            conn.executemany("INSERT INTO metric_usage VALUES (?,?,?,?,?,?,?,?,?)", new_rows)
            conn.executemany("INSERT OR REPLACE INTO metric_usage_rollup VALUES (?,?,?,?)",
                             [(kind, scope, groups[kind, scope]["ordinal"], json.dumps(groups[kind, scope], allow_nan=False)) for kind, scope in dirty])
            updated = {**(previous if resume else {}), "source": source, "offsets": offsets, "ordinal": ordinal}
            _bind_receipts(conn, updated, event_cursor)
            put_meta(conn, "costs", updated)


def _bind_receipts(conn, metadata: dict, event_cursor: dict) -> None:
    """Backfill late event attribution and rebuild only affected hour buckets.

    Receipt admission may precede the event projection. Unknown attribution
    remains unknown until the matching canonical event exists; it is never
    assigned to whichever workflow happens to be active.
    """
    if metadata.get("event_manifest") == event_cursor.get("manifest"):
        return
    old_seq = int(metadata.get("event_seq", 0))
    seq = int(event_cursor.get("source_seq", 0))
    # A replacement with the same/smaller cursor may correct old attribution.
    after = old_seq if seq > old_seq else 0
    dirty = set()
    rows = conn.execute("""SELECT u.*, e.run_id AS event_run, e.attempt_id AS event_attempt,
        e.operation_id AS event_operation FROM metric_usage u JOIN metric_event e ON e.event_id=u.receipt_id
        WHERE u.receipt_id!='' AND e.seq>? ORDER BY e.seq""", (after,)).fetchall()
    for row in rows:
        entry = json.loads(row["payload"])
        dims = [str(entry.get(key) or row[event_key] or "") for key, event_key in
                (("run_id", "event_run"), ("attempt_id", "event_attempt"), ("operation_id", "event_operation"))]
        if dims == [row["run_id"], row["attempt_id"], row["operation_id"]]:
            continue
        bucket = json.loads(row["bucket"])
        bucket[-3:] = dims
        new_bucket = json.dumps(bucket)
        dirty.update((row["bucket"], new_bucket))
        conn.execute("UPDATE metric_usage SET run_id=?,attempt_id=?,operation_id=?,bucket=? WHERE ordinal=?",
                     [*dims, new_bucket, row["ordinal"]])
    for bucket in dirty:
        receipts = conn.execute("SELECT ordinal,payload FROM metric_usage WHERE bucket=? ORDER BY ordinal", (bucket,)).fetchall()
        conn.execute("DELETE FROM metric_usage_rollup WHERE kind='hour' AND scope=?", (bucket,))
        if not receipts:
            continue
        summary = CostSummary(role=bucket)
        value = {"statuses": {}, "catalogs": {}, "ordinal": receipts[0][0]}
        for _, raw in receipts:
            entry = json.loads(raw)
            CostTracker._accumulate_summary(summary, entry)
            status = str(entry.get("cost_status") or "legacy")
            value["statuses"][status] = value["statuses"].get(status, 0) + 1
            digest = str(entry.get("pricing_catalog_digest") or entry.get("catalog_digest") or "")
            if digest:
                value["catalogs"][digest] = {"catalog_version": str(entry.get("catalog_version") or ""), "digest": digest}
        value["summary"] = asdict(summary)
        conn.execute("INSERT INTO metric_usage_rollup VALUES ('hour',?,?,?)",
                     (bucket, value["ordinal"], json.dumps(value)))
    metadata.update(event_seq=seq, event_manifest=event_cursor.get("manifest"))


class ProjectedCostTracker(CostTracker):
    def _groups(self, kind: str) -> list:
        with connect_readonly(db_path(self.cost_path.parent)) as conn:
            return [(row[0], json.loads(row[1])) for row in conn.execute(
                "SELECT scope,payload FROM metric_usage_rollup WHERE kind=? ORDER BY ordinal", (kind,))]

    def per_role_totals(self, *, last_days=None):
        if last_days is not None: return super().per_role_totals(last_days=last_days)
        return {key: CostSummary(**value["summary"]) for key, value in self._groups("role")}

    def summary_by_backend(self, *, last_days=None):
        if last_days is not None: return super().summary_by_backend(last_days=last_days)
        return {key: CostSummary(**value["summary"]) for key, value in self._groups("backend")}

    def precision_summary(self, *, last_days=None):
        if last_days is not None: return super().precision_summary(last_days=last_days)
        groups = self._groups("all")
        value = groups[0][1] if groups else {"summary": asdict(CostSummary(role="")), "statuses": {}, "catalogs": {}}
        summary = CostSummary(**value["summary"])
        return {"schema_version": "cost-precision-summary.v1", "display_total_usd": round(summary.total_usd, 12),
                "estimated_usd": round(summary.estimated_usd, 12), "provider_reported_usd": round(summary.provider_reported_usd, 12),
                "billed_usd": round(summary.billed_usd, 12), "unpriced_entries": summary.unpriced_entries,
                "partial_entries": summary.partial_entries, "statuses": dict(sorted(value["statuses"].items())),
                "catalogs": list(value["catalogs"].values())}
