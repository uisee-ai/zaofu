"""Projections layer: events (moved verbatim from web/server.py)."""
from __future__ import annotations

import itertools
import os

from fastapi import Request
from pathlib import Path
from typing import Any
from typing import AsyncIterator
from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.segments import iter_event_records
from zf.core.security.redaction import redact_event
from zf.core.security.redaction import redact_obj
from zf.core.task.store import TaskStore
from zf.core.trace.diagnostics import _safe_trace_id
from zf.runtime.execution_route import project_execution_route
import asyncio
import json
import threading
from zf.web.projections.common import _first_nonempty, _is_blocked_event, _is_failed_event, _line_count, _matches_event_filters, _matches_task_filters, _parse_search_query, _payload_first_string, _payload_mentions, _payload_ref, _raw_event_has_task_id, _read_events_with_seq
from zf.web.projections.request_util import _sse_event, _sse_gap
from zf.web.projections.summaries import _archive_tasks, _refs_from_events


_EVENT_LOG_RUN_ID = "event-log-latest"


def _trace_detail(
    state_dir: Path,
    trace_id: str,
    config: ZfConfig | None = None,
) -> dict:
    fallback_task_id = ""
    if trace_id.startswith("task:"):
        fallback_task_id = trace_id.split(":", 1)[1]
    events = [
        (seq, event)
        for seq, event in _events_with_seq(state_dir, config=config)
        if getattr(event, "correlation_id", None) == trace_id
        or getattr(event, "id", None) == trace_id
        or _payload_mentions(getattr(event, "payload", {}), trace_id)
        or (
            fallback_task_id
            and getattr(event, "task_id", None) == fallback_task_id
        )
    ]
    execution_route = project_execution_route(events, trace_id=trace_id)
    return {
        "trace_id": trace_id,
        "event_count": len(events),
        "timeline": [_event_to_dict(seq, event) for seq, event in events],
        "tasks": sorted({
            event.task_id for _, event in events
            if getattr(event, "task_id", None)
        }),
        "actors": sorted({
            event.actor for _, event in events
            if getattr(event, "actor", None)
        }),
        "git_refs": _refs_from_events(events, state_dir=state_dir, config=config),
        "diagnostics": _diagnostics(state_dir, trace_id),
        "execution_route": redact_obj(execution_route),
        "empty": not events,
    }


def _fleet_stats_projection(state_dir: Path, *, config: ZfConfig | None = None) -> dict:
    from zf.runtime.fleet_efficiency import (
        build_role_efficiency,
        build_task_flow_stats,
    )

    events = _events_with_seq(state_dir, config=config)
    kanban_path = state_dir / "kanban.json"
    tasks = (
        {task.id: task for task in TaskStore(kanban_path).list_all()}
        if kanban_path.exists() else {}
    )
    return {
        "task_flow": build_task_flow_stats(events, tasks),
        "role_efficiency": build_role_efficiency(events, tasks),
    }


def _event_log_run_summary(
    state_dir: Path,
    config: ZfConfig | None = None,
) -> dict[str, Any] | None:
    events = _events_with_seq(state_dir, config=config)
    task_events = [
        (seq, event) for seq, event in events
        if getattr(event, "task_id", None)
    ]
    if not task_events:
        return None
    task_ids = sorted({
        str(getattr(event, "task_id", "") or "")
        for _, event in task_events
        if getattr(event, "task_id", None)
    })
    archived_tasks = _archive_tasks(state_dir, include_active=True)
    terminal_tasks = [
        task for task in archived_tasks
        if str(task.get("id") or "") in set(task_ids)
        and str(task.get("status") or "") in {"done", "cancelled"}
    ]
    failed_events = [
        event for _, event in task_events
        if str(getattr(event, "type", "")).endswith((".failed", ".rejected"))
    ]
    first_event = task_events[0][1]
    last_event = events[-1][1] if events else task_events[-1][1]
    return {
        "run_id": _EVENT_LOG_RUN_ID,
        "trace_id": str(getattr(first_event, "correlation_id", "") or ""),
        "test_task_id": task_ids[0] if len(task_ids) == 1 else "",
        "scenario_id": "event-log",
        "target_project_id": "",
        "target_config": "",
        "attempt": 1,
        "status": "projected",
        "health": "ok",
        "live_state_dir": str(state_dir),
        "artifact_dir": "",
        "started_at": str(getattr(first_event, "ts", "") or ""),
        "heartbeat_at": "",
        "ended_at": str(getattr(last_event, "ts", "") or ""),
        "archived_at": "",
        "source": "events.jsonl",
        "projection": "event_log_fallback",
        "summary": {
            "event_count": len(events),
            "task_event_count": len(task_events),
            "task_count": len(task_ids),
            "done_tasks": len([task for task in terminal_tasks if task.get("status") == "done"]),
            "cancelled_tasks": len([task for task in terminal_tasks if task.get("status") == "cancelled"]),
            "failed_event_count": len(failed_events),
            "task_ids": task_ids[:50],
        },
    }


def _traces(state_dir: Path, config: ZfConfig | None = None) -> list[dict]:
    grouped: dict[str, dict] = {}
    # Full replay (NOT the slim index): grouping calls _payload_ref(payload,
    # "trace_id") which deep-extracts a trace_id that can live in a nested
    # payload field. The slim payload truncates nested fields (_shallow_dict),
    # so ~0.2% of events would regroup and one trace's event_count drifts
    # (caught by the old-vs-new metric parity diff). _traces is only ~0.8s even
    # on a 43MB log, and the trace PAGE speedup comes from the scoped /traces
    # endpoint skipping the full snapshot — not from slimming this projection.
    for seq, event in _events_with_seq(state_dir, config=config):
        trace_id = getattr(event, "correlation_id", None)
        if not trace_id:
            payload_trace = _payload_ref(getattr(event, "payload", {}), "trace_id")
            trace_id = str(payload_trace) if payload_trace else None
        if not trace_id:
            task_id = str(getattr(event, "task_id", "") or "").strip()
            if not task_id:
                continue
            trace_id = f"task:{task_id}"
        item = grouped.setdefault(str(trace_id), {
            "trace_id": str(trace_id),
            "first_seq": seq,
            "last_seq": seq,
            "first_ts": getattr(event, "ts", ""),
            "last_ts": getattr(event, "ts", ""),
            "event_count": 0,
            "task_ids": set(),
            "actors": set(),
            "last_type": "",
            "source": "event_trace"
            if not str(trace_id).startswith("task:")
            else "task_event_fallback",
        })
        item["event_count"] += 1
        item["last_seq"] = seq
        item["last_ts"] = getattr(event, "ts", "")
        item["last_type"] = getattr(event, "type", "")
        if getattr(event, "task_id", None):
            item["task_ids"].add(event.task_id)
        if getattr(event, "actor", None):
            item["actors"].add(event.actor)
    out = []
    for item in grouped.values():
        out.append({
            **item,
            "task_ids": sorted(item["task_ids"]),
            "actors": sorted(item["actors"]),
        })
    out.sort(key=lambda x: x["last_seq"], reverse=True)
    return out[:80]


def _event_signal_summary(event_type: str, payload: dict) -> str:
    for key in ("summary", "message", "reason", "text", "status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:240]
    if event_type == "worker.state.changed":
        from_state = payload.get("from")
        to_state = payload.get("to")
        if to_state:
            return f"{from_state or 'unknown'} -> {to_state}"[:240]
    stop_reason = _payload_first_string(payload, ["provider_stop_reason", "stop_reason"])
    if stop_reason:
        return stop_reason[:240]
    return ""


def _events_page(
    state_dir: Path,
    *,
    limit: int = 100,
    cursor: int | None = None,
    task_id: str | None = None,
    actor: str | None = None,
    event_type: str | None = None,
    event_prefix: str | None = None,
    failed: bool = False,
    blocked: bool = False,
    config: ZfConfig | None = None,
) -> dict:
    limit = max(1, min(limit, 500))
    try:
        from zf.web.projections import read_model

        projected = read_model.events_page(
            state_dir,
            limit=limit,
            cursor=cursor,
            task_id=task_id,
            actor=actor,
            event_type=event_type,
            event_prefix=event_prefix,
            failed=failed,
            blocked=blocked,
            config=config,
        )
        if projected is not None:
            return projected
    except Exception:
        pass
    events = _events_with_seq(state_dir, config=config)
    current_seq = events[-1][0] if events else 0

    filtered = []
    for seq, event in events:
        if cursor is not None and seq <= cursor:
            continue
        if task_id and getattr(event, "task_id", None) != task_id:
            continue
        if actor and getattr(event, "actor", None) != actor:
            continue
        if event_type and getattr(event, "type", None) != event_type:
            continue
        if event_prefix and not str(getattr(event, "type", "")).startswith(event_prefix):
            continue
        if failed and not _is_failed_event(event):
            continue
        if blocked and not _is_blocked_event(event):
            continue
        filtered.append((seq, event))

    page = filtered[:limit] if cursor is not None else filtered[-limit:]
    return {
        "items": [_event_to_dict(seq, event) for seq, event in page],
        "next_cursor": page[-1][0] if page else cursor,
        "current_seq": current_seq,
        "limit": limit,
    }


def _diagnostics(state_dir: Path, trace_id: str | None) -> dict:
    if not trace_id:
        return {"trace_id": "", "items": [], "empty": True}
    root = state_dir / "diagnostics" / _safe_trace_id(trace_id)
    items = []
    if root.exists():
        for path in sorted(root.glob("*.jsonl")):
            stream = path.stem
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    payload = {"malformed": True, "raw": line}
                items.append({
                    "stream": stream,
                    "index": index,
                    "payload": redact_obj(payload),
                })
    return {
        "trace_id": trace_id,
        "path": str(root),
        "items": items,
        "empty": not items,
    }


def _search(
    state_dir: Path,
    *,
    q: str,
    limit: int = 50,
    config: ZfConfig | None = None,
) -> dict:
    from zf.web.projections.tasks import _kanban  # deferred: import cycle
    limit = max(1, min(limit, 200))
    filters, terms = _parse_search_query(q)
    task_results = []
    for task in _kanban(state_dir, config=config):
        if not _matches_task_filters(task, filters):
            continue
        haystack = json.dumps(task, ensure_ascii=False, default=str).lower()
        if terms and not all(term.lower() in haystack for term in terms):
            continue
        task_results.append(task)
        if len(task_results) >= limit:
            break

    event_results = []
    for seq, event in _events_with_seq(state_dir, config=config):
        if not _matches_event_filters(event, filters):
            continue
        haystack = json.dumps(_event_to_dict(seq, event), ensure_ascii=False, default=str).lower()
        if terms and not all(term.lower() in haystack for term in terms):
            continue
        event_results.append(_event_to_dict(seq, event))
    event_results = event_results[-limit:]

    return {
        "query": q,
        "filters": filters,
        "terms": terms,
        "tasks": task_results,
        "events": event_results,
        "traces": _traces(state_dir, config=config)[:limit],
    }


# _events_with_seq is called ~20x per snapshot (each projection re-decodes the
# whole log). The log is append-only, so memoize by (size, mtime_ns): any append
# changes the fingerprint and misses. Same replay, decoded once — byte-identical.
_EVENTS_WITH_SEQ_CACHE: dict[str, dict] = {}  # {fp, epoch, events, archives, consumed}
_EVENTS_WITH_SEQ_CACHE_LOCK = threading.Lock()
_EVENTS_WITH_SEQ_CACHE_MAX = 6

# Heavy per-event derivations (lowered payload dumps; task-event leaf index)
# are pure functions of the memoized events list but were rebuilt per request —
# on a 30k-event log that is seconds of GIL-bound work per page load. Memoize
# by the same (size, mtime_ns) fingerprint; smaller cap (memory-heavy).
_DERIVED_CACHE: dict[str, tuple[tuple[int, int], object]] = {}
_DERIVED_CACHE_LOCK = threading.Lock()
# One snapshot touches ~5 derived kinds per project (read_days:1/:14,
# search_texts, task_index, ...); 6 slots thrashed across kinds and forced
# a full re-decode every snapshot. Size for ~6 projects x ~5 kinds.
_DERIVED_CACHE_MAX = 32

# R6-5: byte-budget eviction. Slot counts don't bound memory — a decoded
# 40MB log holds ~580-700MB RSS (measured 2026-07-02 on r2/r10, ≈14x raw
# bytes; event lists + payload dicts). Each entry carries an estimated cost;
# oldest entries are evicted across BOTH caches until under budget.
_CACHE_BUDGET_BYTES = int(
    os.environ.get("ZF_WEB_EVENT_CACHE_BUDGET_MB", "2048")
) * 1024 * 1024
_DECODED_COST_FACTOR = 14   # events / read_days entries: own decoded objects
_DERIVED_COST_FACTOR = 2    # index/texts entries: share event objects
_CACHE_STAMP = itertools.count()


def _log_bytes_basis(state_dir: Path, fingerprint: tuple[int, int]) -> int:
    """Raw bytes backing a cache entry: active log + archive segments."""
    entry = _EVENTS_WITH_SEQ_CACHE.get(str(state_dir))
    archives = entry["archives"] if entry is not None else _archive_inventory(state_dir)
    return fingerprint[0] + sum(size for _rel, size in archives)


def _enforce_cache_budget() -> None:
    """Evict oldest entries (either cache) until estimated cost fits budget.

    Called after inserts, outside the insert locks (locks are not reentrant);
    acquires both locks in a fixed order to stay deadlock-free. Always leaves
    the newest entry per cache."""
    with _EVENTS_WITH_SEQ_CACHE_LOCK:
        with _DERIVED_CACHE_LOCK:
            while True:
                total = sum(e["cost"] for e in _EVENTS_WITH_SEQ_CACHE.values()) + sum(
                    e["cost"] for e in _DERIVED_CACHE.values()
                )
                if total <= _CACHE_BUDGET_BYTES:
                    return
                candidates = []
                if len(_EVENTS_WITH_SEQ_CACHE) > 1:
                    key = next(iter(_EVENTS_WITH_SEQ_CACHE))
                    candidates.append((_EVENTS_WITH_SEQ_CACHE[key]["stamp"], _EVENTS_WITH_SEQ_CACHE, key))
                if len(_DERIVED_CACHE) > 1:
                    key = next(iter(_DERIVED_CACHE))
                    candidates.append((_DERIVED_CACHE[key]["stamp"], _DERIVED_CACHE, key))
                if not candidates:
                    return
                _stamp, cache, key = min(candidates)
                cache.pop(key)


def _derived_cached(state_dir, kind: str, config, build, fold=None):
    """Entries are (fingerprint, epoch, n_events, value).

    ``fold(old_value, tail_rows)`` — optional incremental path (R6-1): when the
    events cache folded (same epoch → shared prefix, same objects), the derived
    view extends its cached value with just the new (seq, event) rows instead
    of rebuilding from the whole log. fold must return a NEW value (cached one
    may be concurrently read).
    """
    key = f"{state_dir}::{kind}"
    fingerprint = _event_log_fingerprint(state_dir)
    cached = _DERIVED_CACHE.get(key)
    if cached is not None and cached["fp"] == fingerprint:
        return cached["value"]
    events, epoch, fp = _events_state(state_dir, config=config)
    if (
        fold is not None
        and cached is not None
        and cached["epoch"] == epoch
        and len(events) >= cached["n"]
    ):
        value = fold(cached["value"], events[cached["n"]:])
    else:
        value = build()
    basis = _log_bytes_basis(state_dir, fp)
    factor = _DECODED_COST_FACTOR if kind.startswith("read_days:") else _DERIVED_COST_FACTOR
    with _DERIVED_CACHE_LOCK:
        _DERIVED_CACHE[key] = {
            "fp": fp,
            "epoch": epoch,
            "n": len(events),
            "value": value,
            "cost": basis * factor,
            "stamp": next(_CACHE_STAMP),
        }
        while len(_DERIVED_CACHE) > _DERIVED_CACHE_MAX:
            _DERIVED_CACHE.pop(next(iter(_DERIVED_CACHE)))
    _enforce_cache_budget()
    return value


def event_ref_kv(state_dir: Path, config: ZfConfig | None = None) -> dict:
    """Per-event collected ref keys (seq -> (event, refs)), fingerprint-memoized.

    _refs_from_events re-walked every payload with a 26-key collect for every
    caller (kanban per-task, task detail, delivery fallbacks) — the same events
    each time. Entries keep the event object so consumers can identity-check
    (`hit[0] is event`) and fall back to a direct collect on mismatch.
    """
    from zf.web.projections.common import _REF_EVENT_KEYS, _payload_collect

    def _collect(event) -> dict:
        payload = getattr(event, "payload", {}) or {}
        return {
            key: value
            for key, value in _payload_collect(payload, _REF_EVENT_KEYS).items()
            if value not in (None, "")
        }

    def build() -> dict:
        return {
            seq: (event, _collect(event))
            for seq, event in _events_with_seq(state_dir, config=config)
        }

    def fold(old: dict, tail) -> dict:
        out = dict(old)
        for seq, event in tail:
            out[seq] = (event, _collect(event))
        return out

    return _derived_cached(state_dir, "event_ref_kv", config, build, fold=fold)


def event_topology_kv(state_dir: Path, config: ZfConfig | None = None) -> dict:
    """Per-event topology refs (seq -> (event, refs)), fingerprint-memoized.

    _fanouts/_candidates each re-ran a full-log _payload_collect per request
    (700k+ recursive calls each on r2). Same fold/identity-check contract as
    event_ref_kv; consumers fall back to a direct collect on object mismatch.
    Empty values are dropped — all consumers use `or ""`/_first_nonempty."""
    from zf.web.projections.common import _TOPOLOGY_EVENT_KEYS, _payload_collect

    def _collect(event) -> dict:
        payload = getattr(event, "payload", {}) or {}
        return {
            key: value
            for key, value in _payload_collect(payload, _TOPOLOGY_EVENT_KEYS).items()
            if value not in (None, "")
        }

    def build() -> dict:
        return {
            seq: (event, _collect(event))
            for seq, event in _events_with_seq(state_dir, config=config)
        }

    def fold(old: dict, tail) -> dict:
        out = dict(old)
        for seq, event in tail:
            out[seq] = (event, _collect(event))
        return out

    return _derived_cached(state_dir, "event_topology_kv", config, build, fold=fold)


def fanout_seq_maps(
    state_dir: Path, config: ZfConfig | None = None,
) -> tuple[dict, dict]:
    """(last_seq_by_fanout, started_seq_by_fanout) — task-independent maps.

    _latest_task_fanout_runtime rebuilt these identical maps with a full-log
    scan PER TASK (kanban loop + task detail). Top-level payload.get only —
    exact parity with the original scan, no deep _payload_ref semantics."""

    def _apply(last: dict, started: dict, rows) -> None:
        for seq, event in rows:
            payload = event.payload if isinstance(event.payload, dict) else {}
            fanout_id = str(payload.get("fanout_id") or "").strip()
            if not fanout_id:
                continue
            last[fanout_id] = max(seq, last.get(fanout_id, 0))
            if event.type == "fanout.started":
                started[fanout_id] = seq

    def build() -> tuple[dict, dict]:
        last: dict[str, int] = {}
        started: dict[str, int] = {}
        _apply(last, started, _events_with_seq(state_dir, config=config))
        return (last, started)

    def fold(old: tuple[dict, dict], tail) -> tuple[dict, dict]:
        last, started = dict(old[0]), dict(old[1])
        _apply(last, started, tail)
        return (last, started)

    return _derived_cached(state_dir, "fanout_seq_maps", config, build, fold=fold)


def events_read_days(state_dir, days: int = 1, config=None) -> list:
    """Fingerprint-memoized EventLog.read_days — six projections each decoded
    the log independently per snapshot (119k events decoded for a 10.6k log).
    Key includes the UTC date so the calendar window rolls at midnight."""
    from datetime import datetime, timezone

    from zf.core.events.factory import event_log_from_project

    day_key = datetime.now(timezone.utc).date().isoformat()

    def build():
        return list(event_log_from_project(state_dir, config=config).read_days(days))

    def fold(old: list, tail) -> list:
        # New rows are active-file appends = inside every read_days window.
        # _parse_file silently skips non-JSON lines; the events cache injects
        # an event.malformed placeholder (actor=segments) for them — drop
        # those to keep exact parity. Schema-invalid JSON (actor=zf-cli) stays.
        fresh = [
            event
            for _seq, event in tail
            if not (
                getattr(event, "type", "") == "event.malformed"
                and getattr(event, "actor", "") == "segments"
            )
        ]
        return old + fresh if fresh else old

    return _derived_cached(state_dir, f"read_days:{days}:{day_key}", config, build, fold=fold)


def payload_search_texts(state_dir, config=None) -> list[str]:
    """Lowered payload dumps aligned with _events_with_seq(state_dir)."""
    from zf.web.projections.common import _payload_search_text

    def build():
        return [
            _payload_search_text(getattr(event, "payload", {}) or {})
            for _, event in _events_with_seq(state_dir, config=config)
        ]

    def fold(old: list, tail) -> list:
        return old + [
            _payload_search_text(getattr(event, "payload", {}) or {})
            for _, event in tail
        ]

    return _derived_cached(state_dir, "search_texts", config, build, fold=fold)


def task_event_index(state_dir, config=None):
    """Fingerprint-memoized TaskEventIndex over the memoized events list."""
    from zf.runtime.long_horizon import TaskEventIndex

    def build():
        return TaskEventIndex([event for _, event in _events_with_seq(state_dir, config=config)])

    def fold(old, tail):
        return old.extended([event for _, event in tail])

    return _derived_cached(state_dir, "task_event_index", config, build, fold=fold)


def _archive_inventory(state_dir: Path) -> tuple:
    from zf.core.events.segments import list_event_segments

    return tuple(
        (seg.rel_path, seg.size)
        for seg in list_event_segments(state_dir)
        if seg.kind == "archive"
    )


def _try_append_fold(state_dir: Path, entry: dict, fingerprint: tuple[int, int], config) -> dict | None:
    """Fold new active-log tail onto the cached events (R6-1).

    events.jsonl is append-only and archive segments are immutable, so when
    the archive inventory is unchanged and the active file only grew, the
    cached prefix is still valid — decode just the new bytes instead of the
    whole log (a full rebuild on every append made "warm" a fiction for
    active projects). Any other shape change falls back to a full rebuild.
    """
    if fingerprint == (0, 0):
        return None
    if entry["archives"] != _archive_inventory(state_dir):
        return None
    consumed = entry["consumed"]
    size = fingerprint[0]
    if size < consumed:
        return None
    try:
        with (state_dir / "events.jsonl").open("rb") as fh:
            fh.seek(consumed)
            data = fh.read(size - consumed)
    except OSError:
        return None
    last_newline = data.rfind(b"\n")
    tail_bytes = data[: last_newline + 1] if last_newline >= 0 else b""
    events = entry["events"]
    tail: list[tuple[int, object]] = []
    if tail_bytes:
        from zf.core.events.factory import event_log_from_project
        from zf.core.events.model import ZfEvent

        event_log = event_log_from_project(state_dir, config=config)
        seq = events[-1][0] if events else 0
        for raw in tail_bytes.splitlines():
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            seq += 1
            event = event_log.decode_line(line)
            if event is None:
                # Same placeholder iter_event_records injects for non-JSON rows.
                event = ZfEvent(
                    type="event.malformed",
                    actor="segments",
                    payload={
                        "line": line[:200],
                        "error": "unable to decode event line",
                    },
                )
            tail.append((seq, event))
    return {
        "fp": fingerprint,
        "epoch": entry["epoch"],
        "events": events + tail if tail else events,
        "archives": entry["archives"],
        "consumed": consumed + len(tail_bytes),
        "cost": (fingerprint[0] + sum(size for _rel, size in entry["archives"])) * _DECODED_COST_FACTOR,
        "stamp": next(_CACHE_STAMP),
    }


def _events_state(
    state_dir: Path,
    config: ZfConfig | None = None,
) -> tuple[list[tuple[int, object]], int, tuple[int, int]]:
    """(events, epoch, fingerprint). epoch increments on full rebuild only —
    derived views may fold onto their cached value iff the epoch matched
    (guaranteeing the shared prefix is the same list objects)."""
    key = str(state_dir)
    fingerprint = _event_log_fingerprint(state_dir)
    entry = _EVENTS_WITH_SEQ_CACHE.get(key)
    if entry is not None and entry["fp"] == fingerprint:
        return entry["events"], entry["epoch"], fingerprint
    result = None
    with _EVENTS_WITH_SEQ_CACHE_LOCK:
        entry = _EVENTS_WITH_SEQ_CACHE.get(key)
        if entry is not None and entry["fp"] == fingerprint:
            return entry["events"], entry["epoch"], fingerprint
        if entry is not None:
            folded = _try_append_fold(state_dir, entry, fingerprint, config)
            if folded is not None:
                _EVENTS_WITH_SEQ_CACHE[key] = folded
                result = (folded["events"], folded["epoch"], fingerprint)
        if result is None:
            try:
                records = list(iter_event_records(state_dir, config=config))
            except Exception:
                return [], (entry["epoch"] + 1 if entry else 0), fingerprint
            events = [(record.seq, record.event) for record in records]
            consumed = 0
            for record in reversed(records):
                if record.raw_segment == "events.jsonl":
                    consumed = record.raw_offset + record.raw_length
                    break
            epoch = (entry["epoch"] + 1) if entry else 0
            archives = _archive_inventory(state_dir)
            _EVENTS_WITH_SEQ_CACHE[key] = {
                "fp": fingerprint,
                "epoch": epoch,
                "events": events,
                "archives": archives,
                "consumed": consumed,
                "cost": (fingerprint[0] + sum(size for _rel, size in archives)) * _DECODED_COST_FACTOR,
                "stamp": next(_CACHE_STAMP),
            }
            while len(_EVENTS_WITH_SEQ_CACHE) > _EVENTS_WITH_SEQ_CACHE_MAX:
                _EVENTS_WITH_SEQ_CACHE.pop(next(iter(_EVENTS_WITH_SEQ_CACHE)))
            result = (events, epoch, fingerprint)
    _enforce_cache_budget()
    return result


def _events_with_seq(
    state_dir: Path,
    config: ZfConfig | None = None,
) -> list[tuple[int, object]]:
    events, _epoch, _fp = _events_state(state_dir, config=config)
    return events


def _event_log_fingerprint(state_dir: Path) -> tuple[int, int]:
    try:
        stat = (state_dir / "events.jsonl").stat()
    except OSError:
        return (0, 0)
    return (int(stat.st_size), int(stat.st_mtime_ns))


def _events_with_exact_task_id(
    state_dir: Path,
    task_id: str,
    config: ZfConfig | None = None,
) -> list[tuple[int, object]]:
    try:
        out: list[tuple[int, object]] = []
        for record in iter_event_records(state_dir, config=config):
            if task_id not in record.raw_line or not _raw_event_has_task_id(record.raw_line, task_id):
                continue
            out.append((record.seq, record.event))
        return out
    except Exception:
        return []


def _trace_id_from_events(events: list[tuple[int, object]]) -> Any:
    return _first_nonempty(
        [getattr(event, "correlation_id", None) for _, event in reversed(events)]
        + [
            _payload_ref(getattr(event, "payload", {}), "trace_id")
            for _, event in reversed(events)
        ]
    )


def _event_to_dict(seq: int, event: object) -> dict:
    safe_event = redact_event(event)  # type: ignore[arg-type]
    return {
        "seq": seq,
        "id": getattr(safe_event, "id", ""),
        "ts": getattr(safe_event, "ts", ""),
        "type": getattr(safe_event, "type", ""),
        "actor": getattr(safe_event, "actor", None),
        "task_id": getattr(safe_event, "task_id", None),
        "payload": getattr(safe_event, "payload", {}) or {},
        "causation_id": getattr(safe_event, "causation_id", None),
        "correlation_id": getattr(safe_event, "correlation_id", None),
    }


def _last_event_by_actor(
    state_dir: Path,
    config: ZfConfig | None = None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for _, event in _events_with_seq(state_dir, config=config):
        actor = getattr(event, "actor", None)
        if actor:
            out[str(actor)] = getattr(event, "ts", "")
    return out


def _active_task_by_instance(
    state_dir: Path,
    config: ZfConfig | None = None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    terminal_task_ids: set[str] = set()
    path = state_dir / "kanban.json"
    if path.exists():
        try:
            for task in TaskStore(path).list_all_with_archive():
                if task.status in {"done", "cancelled"}:
                    terminal_task_ids.add(task.id)
                elif task.assigned_to:
                    out[str(task.assigned_to)] = task.id
        except Exception:
            pass
    for _, event in reversed(_events_with_seq(state_dir, config=config)):
        actor = getattr(event, "actor", None)
        task_id = getattr(event, "task_id", None)
        if actor and task_id and str(task_id) not in terminal_task_ids:
            out.setdefault(str(actor), str(task_id))
    return out


def _stage_summary(
    events: list[tuple[int, object]],
    prefixes: set[str],
) -> dict:
    stage_events = [
        (seq, event)
        for seq, event in events
        if any(getattr(event, "type", "").startswith(prefix) for prefix in prefixes)
    ]
    if not stage_events:
        return {"state": "empty", "latest": None, "event_count": 0}
    latest_seq, latest = stage_events[-1]
    event_type = getattr(latest, "type", "")
    state = "observed"
    if any(token in event_type for token in ("passed", "approved", "completed")):
        state = "approved" if "approved" in event_type else "passed"
    elif any(token in event_type for token in ("failed", "rejected", "blocked")):
        state = "rejected" if "rejected" in event_type else "failed"
    return {
        "state": state,
        "latest": _event_to_dict(latest_seq, latest),
        "event_count": len(stage_events),
    }


def _recent_events(
    state_dir: Path,
    limit: int,
    config: ZfConfig | None = None,
) -> list[dict]:
    events = _events_with_seq(state_dir, config=config)
    tail = events[-limit:] if len(events) > limit else events
    return [_event_to_dict(seq, event) for seq, event in tail]


async def _tail_events(
    state_dir: Path,
    request: Request,
    event_log: EventLog | None = None,
    cursor: int | None = None,
) -> AsyncIterator[bytes]:
    """SSE generator: replay by cursor, then tail events.jsonl merged with
    the ephemeral LiveDeltaBus (doc 106 B axis — token deltas never touch
    the ledger; they ride the same SSE wire with the current committed seq,
    so client reconnect cursors stay anchored to committed events only)."""
    from zf.runtime.live_delta_bus import LiveDeltaBus

    path = state_dir / "events.jsonl"
    event_log = event_log or EventLog(path)
    live_bus = LiveDeltaBus(state_dir)
    live_cursors: dict[str, int] = {}
    live_sweep_countdown = 0
    live_bus.sweep()
    # Send initial heartbeat so EventSource onopen fires deterministically
    yield b": connected\n\n"
    last_size = 0
    last_inode = -1
    last_seq = 0

    try:
        stat = path.stat()
        last_inode = stat.st_ino
        last_size = stat.st_size
        last_seq = _line_count(path)
        replay_from = cursor if cursor is not None else 0
        if cursor is not None and cursor > last_seq:
            yield _sse_gap(cursor=cursor, current=last_seq)
        else:
            # P1-8 (2026-07-09): the replay read (_read_events_with_seq) does a
            # full read_text() + per-line decode (incl. signature verify) with no
            # cache. Running it inline on the asyncio event loop froze *every*
            # SSE/HTTP client for its duration on a large log — and a
            # reconnecting EventSource re-triggered it every ~3s. Offload to a
            # thread so a heavy replay only delays the connecting client, not the
            # shared event loop.
            replay = await asyncio.get_running_loop().run_in_executor(
                None, _read_events_with_seq, path, event_log
            )
            for seq, event in replay:
                if seq <= replay_from:
                    continue
                yield _sse_event(seq, event)
    except FileNotFoundError:
        if cursor:
            yield _sse_gap(cursor=cursor, current=0)

    while True:
        if await request.is_disconnected():
            return
        try:
            stat = path.stat()
        except FileNotFoundError:
            await asyncio.sleep(0.5)
            continue
        # Detect rotation (new inode) — reopen from start
        if stat.st_ino != last_inode:
            last_inode = stat.st_ino
            last_size = 0
            last_seq = 0
        if stat.st_size > last_size:
            try:
                with path.open("rb") as f:
                    f.seek(last_size)
                    chunk = f.read(stat.st_size - last_size)
                last_size = stat.st_size
            except OSError:
                await asyncio.sleep(0.5)
                continue
            for raw_line in chunk.splitlines():
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                event = event_log.decode_line(line)
                if event is None or event.type == "event.malformed":
                    continue
                last_seq += 1
                yield _sse_event(last_seq, event)
        else:
            yield b": ping\n\n"  # comment heartbeat keeps connection warm
        try:
            live_rows, live_cursors = live_bus.read_since(live_cursors)
        except Exception:
            live_rows = []
        for row in live_rows:
            yield _sse_event(last_seq, row)
        live_sweep_countdown -= 1
        if live_sweep_countdown <= 0:
            live_sweep_countdown = 120
            try:
                live_bus.sweep()
            except Exception:
                pass
        await asyncio.sleep(0.5)
