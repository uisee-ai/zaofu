"""Incremental admitted cost receipts and role/backend/hour rollups.

Accounting admission/deduplication/pricing stays in CostTracker. Reprocessing
the same file cursor never charges it twice; corrections replace projections.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat as statmod
from dataclasses import asdict
from pathlib import Path

from zf.core.cost.tracker import CostTracker, CostSummary, _entry_datetime
from zf.core.state.locks import locked_path
from zf.web.projections.metric_facts import writer, meta, put_meta, load_meta
from zf.web.projections.read_model import db_path
from zf.web.projections.sqlite_reader import connect_readonly


def _sources(state_dir: Path) -> tuple[list[Path], dict[str, list[int]], dict[str, int]]:
    try:
        entries = os.scandir(state_dir / "cost")
    except FileNotFoundError:
        try:
            (state_dir / "cost").lstat()
        except FileNotFoundError:
            paths = []
        else:
            raise  # A dangling declared cost archive directory is not absent.
    else:
        with entries:
            paths = sorted(Path(entry.path) for entry in entries
                           if entry.name.endswith(".jsonl") and statmod.S_ISREG(entry.stat().st_mode))
    active = state_dir / "cost.jsonl"
    try:
        active.stat()
    except FileNotFoundError:
        try:
            active.lstat()
        except FileNotFoundError:
            pass
        else:
            raise  # An existing dangling cost file cannot be read.
    else:
        paths.append(active)
    source, ctimes = {}, {}
    for path in paths:
        rel = path.relative_to(state_dir).as_posix()
        stat = path.stat()
        # Keep the four-element source format readable by older dashboards.
        source[rel] = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
        ctimes[rel] = stat.st_ctime_ns
    return paths, source, ctimes


def _prefix_sha256(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    remaining = size
    with path.open("rb") as handle:
        while remaining:
            block = handle.read(min(1024 * 1024, remaining))
            if not block:
                raise OSError("cost source truncated during projection")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def _guard_matches(previous: dict, source: dict, ctimes: dict) -> bool:
    guard = previous.get("source_guard")
    return (isinstance(guard, dict) and guard.get("source") == source
            and previous.get("source") == source and guard.get("ctimes") == ctimes
            and guard.get("offsets") == previous.get("offsets")
            and guard.get("ordinal") == previous.get("ordinal")
            and ("cost.jsonl" not in source or isinstance(guard.get("active_sha256"), str)))


def _assert_sources_unchanged(state_dir: Path, source: dict, ctimes: dict) -> None:
    _, after, after_ctimes = _sources(state_dir)
    if after != source or after_ctimes != ctimes:
        raise OSError("cost source changed during projection")


def _cost_record(raw: bytes, *, allow_partial_tail: bool) -> dict | None:
    if not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        if allow_partial_tail and not raw.endswith(b"\n"):
            return None  # A partial active tail waits for the next append.
        raise OSError("invalid complete cost record") from exc
    if not isinstance(value, dict):
        raise OSError("cost record must be an object")
    return value


def validate_cost_files(paths: list[Path], *, active: Path) -> None:
    """Check fallback input before the legacy tracker can skip corrupt lines."""
    for path in paths:
        with path.open("rb") as handle:
            for raw in handle:
                _cost_record(raw, allow_partial_tail=path == active)


def _append_keeps_record_boundary(path: Path, previous_size: int, consumed: int) -> bool:
    if not previous_size or consumed < previous_size:
        return True
    with path.open("rb") as handle:
        handle.seek(previous_size - 1)
        old_last_and_new_first = handle.read(2)
    if len(old_last_and_new_first) != 2:
        raise OSError("cost source changed during boundary verification")
    return old_last_and_new_first[0:1] == b"\n" or old_last_and_new_first[1:2] == b"\n"


def refresh(state_dir: Path) -> None:
    active = state_dir / "cost.jsonl"
    paths, source, ctimes = _sources(state_dir)
    event_cursor = load_meta(state_dir, "events", {})
    previous = load_meta(state_dir, "costs", {})
    if _guard_matches(previous, source, ctimes) and previous.get("event_manifest") == event_cursor.get("manifest"): return
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
        paths, source, ctimes = _sources(state_dir)
        event_cursor = meta(conn, "events", {})
        previous = meta(conn, "costs", {})
        if _guard_matches(previous, source, ctimes):
            with conn:
                _bind_receipts(conn, previous, event_cursor)
                _assert_sources_unchanged(state_dir, source, ctimes)
                put_meta(conn, "costs", previous)
            return
        old = previous.get("source", {})
        if not isinstance(old, dict):
            old = {}
        guard = previous.get("source_guard")
        old_ctimes = guard.get("ctimes") if isinstance(guard, dict) else None
        previous_offsets = previous.get("offsets")
        resume = (bool(previous) and isinstance(guard, dict) and guard.get("source") == old
                  and isinstance(old_ctimes, dict) and isinstance(previous_offsets, dict)
                  and guard.get("offsets") == previous_offsets and guard.get("ordinal") == previous.get("ordinal")
                  and type(previous.get("ordinal")) is int and previous["ordinal"] >= 0
                  and old.keys() == source.keys())
        for path, identity in source.items():
            prior = old.get(path)
            if (not isinstance(prior, list) or len(prior) != 4
                    or not all(type(item) is int for item in prior)):
                resume = False
                continue
            old_offset = previous_offsets.get(path, 0) if isinstance(previous_offsets, dict) else None
            if type(old_offset) is not int or not 0 <= old_offset <= prior[2]:
                resume = False
                continue
            if old.get(path) == identity:
                if not isinstance(old_ctimes, dict) or old_ctimes.get(path) != ctimes[path]:
                    resume = False
                continue
            if path != "cost.jsonl" or identity[:2] != prior[:2] or identity[2] <= prior[2]:
                resume = False
            elif (not isinstance(guard, dict) or not isinstance(guard.get("active_sha256"), str)
                  or _prefix_sha256(active, prior[2]) != guard["active_sha256"]):
                # Same inode/greater size cannot prove the old prefix was not
                # rewritten. Only a matching full-prefix digest permits fold.
                resume = False
            elif not _append_keeps_record_boundary(
                active, prior[2], old_offset,
            ):
                # A complete no-newline JSON record was previously admitted;
                # appending without a separator changes that same record.
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
                    entry = _cost_record(raw, allow_partial_tail=segment == "cost.jsonl")
                    if entry is None:
                        if raw.endswith(b"\n"):
                            offsets[segment] = handle.tell()
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
        active_sha256 = _prefix_sha256(active, source["cost.jsonl"][2]) if "cost.jsonl" in source else None
        _assert_sources_unchanged(state_dir, source, ctimes)
        with conn:
            if not resume:
                conn.execute("DELETE FROM metric_usage")
                conn.execute("DELETE FROM metric_usage_rollup")
            conn.executemany("INSERT INTO metric_usage VALUES (?,?,?,?,?,?,?,?,?)", new_rows)
            conn.executemany("INSERT OR REPLACE INTO metric_usage_rollup VALUES (?,?,?,?)",
                             [(kind, scope, groups[kind, scope]["ordinal"], json.dumps(groups[kind, scope], allow_nan=False)) for kind, scope in dirty])
            updated = {**(previous if resume else {}), "source": source, "offsets": offsets, "ordinal": ordinal,
                       "source_guard": {"source": source, "ctimes": ctimes, "active_sha256": active_sha256,
                                        "offsets": dict(offsets), "ordinal": ordinal}}
            _bind_receipts(conn, updated, event_cursor)
            put_meta(conn, "costs", updated)
            _assert_sources_unchanged(state_dir, source, ctimes)


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
