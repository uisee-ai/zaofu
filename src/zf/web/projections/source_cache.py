"""Persist pure projections against exact declared file/config dependencies.

No TTL or stale response: clock values and external probes do not belong here.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Callable, TypeVar

from zf.core.events.segments import list_event_segments
from zf.core.state.locks import locked_path
from zf.web.projections import read_model
from zf.web.projections.loop_view_source import _decoder_identity
from zf.web.projections.projection_failures import collect_projection_failures, record_projection_failure

T = TypeVar("T")


def fingerprint(state_dir: Path, paths: list[Path], config=None) -> str:
    digest = hashlib.sha256(str(state_dir.resolve()).encode())
    digest.update(_decoder_identity(config).encode())
    if config is not None:
        digest.update(json.dumps(asdict(config), sort_keys=True, default=str).encode())
    inputs = [segment.path for segment in list_event_segments(state_dir)] + paths
    for path in sorted(set(inputs)):
        digest.update(str(path).encode())
        try:
            stat = path.stat()
            digest.update(str((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)).encode())
        except FileNotFoundError:
            digest.update(b"missing")
    return digest.hexdigest()


def read_source_projection(state_dir: Path, key: str, paths: list[Path], build: Callable[[], T], *, config=None) -> T:
    before = fingerprint(state_dir, paths, config)
    cache_key = f"source-projection.v1:{key}"
    cached = read_model.get_cached_projection(state_dir, cache_key)
    if cached is not None and cached.get("fingerprint") == before and before == fingerprint(state_dir, paths, config):
        return cached["value"]
    with locked_path(state_dir / "projections" / ("source-" + hashlib.sha256(key.encode()).hexdigest()[:16])):
        before = fingerprint(state_dir, paths, config)
        cached = read_model.get_cached_projection(state_dir, cache_key)
        if cached is not None and cached.get("fingerprint") == before and before == fingerprint(state_dir, paths, config):
            return cached["value"]
        with collect_projection_failures() as failures:
            result = build()
        for failure in failures:
            record_projection_failure(failure)
        if not failures and before == fingerprint(state_dir, paths, config):
            try:
                read_model.set_cached_projection(state_dir, cache_key, kind="source-projection", source_seq=0,
                                                  payload={"fingerprint": before, "value": result})
            except sqlite3.DatabaseError:
                pass  # Disposable cache failure must not erase canonical data.
        return result


def task_paths(state_dir: Path) -> list[Path]:
    # Include the directory identity to detect newly created/removed archives
    # during a build, in addition to content identities of existing entries.
    return [state_dir / "kanban.json", state_dir / "kanban", *sorted((state_dir / "kanban").glob("*.json"))]
