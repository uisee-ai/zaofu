"""Lightweight Web/API timing diagnostics.

The timing log is a runtime projection, not control-plane truth. It is written
under ``state_dir/logs`` so operators can measure slow API paths without
coupling the dashboard to the orchestrator internals.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from fastapi import Request


_PROJECT_RE = re.compile(r"^/api/projects/([^/]+)")
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def timing_log_path(state_dir: Path) -> Path:
    return Path(state_dir) / "logs" / "web-api-timing.jsonl"


def should_record_path(path: str) -> bool:
    if not path.startswith("/api"):
        return False
    if path.endswith("/stream") or "/stream?" in path:
        return False
    return True


def project_id_from_path(path: str) -> str:
    match = _PROJECT_RE.match(path)
    return match.group(1) if match else ""


def state_dir_digest(state_dir: Path) -> str:
    return sha256(str(Path(state_dir).resolve()).encode("utf-8")).hexdigest()[:12]


def route_pattern(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    return str(path or request.url.path)


def record_timing(
    state_dir: Path,
    *,
    method: str,
    path: str,
    route: str,
    status_code: int,
    elapsed_ms: float,
    response_bytes: int | None = None,
) -> None:
    record = {
        "schema_version": "web-api-timing.v1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "route": route,
        "project_id": project_id_from_path(path),
        "state_dir_hash": state_dir_digest(state_dir),
        "status_code": int(status_code),
        "elapsed_ms": round(float(elapsed_ms), 3),
        "response_bytes": int(response_bytes or 0),
    }
    path_obj = timing_log_path(state_dir)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_for(path_obj)
    with lock:
        with path_obj.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def summarize_timings(
    state_dir: Path,
    *,
    project_id: str = "",
    limit: int = 2000,
    slow_limit: int = 10,
) -> dict[str, Any]:
    records = list(_read_recent_records(timing_log_path(state_dir), limit=max(1, min(limit, 20_000))))
    if project_id:
        records = [item for item in records if str(item.get("project_id") or "") == project_id]
    by_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_route[str(record.get("route") or record.get("path") or "")].append(record)
    route_rows = [
        _route_summary(route, rows)
        for route, rows in sorted(by_route.items())
        if route
    ]
    route_rows.sort(key=lambda item: (item["p95_ms"], item["max_ms"]), reverse=True)
    elapsed = [float(record.get("elapsed_ms") or 0.0) for record in records]
    bytes_values = [int(record.get("response_bytes") or 0) for record in records]
    return {
        "schema_version": "web-perf-summary.v1",
        "source": str(timing_log_path(state_dir)),
        "project_id": project_id,
        "count": len(records),
        "p50_ms": _percentile(elapsed, 0.50),
        "p95_ms": _percentile(elapsed, 0.95),
        "max_ms": max(elapsed) if elapsed else 0.0,
        "total_response_bytes": sum(bytes_values),
        "routes": route_rows[:slow_limit],
        "slowest": sorted(
            records,
            key=lambda item: float(item.get("elapsed_ms") or 0.0),
            reverse=True,
        )[:slow_limit],
    }


def response_size_from_headers(headers: Any) -> int | None:
    try:
        raw = headers.get("content-length")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return max(0, int(str(raw)))
    except ValueError:
        return None


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def _read_recent_records(path: Path, *, limit: int) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _route_summary(route: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [float(row.get("elapsed_ms") or 0.0) for row in rows]
    bytes_values = [int(row.get("response_bytes") or 0) for row in rows]
    errors = [row for row in rows if int(row.get("status_code") or 0) >= 500]
    return {
        "route": route,
        "count": len(rows),
        "p50_ms": _percentile(elapsed, 0.50),
        "p95_ms": _percentile(elapsed, 0.95),
        "max_ms": max(elapsed) if elapsed else 0.0,
        "avg_response_bytes": round(sum(bytes_values) / len(bytes_values), 1) if bytes_values else 0.0,
        "error_count": len(errors),
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * pct))))
    return round(ordered[index], 3)
