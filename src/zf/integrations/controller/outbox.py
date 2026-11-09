"""Durable, bounded delivery queue. ACK never deletes source EventLog facts."""

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class OutboxFull(RuntimeError):
    pass


class Outbox:
    def __init__(self, state_dir: Path, config):
        self.path = Path(state_dir) / "controller" / "outbox.sqlite3"
        self.config = config
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT UNIQUE NOT NULL,
                  message_id TEXT NOT NULL, body TEXT NOT NULL, size INTEGER NOT NULL, status TEXT NOT NULL,
                  created REAL NOT NULL, reason TEXT, origin_id TEXT NOT NULL, wire_seq INTEGER NOT NULL,
                  UNIQUE(origin_id, wire_seq));
                CREATE TABLE IF NOT EXISTS samples(series TEXT PRIMARY KEY, sequence INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS bindings(deployment TEXT PRIMARY KEY, mode TEXT NOT NULL, started REAL NOT NULL);
            ''')
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('stream_id', ?)", (str(uuid.uuid4()),))
            for key in ("workspace_id", "project_id"):
                existing = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
                value = getattr(config, key)
                if existing and existing[0] != value:
                    raise ValueError("persistent outbox identity mismatch")
                db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))
            binding = db.execute("SELECT * FROM bindings ORDER BY started DESC LIMIT 1").fetchone()
            if binding is not None and binding['deployment'] == config.deployment_id:
                if binding['mode'] != config.mode:
                    raise ValueError("changing accounting mode requires a new deployment binding")
            else:
                if db.execute("SELECT 1 FROM bindings WHERE deployment=?", (config.deployment_id,)).fetchone():
                    raise ValueError("retired deployment cannot reclaim an outbox")
                db.execute("INSERT INTO bindings VALUES (?,?,?)", (config.deployment_id, config.mode, time.time()))
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def binding_at(self, timestamp: str) -> dict | None:
        occurred = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        if occurred.tzinfo is None:
            raise ValueError("source timestamp must include a timezone")
        with self.connect() as db:
            row = db.execute("SELECT * FROM bindings WHERE started<=? ORDER BY started DESC LIMIT 1", (occurred.timestamp(),)).fetchone()
        return dict(row) if row else None

    def enqueue(self, source_key: str, occurred_at: str, payload: dict, *, sample_series: str = "", origin_id: str = "") -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM messages WHERE source_key=?", (source_key,)).fetchone():
                return False
            pending = db.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM messages WHERE status != 'acked'").fetchone()
            if pending[0] >= self.config.max_pending_records:
                raise OutboxFull("Controller outbox record capacity reached")
            stream_id = db.execute("SELECT value FROM metadata WHERE key='stream_id'").fetchone()[0]
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM messages").fetchone()[0]
            origin_id = origin_id or self.config.deployment_id
            wire_seq = db.execute("SELECT COALESCE(MAX(wire_seq),0)+1 FROM messages WHERE origin_id=?", (origin_id,)).fetchone()[0]
            payload = dict(payload)
            if sample_series:
                db.execute("INSERT INTO samples VALUES (?, 1) ON CONFLICT(series) DO UPDATE SET sequence=sequence+1", (sample_series,))
                payload["sample_seq"] = db.execute("SELECT sequence FROM samples WHERE series=?", (sample_series,)).fetchone()[0]
            message_id = hashlib.sha256((self.config.project_id + ':' + source_key).encode()).hexdigest()
            body = json.dumps({"protocol": "zfc.telemetry.v1", "workspace_id": self.config.workspace_id,
                "origin_deployment_id": origin_id, "stream_id": stream_id, "seq": wire_seq,
                "message_id": message_id, "occurred_at": occurred_at, "payload": payload}, separators=(',', ':'))
            size = len(body.encode())
            if size > 60000 or pending[1] + size > self.config.max_pending_bytes:
                raise OutboxFull("Controller outbox byte capacity reached")
            db.execute("INSERT INTO messages(seq, source_key, message_id, body, size, status, created, origin_id, wire_seq) VALUES (?,?,?,?,?,'pending',?,?,?)",
                       (seq, source_key, message_id, body, size, time.time(), origin_id, wire_seq))
        return True

    def pending(self, limit: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT body FROM messages WHERE status='pending' ORDER BY seq LIMIT ?", (limit,)).fetchall()
        result, size = [], 100
        for row in rows:
            size += len(row[0].encode()) + 1
            if size > 64000:
                break
            result.append(json.loads(row[0]))
        return result

    def snapshot(self, fingerprint: str, occurred_at: str, payload: dict) -> None:
        with self.connect() as db:
            previous = db.execute("SELECT value FROM metadata WHERE key='project_snapshot'").fetchone()
        if previous and previous[0] == fingerprint:
            return
        # A crash between enqueue and fingerprint commit may repeat a snapshot;
        # its source version remains ordered and usage is unaffected.
        self.enqueue('project:' + self.config.deployment_id + ':' + occurred_at, occurred_at, payload)
        with self.connect() as db:
            db.execute("INSERT INTO metadata VALUES ('project_snapshot',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (fingerprint,))

    def acknowledge(self, sent: list[dict], response: dict) -> None:
        if response.get('protocol') != 'zfc.telemetry.v1' or len(response.get('results', [])) != len(sent):
            raise ValueError("invalid Controller ACK")
        with self.connect() as db:
            for message, result in zip(sent, response['results']):
                if result.get('message_id') not in (None, message['message_id']) or result.get('seq', message['seq']) != message['seq']:
                    raise ValueError("ACK message identity mismatch")
                status = result.get('status')
                if status in ('accepted', 'duplicate'):
                    if result.get('message_id') != message['message_id']:
                        raise ValueError("accepted ACK must name the message")
                    # Keep an idempotency tombstone, release the durable payload space.
                    db.execute("UPDATE messages SET status='acked',body='',size=0 WHERE message_id=?", (message['message_id'],))
                elif status == 'rejected':
                    db.execute("UPDATE messages SET status='quarantined', reason=? WHERE message_id=?", (str(result.get('reason') or 'rejected')[:64], message['message_id']))
                elif status != 'retry':
                    raise ValueError("unknown ACK status")

    def status(self) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT COUNT(*),COALESCE(SUM(size),0),MIN(created) FROM messages WHERE status!='acked'").fetchone()
            quarantine = db.execute("SELECT COUNT(*) FROM messages WHERE status='quarantined'").fetchone()[0]
            stream = db.execute("SELECT value FROM metadata WHERE key='stream_id'").fetchone()[0]
            started = db.execute("SELECT MIN(started) FROM bindings").fetchone()[0]
            origins = [dict(item) for item in db.execute('SELECT origin_id, MAX(wire_seq) AS max_seq FROM messages GROUP BY origin_id')]
        return {"pending_records": row[0], "pending_bytes": row[1], "oldest_pending": row[2], "quarantined_records": quarantine, "stream_id": stream,
                "coverage_since": datetime.fromtimestamp(started, timezone.utc).isoformat(), 'origins': origins}
