"""Durable Connector obligations, never a replacement for Kernel business state."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time


class Ledger:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / 'installation' / 'control.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS obligations (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                  body TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(name TEXT PRIMARY KEY, value INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS native_usage ON obligations(json_extract(body,'$.native_session_id'));
                CREATE INDEX IF NOT EXISTS actor_usage ON obligations(json_extract(body,'$.actor'));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def insert(self, identifier, kind, status, body):
        with self.connect() as db:
            return bool(db.execute('INSERT OR IGNORE INTO obligations VALUES (?,?,?,?,?)',
                                   (identifier, kind, status, json.dumps(body), time.time())).rowcount)

    def admit_execution(self, identifier, body):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT body FROM obligations WHERE kind='execution' AND status IN ('preparing','running')"):
                previous = json.loads(row[0])
                if previous['project_id'] == body['project_id'] and any(
                    body.get(key) and body.get(key) == previous.get(key)
                    for key in ('session_id', 'actor', 'parent_operation_id')
                ):
                    from zf.integrations.controller.binding import ManagedDenied
                    raise ManagedDenied('previous native execution is still unresolved')
            db.execute('INSERT INTO obligations VALUES (?,?,?,?,?)',
                       (identifier, 'execution', 'preparing', json.dumps(body), time.time()))

    def put(self, identifier, status, body):
        with self.connect() as db:
            if db.execute('UPDATE obligations SET status=?,body=?,updated=? WHERE id=?',
                          (status, json.dumps(body), time.time(), identifier)).rowcount != 1:
                raise ValueError('missing Connector obligation')

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM obligations WHERE id=?', (identifier,)).fetchone()
        return self.decode(row) if row else None

    def rows(self, kind):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM obligations WHERE kind=? ORDER BY updated', (kind,)).fetchall()
        return [self.decode(row) for row in rows]

    def next_version(self, name):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO versions VALUES (?,1) ON CONFLICT(name) DO UPDATE SET value=value+1', (name,))
            return db.execute('SELECT value FROM versions WHERE name=?', (name,)).fetchone()[0]

    def covers_usage(self, project, actor, native, occurred):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM obligations WHERE kind='execution' "
                "AND json_extract(body,'$.project_id')=? AND json_extract(body,'$.started_at')<=? "
                "AND COALESCE(json_extract(body,'$.finished_at'),?)>=? "
                "AND ((?!='' AND json_extract(body,'$.actor')=?) OR (?!='' AND json_extract(body,'$.native_session_id')=?)) LIMIT 1",
                (project, occurred, occurred, occurred, actor or '', actor or '', native or '', native or '')).fetchone() is not None

    def owns_terminal(self, project, session):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM obligations WHERE kind='execution' AND json_extract(body,'$.source')='terminal' "
                "AND json_extract(body,'$.project_id')=? AND json_extract(body,'$.session_id')=? LIMIT 1", (project, session)).fetchone() is not None

    @staticmethod
    def decode(row):
        return {**dict(row), 'body': json.loads(row['body'])}
