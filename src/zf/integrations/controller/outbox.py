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
                CREATE TABLE IF NOT EXISTS mode_epochs(deployment TEXT NOT NULL, mode TEXT NOT NULL,
                  started REAL NOT NULL, PRIMARY KEY(deployment, started));
                CREATE TABLE IF NOT EXISTS capacity(id TEXT PRIMARY KEY, records INTEGER NOT NULL, bytes INTEGER NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO mode_epochs SELECT deployment,mode,started FROM bindings')
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
                    epoch = getattr(config, '_mode_epoch', 0)
                    latest = db.execute('SELECT MAX(started) FROM mode_epochs WHERE deployment=?', (config.deployment_id,)).fetchone()[0]
                    if not epoch or epoch <= latest or db.execute('SELECT 1 FROM capacity LIMIT 1').fetchone():
                        raise ValueError("changing accounting mode requires a drained, applied policy boundary")
                    db.execute('INSERT INTO mode_epochs VALUES (?,?,?)', (config.deployment_id, config.mode, epoch))
                    db.execute('UPDATE bindings SET mode=? WHERE deployment=?', (config.mode, config.deployment_id))
            else:
                if db.execute("SELECT 1 FROM bindings WHERE deployment=?", (config.deployment_id,)).fetchone():
                    raise ValueError("retired deployment cannot reclaim an outbox")
                db.execute("INSERT INTO bindings VALUES (?,?,?)", (config.deployment_id, config.mode, time.time()))
                db.execute('INSERT INTO mode_epochs SELECT deployment,mode,started FROM bindings WHERE deployment=?', (config.deployment_id,))
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
            row = db.execute("SELECT * FROM mode_epochs WHERE started<=? ORDER BY started DESC LIMIT 1", (occurred.timestamp(),)).fetchone()
        return dict(row) if row else None

    def has_reservations(self):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM capacity LIMIT 1').fetchone() is not None

    def enqueue(self, source_key: str, occurred_at: str, payload: dict, *, sample_series: str = "", origin_id: str = "", baseline_first: bool = False) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM messages WHERE source_key=?", (source_key,)).fetchone():
                return False
            pending = db.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM messages WHERE status IN ('pending','quarantined')").fetchone()
            reserved = db.execute('SELECT COALESCE(SUM(records),0),COALESCE(SUM(bytes),0) FROM capacity WHERE id!=?',
                                  (str(payload.get('operation_id') or ''),)).fetchone()
            if pending[0] + reserved[0] >= self.config.max_pending_records:
                raise OutboxFull("Controller outbox record capacity reached")
            stream_id = db.execute("SELECT value FROM metadata WHERE key='stream_id'").fetchone()[0]
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM messages").fetchone()[0]
            origin_id = origin_id or self.config.deployment_id
            wire_seq = db.execute("SELECT COALESCE(MAX(wire_seq),0)+1 FROM messages WHERE origin_id=?", (origin_id,)).fetchone()[0]
            payload = dict(payload)
            if sample_series:
                baseline_key = 'series-counter:' + sample_series
                previous_sample = db.execute('SELECT sequence FROM samples WHERE series=?', (sample_series,)).fetchone()
                # Existing v1 streams have an established balance; do not rebaseline on upgrade.
                db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', (baseline_key, 'known' if previous_sample else 'pending'))
                db.execute("INSERT INTO samples VALUES (?, 1) ON CONFLICT(series) DO UPDATE SET sequence=sequence+1", (sample_series,))
                payload["sample_seq"] = db.execute("SELECT sequence FROM samples WHERE series=?", (sample_series,)).fetchone()[0]
                first_counter = db.execute('SELECT value FROM metadata WHERE key=?', (baseline_key,)).fetchone()[0] == 'pending'
                payload['baseline_only'] = bool(first_counter and baseline_first and payload.get('tokens') is not None)
                if payload.get('tokens') is not None:
                    db.execute('UPDATE metadata SET value=? WHERE key=?', ('known', baseline_key))
            message_id = hashlib.sha256((self.config.project_id + ':' + source_key).encode()).hexdigest()
            body = json.dumps({"protocol": "zfc.telemetry.v1", "workspace_id": self.config.workspace_id,
                "origin_deployment_id": origin_id, "stream_id": stream_id, "seq": wire_seq,
                "message_id": message_id, "occurred_at": occurred_at, "payload": payload}, separators=(',', ':'))
            size = len(body.encode())
            if size > 60000 or pending[1] + reserved[1] + size > self.config.max_pending_bytes:
                raise OutboxFull("Controller outbox byte capacity reached")
            db.execute("INSERT INTO messages(seq, source_key, message_id, body, size, status, created, origin_id, wire_seq) VALUES (?,?,?,?,?,'pending',?,?,?)",
                       (seq, source_key, message_id, body, size, time.time(), origin_id, wire_seq))
        return True

    def pending(self, limit: int, *, receipt_only: bool = False) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT body FROM messages WHERE status='pending' "
                              "AND (?=0 OR json_extract(body,'$.payload.kind')='usage') ORDER BY seq LIMIT ?",
                              (int(receipt_only), limit)).fetchall()
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
                    # Retain the original wire identity/body for Controller snapshot recovery.
                    db.execute("UPDATE messages SET status='acked' WHERE message_id=?", (message['message_id'],))
                elif status == 'rejected':
                    db.execute("UPDATE messages SET status='quarantined', reason=? WHERE message_id=?", (str(result.get('reason') or 'rejected')[:64], message['message_id']))
                elif status != 'retry':
                    raise ValueError("unknown ACK status")

    def status(self) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT COUNT(*),COALESCE(SUM(size),0),MIN(created) FROM messages WHERE status IN ('pending','quarantined')").fetchone()
            quarantine = db.execute("SELECT COUNT(*) FROM messages WHERE status='quarantined'").fetchone()[0]
            stream = db.execute("SELECT value FROM metadata WHERE key='stream_id'").fetchone()[0]
            started = db.execute("SELECT MIN(started) FROM bindings").fetchone()[0]
            origins = [dict(item) for item in db.execute('SELECT origin_id, MAX(wire_seq) AS max_seq FROM messages GROUP BY origin_id')]
        return {"pending_records": row[0], "pending_bytes": row[1], "oldest_pending": row[2], "quarantined_records": quarantine, "stream_id": stream,
                "coverage_since": datetime.fromtimestamp(started, timezone.utc).isoformat(), 'origins': origins}

    def admit(self, *, records: int = 16, bytes_required: int = 60000) -> None:
        """Fail before a new provider turn if receipts cannot be durably buffered."""
        status = self.status()
        if (status['pending_records'] + records > self.config.max_pending_records
                or status['pending_bytes'] + bytes_required > self.config.max_pending_bytes):
            raise OutboxFull('Controller receipt capacity requires draining before new execution')

    def reserve_capacity(self, identifier: str, *, records=8, bytes_required=60000):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM capacity WHERE id=?', (identifier,)).fetchone():
                return
            pending = db.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM messages WHERE status IN ('pending','quarantined')").fetchone()
            held = db.execute('SELECT COALESCE(SUM(records),0),COALESCE(SUM(bytes),0) FROM capacity').fetchone()
            retained = db.execute("SELECT COALESCE(SUM(size),0) FROM messages WHERE status IN ('acked','disposed')").fetchone()[0]
            if (pending[0] + held[0] + records > self.config.max_pending_records
                    or pending[1] + held[1] + bytes_required > self.config.max_pending_bytes
                    or retained > self.config.max_pending_bytes * 4):
                raise OutboxFull('durable receipt headroom is exhausted')
            db.execute('INSERT INTO capacity VALUES (?,?,?)', (identifier, records, bytes_required))

    def release_capacity(self, identifier):
        with self.connect() as db:
            db.execute('DELETE FROM capacity WHERE id=?', (identifier,))

    def quarantined_ids(self, origin_id):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT message_id FROM messages WHERE origin_id=? AND status='quarantined'", (origin_id,))]

    def reconcile(self, origin_id: str, response: dict) -> None:
        """ACK is delivery; processed is a separate disposition, never an accounting success."""
        if (response.get('protocol') != 'zfc.control.v1'
                or type(response.get('recovery_epoch')) is not int
                or response.get('streams_truncated') or response.get('dispositions_truncated')):
            raise ValueError('incomplete Controller recovery response')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            stream = db.execute("SELECT value FROM metadata WHERE key='stream_id'").fetchone()[0]
            epoch_key = 'recovery-epoch:' + origin_id
            previous = db.execute('SELECT value FROM metadata WHERE key=?', (epoch_key,)).fetchone()
            epoch = response['recovery_epoch']
            if previous and epoch < int(previous[0]):
                raise ValueError('Controller recovery epoch rollback')
            matching = [row for row in response.get('streams', [])
                        if row.get('stream_id') == stream and row.get('origin_id') == origin_id
                        and row.get('workspace_id') == self.config.workspace_id]
            if len(matching) > 1:
                raise ValueError('duplicate Controller stream watermark')
            watermark = matching[0] if matching else {'contiguous_seq': 0, 'processed_seq': 0}
            received, processed = watermark.get('contiguous_seq'), watermark.get('processed_seq')
            if (type(received) is not int or type(processed) is not int
                    or not 0 <= processed <= received):
                raise ValueError('invalid Controller stream watermark')
            # A restored database can forget any retained receipt. Replay with its original ID.
            db.execute("UPDATE messages SET status='pending' WHERE origin_id=? AND wire_seq>? "
                       "AND status='acked' AND body!=''", (origin_id, received))
            missing = db.execute("SELECT COUNT(*) FROM messages WHERE origin_id=? AND wire_seq>? "
                                 "AND status='acked' AND body=''", (origin_id, received)).fetchone()[0]
            if missing:
                raise ValueError('Controller recovery exceeds local retained receipt coverage')
            for decision in response.get('dispositions', []):
                if decision.get('workspace_id') != self.config.workspace_id:
                    raise ValueError('Controller disposition workspace mismatch')
                action = decision.get('action')
                if action not in {'retry', 'close', 'corrected'}:
                    raise ValueError('unknown Controller receipt disposition')
                db.execute("UPDATE messages SET status=?,reason=? WHERE origin_id=? "
                           "AND message_id=? AND status='quarantined'",
                           ('pending' if action == 'retry' else 'disposed', action, origin_id, decision.get('message_id')))
            db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (epoch_key, str(epoch)))
            db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       ('processed:' + origin_id, str(processed)))

    def prune(self, *, retention_seconds: int = 604800) -> int:
        """Only discard settled payloads past retention; permanent IDs prevent double counting."""
        if retention_seconds < 86400:
            raise ValueError('receipt retention must cover at least one day')
        count = 0
        with self.connect() as db:
            for item in db.execute("SELECT key,value FROM metadata WHERE key LIKE 'processed:%'").fetchall():
                count += db.execute("UPDATE messages SET body='',size=0 WHERE origin_id=? AND wire_seq<=? "
                                    "AND status IN ('acked','disposed') AND created<? AND body!=''",
                                    (item['key'][10:], int(item['value']), time.time() - retention_seconds)).rowcount
        return count
