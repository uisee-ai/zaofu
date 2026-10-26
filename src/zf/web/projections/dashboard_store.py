"""Durable, replaceable dashboard resources in the existing projection database.

Rows, generation metadata and the source receipt commit together. No canonical
store is written here; an absent projection is not an empty task list.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.web.projections.read_model import db_path, _connect
from zf.web.projections.sqlite_reader import connect_readonly

VERSION = "dashboard-resources.v2"
# Clock-derived ages/windows and external probes are snapshots as of generated_at.
REFRESH_SECONDS = 60.0


def source_fingerprint(state_dir: Path, project_root: Path, config: ZfConfig | None) -> str:
    digest = hashlib.sha256()
    digest.update(scope_identity(state_dir, project_root, config).encode())
    digest.update(str(int(time.time() // 86400)).encode())
    excluded_dirs = {"projections", "workdirs", "worktrees", ".git", "node_modules"}
    excluded_files = {"event_index.json", "manifest.json.lock"}
    for directory, dirs, names in os.walk(state_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in excluded_dirs)
        for name in sorted(names):
            path = Path(directory) / name
            rel = path.relative_to(state_dir).as_posix()
            if rel.startswith(("logs/web-api-timing.jsonl", "operator/webchat.sqlite3")) or name in excluded_files or rel == "events/manifest.json" or name.endswith((".lock", ".tmp")) and name != "loop.lock":
                continue
            _stat_hash(digest, path, rel)
    _stat_hash(digest, project_root / "zf.yaml", "project-config")
    # Git probes are also refreshed on the clock; track HEAD/index immediately.
    git_dir = project_root / ".git"
    if git_dir.is_file():
        try:
            value = git_dir.read_text().strip().removeprefix("gitdir: ")
            git_dir = (project_root / value).resolve()
        except OSError:
            pass
    for name in ("HEAD", "index", "packed-refs"):
        _stat_hash(digest, git_dir / name, "git/" + name)
    return digest.hexdigest()


def quick_fingerprint(state_dir: Path, project_root: Path, config: ZfConfig | None) -> str:
    """Bounded request check; recursive sidecar verification belongs to the worker.

    Canonical task/config/cost/session files and event appends wake updates
    immediately. Deep sidecar-only and external-probe changes are checked by
    the full fingerprint at the bounded clock refresh.
    """
    from zf.core.events.segments import build_event_manifest
    digest = hashlib.sha256(scope_identity(state_dir, project_root, config).encode())
    digest.update(str(int(time.time() // 86400)).encode())
    digest.update(build_event_manifest(state_dir).digest.encode())
    for path in sorted(state_dir.iterdir()):
        if path.name == "event_index.json" or path.name.endswith((".lock", ".tmp")) and path.name != "loop.lock":
            continue
        if path.is_file():
            _stat_hash(digest, path, path.name)
    for pattern in ("fanouts/*/manifest.json", "kanban/*.json"):
        for path in sorted(state_dir.glob(pattern)):
            _stat_hash(digest, path, path.relative_to(state_dir).as_posix())
    _stat_hash(digest, project_root / "zf.yaml", "project-config")
    return digest.hexdigest()


def scope_identity(state_dir: Path, project_root: Path, config: ZfConfig | None) -> str:
    data = f"{VERSION}\0{state_dir.resolve()}\0{project_root.resolve()}"
    if config is not None:
        data += json.dumps(asdict(config), sort_keys=True, default=str)
    return hashlib.sha256(data.encode()).hexdigest()


def _stat_hash(digest: Any, path: Path, label: str) -> None:
    try:
        stat = path.stat()
        digest.update(f"{label}\0{stat.st_dev}\0{stat.st_ino}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    except OSError:
        digest.update(f"{label}\0missing\n".encode())


def read_group(state_dir: Path, group: str) -> tuple[dict, dict] | None:
    try:
        with connect_readonly(db_path(state_dir)) as conn:
            conn.execute("BEGIN")
            meta = conn.execute("SELECT * FROM dashboard_generation WHERE group_name=? AND version=?", (group, VERSION)).fetchone()
            if meta is None:
                return None
            rows = conn.execute("SELECT resource, ordinal, payload FROM dashboard_resource WHERE group_name=? ORDER BY resource, ordinal", (group,)).fetchall()
            shapes = json.loads(meta["shapes"])
            if not isinstance(json.loads(meta["cursor"]), dict):
                return None
            data: dict[str, Any] = {key: [] if shape == "list" else None for key, shape in shapes.items()}
            for row in rows:
                value = json.loads(row["payload"])
                if shapes[row["resource"]] == "list":
                    data[row["resource"]].append(value)
                else:
                    data[row["resource"]] = value
            return data, dict(meta)
    except (sqlite3.Error, ValueError, KeyError, TypeError):
        return None


def write_group(state_dir: Path, group: str, data: dict, *, fingerprint: str, cursor: dict, source_changed: bool, scope: str = "", trigger: str = "") -> None:
    shapes = {key: "list" if isinstance(value, list) else "value" for key, value in data.items()}
    rows = [(group, key, i, json.dumps(row, ensure_ascii=False, allow_nan=False))
            for key, value in data.items()
            for i, row in enumerate(value if isinstance(value, list) else [value])]
    if sum(len(row[3].encode()) for row in rows) > 16 * 1024 * 1024:
        raise ValueError("dashboard resource generation exceeds 16 MiB")
    conn = _connect(db_path(state_dir))
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS dashboard_generation (
            group_name TEXT PRIMARY KEY, version TEXT NOT NULL, fingerprint TEXT NOT NULL,
            built_at REAL NOT NULL, shapes TEXT NOT NULL, cursor TEXT NOT NULL,
            source_changed INTEGER NOT NULL, scope TEXT NOT NULL DEFAULT '', trigger_fingerprint TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS dashboard_resource (
            group_name TEXT NOT NULL, resource TEXT NOT NULL, ordinal INTEGER NOT NULL,
            payload TEXT NOT NULL, PRIMARY KEY (group_name, resource, ordinal)
        ) WITHOUT ROWID;
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(dashboard_generation)")}
        for column in ("scope", "trigger_fingerprint"):
            if column not in columns:
                conn.execute(f"ALTER TABLE dashboard_generation ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        with conn:
            previous = {(row["resource"], row["ordinal"]): row["payload"] for row in conn.execute(
                "SELECT resource, ordinal, payload FROM dashboard_resource WHERE group_name=?", (group,))}
            current = {(row[1], row[2]) for row in rows}
            conn.executemany("DELETE FROM dashboard_resource WHERE group_name=? AND resource=? AND ordinal=?",
                             [(group, key, ordinal) for key, ordinal in previous.keys() - current])
            changed = [row for row in rows if previous.get((row[1], row[2])) != row[3]]
            conn.executemany("INSERT OR REPLACE INTO dashboard_resource VALUES (?, ?, ?, ?)", changed)
            conn.execute("INSERT OR REPLACE INTO dashboard_generation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (group, VERSION, fingerprint, time.time(), json.dumps(shapes), json.dumps(cursor), int(source_changed), scope, trigger))
    finally:
        conn.close()
