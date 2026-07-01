"""Projections layer: events (moved verbatim from web/server.py)."""
from __future__ import annotations

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
        "git_refs": _refs_from_events(events),
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
_EVENTS_WITH_SEQ_CACHE: dict[str, tuple[tuple[int, int], list[tuple[int, object]]]] = {}
_EVENTS_WITH_SEQ_CACHE_LOCK = threading.Lock()
_EVENTS_WITH_SEQ_CACHE_MAX = 6


def _events_with_seq(
    state_dir: Path,
    config: ZfConfig | None = None,
) -> list[tuple[int, object]]:
    key = str(state_dir)
    fingerprint = _event_log_fingerprint(state_dir)
    cached = _EVENTS_WITH_SEQ_CACHE.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    try:
        events = [(record.seq, record.event) for record in iter_event_records(state_dir, config=config)]
    except Exception:
        return []
    with _EVENTS_WITH_SEQ_CACHE_LOCK:
        _EVENTS_WITH_SEQ_CACHE[key] = (fingerprint, events)
        while len(_EVENTS_WITH_SEQ_CACHE) > _EVENTS_WITH_SEQ_CACHE_MAX:
            _EVENTS_WITH_SEQ_CACHE.pop(next(iter(_EVENTS_WITH_SEQ_CACHE)))
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
    """SSE generator: replay by cursor, then tail events.jsonl."""
    path = state_dir / "events.jsonl"
    event_log = event_log or EventLog(path)
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
            for seq, event in _read_events_with_seq(path, event_log):
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
        await asyncio.sleep(0.5)
