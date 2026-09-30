"""Rebuildable byte-offset index; the append-only JSONL remains authoritative.

Callers hold the JSONL's locked_path for lookup and append. Never cache entire
responses (or a history-sized Python list) in the Web process.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Iterator


@contextmanager
def open_idempotency_index(path: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path.with_suffix(".index.sqlite3"), timeout=30)
    try:
        db.execute("PRAGMA cache_size = -2048")
        db.execute("PRAGMA temp_store = FILE")
        db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, offset INTEGER NOT NULL, length INTEGER NOT NULL, state TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS cursor (id INTEGER PRIMARY KEY CHECK (id=1), device INTEGER, inode INTEGER, offset INTEGER, mtime INTEGER)")
        _refresh(db, path)
        yield db
    finally:
        db.close()


def _refresh(db: sqlite3.Connection, path: Path) -> None:
    stat = path.stat() if path.exists() else None
    saved = db.execute("SELECT device, inode, offset, mtime FROM cursor WHERE id=1").fetchone()
    offset = saved[2] if saved else 0
    with db:
        if stat is None:
            db.execute("DELETE FROM entries")
            db.execute("DELETE FROM cursor")
            return
        if saved and (saved[:2] != (stat.st_dev, stat.st_ino) or stat.st_size < offset
                      or (stat.st_size == offset and stat.st_mtime_ns != saved[3])):
            db.execute("DELETE FROM entries")
            offset = 0
        if saved and offset == stat.st_size and stat.st_mtime_ns == saved[3]:
            return
        with path.open("rb") as stream:
            stream.seek(offset)
            while True:
                start = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    # Never treat an uncertain receipt as absent and execute
                    # a potentially completed external mutation a second time.
                    raise ValueError("Incomplete idempotency receipt; recovery required before mutation")
                offset = stream.tell()
                try:
                    item = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not item["key"]:
                    continue
                db.execute("INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?)",
                           (item["key"], start, len(line), str(item.get("state") or "")))
        db.execute("INSERT OR REPLACE INTO cursor VALUES (1, ?, ?, ?, ?)",
                   (stat.st_dev, stat.st_ino, offset, stat.st_mtime_ns))


def indexed_record(path: Path, row: tuple[int, int]) -> dict:
    with path.open("rb") as stream:
        stream.seek(row[0])
        result = json.loads(stream.read(row[1]))
    if not isinstance(result, dict):
        raise ValueError("Invalid idempotency receipt")
    return result


def latest_record(db: sqlite3.Connection, path: Path, key: str) -> dict | None:
    row = db.execute("SELECT offset, length FROM entries WHERE key=?", (key,)).fetchone()
    return indexed_record(path, row) if row else None


def pending_records(db: sqlite3.Connection, path: Path) -> Iterator[dict]:
    for row in db.execute("SELECT offset, length FROM entries WHERE state='pending'"):
        yield indexed_record(path, row)
