"""Rebuildable mailbox locators. All returned facts are decoded from their canonical ledger byte ranges."""

from __future__ import annotations

from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import sqlite3

from zf.core.events.append_witness import arm_witness, read_witness, signature
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.state.rotation import list_archives
from zf.runtime.call_result_envelope import canonical_json_sha256 as digest

# These may arrive between the authority read and append. None declares an
# operation/contract, lease, coordination turn, lifecycle transition or input.
OBSERVATIONS = frozenset(
    {
        "worker.heartbeat",
        "agent.text",
        "agent.thinking",
        "agent.tool.started",
        "agent.tool.completed",
        "codex.hook.pre_tool_use",
        "codex.hook.post_tool_use",
        "claude.hook.pre_tool_use",
        "claude.hook.post_tool_use",
    }
)
MAIL = "workflow.mailbox."
SCHEMA = 2


def _labels(value):
    if not isinstance(value, dict):
        return set()
    return {
        kind + ":" + value[k]
        for kind, k in [
            ("actor", "actor"),
            ("actor", "role"),
            ("op", "operation_id"),
            ("version", "version"),
            ("advisory", "assistance_request_id"),
        ]
        if isinstance(value.get(k), str) and value[k]
    }


def _add(db, file, pos, length, e):
    if e.type in OBSERVATIONS:
        return
    p = e.payload
    run = p.get("workflow_run_id") or p.get("run_id") or e.correlation_id or ""
    # Request approvals may precede selection of a distinct Run ID. All
    # proposal receipts are candidates; roster() still joins the exact request.
    if e.type.startswith("workflow.request.") or e.type in {
        "loop.pause_requested",
        "loop.resume_requested",
        "runtime.maintenance.entered",
        "runtime.maintenance.exited",
        "dispatch.paused",
        "dispatch.resumed",
        "event.malformed",
    }:
        run = ""
    is_mail = e.type.startswith(MAIL)
    if is_mail and (e.origin != "kernel" or e.actor != "durable-mailbox"):
        return
    run = run if isinstance(run, str) else ""
    op = p.get("operation_id") if isinstance(p.get("operation_id"), str) else ""
    key = p.get("message_key") if isinstance(p.get("message_key"), str) else ""
    db.execute("INSERT INTO fact VALUES(?,?,?,?,?,?,?)", (file, pos, length, run, is_mail, op, key))
    if not is_mail:
        return
    labels = _labels(p.get("sender")) | _labels(p.get("sender_address")) | _labels(p.get("consumer"))
    for addr in [*p.get("recipients", ()), p.get("recipient"), p.get("target")]:
        labels |= _labels(addr)
    if p.get("owner_instance"):
        labels.add("actor:" + p["owner_instance"])
    if p.get("question_id"):
        labels.add("advisory:" + p["question_id"])
    keys = {key} if key else set()
    keys.update(r["message_key"] for r in p.get("items", ()))
    db.executemany("INSERT INTO label VALUES(?,?,?)", ((file, pos, i) for i in sorted(labels)))
    db.executemany("INSERT INTO thread VALUES(?,?,?)", ((file, pos, i) for i in sorted(keys)))

    # The scheduler needs only outstanding deliveries. This index is a
    # candidate filter; actions still fold the selected canonical facts.
    states = []
    kind = e.type[len(MAIL) :]
    if kind == "accepted":
        states = [
            (key, digest(addr), "question" if p["kind"] == "question" else "notice")
            for addr in p["recipients"]
        ]
    elif kind == "acknowledged":
        states = [(row["message_key"], digest(row["recipient"]), "acked") for row in p["items"]]
    elif kind == "disposition" and p.get("status") in {"cancelled", "timed_out"}:
        states = [(key, p["recipient_digest"], "revoked")]
    elif kind == "question" and p["action"] in {"answer", "cancel"}:
        states = [(key, "", "closed")]
    elif kind == "question" and p["action"] == "handoff":
        states = [(key, digest(p["target"]), "question")]
    db.executemany(
        "INSERT INTO eligibility VALUES(?,?,?,?,?)", ((file, run, k, rid, state) for k, rid, state in states)
    )


def _open_rows(db):
    # A question remains open after ACK. A handoff to another approved version
    # is a new recipient binding, so revocation of the old version is local.
    return list(
        db.execute("""
        WITH pending AS (
          SELECT DISTINCT a.run,a.k FROM eligibility a WHERE a.state IN ('question','notice')
          AND NOT EXISTS(SELECT 1 FROM eligibility b WHERE b.k=a.k AND b.state='closed')
          AND NOT EXISTS(SELECT 1 FROM eligibility b WHERE b.k=a.k AND b.rid=a.rid
                      AND (b.state='revoked' OR (a.state='notice' AND b.state='acked'))))
        SELECT f.file,f.pos,f.n FROM fact f WHERE f.mail=0 AND
            (f.run IN (SELECT run FROM pending) OR (f.run='' AND EXISTS(SELECT 1 FROM pending)))
        UNION SELECT f.file,f.pos,f.n FROM pending p JOIN thread t ON p.k=t.value
            JOIN fact f ON (f.file,f.pos)=(t.file,t.pos) AND f.run=p.run""")
    )


def _schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS segment(path TEXT PRIMARY KEY,stamp TEXT,consumed INTEGER);
        CREATE TABLE IF NOT EXISTS fact(file TEXT,pos INTEGER,n INTEGER,run TEXT,mail INTEGER,op TEXT,k TEXT,
            PRIMARY KEY(file,pos)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS by_run ON fact(mail,run);
        CREATE INDEX IF NOT EXISTS by_op ON fact(op);
        CREATE TABLE IF NOT EXISTS label(file TEXT,pos INTEGER,value TEXT,PRIMARY KEY(value,file,pos)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS thread(file TEXT,pos INTEGER,value TEXT,PRIMARY KEY(value,file,pos)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS thread_fact ON thread(file,pos);
        CREATE TABLE IF NOT EXISTS eligibility(file TEXT,run TEXT,k TEXT,rid TEXT,state TEXT);
        CREATE INDEX IF NOT EXISTS eligibility_key ON eligibility(k,state,rid);
    """)


def _scan(db, log, path, name, old):
    stamp = signature(path)
    oldstamp, consumed = old or (None, 0)
    if stamp == oldstamp:
        return False
    proof = read_witness(log.path) if path == log.path else {}
    if oldstamp and proof.get("base") == oldstamp and proof.get("head") == stamp:
        start = consumed
    else:
        # Includes unknown growth, in-place changes, archive relocation and
        # old unsigned cache entries. No guessed byte boundary grants access.
        for table, col in [("fact", "file"), ("label", "file"), ("thread", "file"), ("eligibility", "file")]:
            db.execute("DELETE FROM " + table + " WHERE " + col + "=?", (name,))
        start = 0
    with path.open("rb") as stream:
        stream.seek(start)
        while line := stream.readline():
            if not line.endswith(b"\n"):
                break
            e = log.decode_line(line.decode("utf-8", "replace").strip()) if line.strip() else None
            if e:
                _add(db, name, start, len(line), e)
            start += len(line)
    if signature(path) != stamp:
        raise ValueError("mailbox ledger changed during indexing")
    db.execute("INSERT OR REPLACE INTO segment VALUES(?,?,?)", (name, json.dumps(stamp), start))
    return True


def _read_rows(db, log, identity, run, versions, all_mail):
    if not run and identity and identity.get("operation_id"):
        runs = {
            r[0]
            for r in db.execute(
                "SELECT DISTINCT run FROM fact WHERE op=? AND mail=0", (identity["operation_id"],)
            )
            if r[0]
        }
        if len(runs) == 1:
            run = runs.pop()
        else:
            raise ValueError("mailbox operation has no unique Run")
    if not run and identity and identity.get("assistance_request_id"):
        advisory = "advisory:" + identity["assistance_request_id"]
        actor = "actor:" + identity["actor"]
        runs = {
            r[0]
            for r in db.execute(
                """
            SELECT DISTINCT f.run FROM label l JOIN label a ON (a.file,a.pos)=(l.file,l.pos)
            JOIN fact f ON (f.file,f.pos)=(a.file,a.pos)
            WHERE l.value=? AND a.value=? AND f.mail=1""",
                (advisory, actor),
            )
            if r[0]
        }
        if len(runs) != 1:
            raise ValueError("advisory has no unique authorized mailbox Run")
        run = runs.pop()
    scope = "1" if not run else "f.run IN (?,'')"
    params = () if not run else (run,)
    if all_mail or versions is None:
        clause = scope if all_mail else scope + " AND f.mail=0"
        rows = db.execute("SELECT f.file,f.pos,f.n FROM fact f WHERE " + clause, params)
    else:
        labels = sorted(_labels(identity) | {"version:" + v for v in versions})
        db.execute("CREATE TEMP TABLE IF NOT EXISTS wanted(value TEXT PRIMARY KEY)")
        db.execute("DELETE FROM wanted")
        db.executemany("INSERT INTO wanted VALUES(?)", ((s,) for s in labels))
        rows = db.execute(
            """
          WITH matched AS (SELECT DISTINCT l.file,l.pos FROM label l JOIN wanted w ON w.value=l.value),
          keys AS (SELECT DISTINCT t.value FROM matched m JOIN thread t ON (t.file,t.pos)=(m.file,m.pos)),
          selected AS (SELECT file,pos FROM matched UNION SELECT t.file,t.pos FROM keys JOIN thread t USING(value))
          SELECT f.file,f.pos,f.n FROM selected s JOIN fact f ON (s.file,s.pos)=(f.file,f.pos)
          WHERE """
            + scope,
            params,
        )
    return run, list(rows)


class MailboxIndex:
    """Acquire one checked segment inventory; read governance, then only the consumer's threads."""

    def __init__(self, runtime):
        self.log = runtime.event_log
        self.state = Path(runtime.state_dir)

    def read(self, identity=None, *, select_versions=None, all_mail=False, open_only=False):
        log = self.log
        root = self.state / "projections/durable-mailbox"
        root.mkdir(parents=True, exist_ok=True)
        dbpath = root / "locators.sqlite"
        private = self.state / "private/durable-mailbox-index"
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        anchor = private / "cache.json"
        signer = getattr(log.signer, "cache_fingerprint", lambda: "unsigned")()
        with locked_path(log.path):
            try:
                seal = json.loads(anchor.read_text())
            except (OSError, ValueError):
                seal = {}
            # A mutable DB cannot supply authority. A mismatched DB or a changed
            # signing policy invalidates all locators, including negative lookup.
            if (
                seal.get("db") != signature(dbpath)
                or seal.get("version") != SCHEMA
                or seal.get("signer") != signer
                or seal.get("unsigned") != log.allow_unsigned
            ):
                dbpath.unlink(missing_ok=True)
            fresh = not dbpath.exists()
            with closing(sqlite3.connect(dbpath)) as db:
                if fresh:
                    _schema(db)
                    os.chmod(dbpath, 0o600)
                known = {
                    path: (json.loads(stamp), consumed)
                    for path, stamp, consumed in db.execute("SELECT * FROM segment")
                }
                paths = [*list_archives(log._archive_dir, suffix=".jsonl")]
                if log.path.exists():
                    paths.append(log.path)
                names = [str(p.relative_to(self.state)) for p in paths]
                changed = fresh
                for removed in set(known) - set(names):
                    for table, col in [
                        ("fact", "file"),
                        ("label", "file"),
                        ("thread", "file"),
                        ("segment", "path"),
                        ("eligibility", "file"),
                    ]:
                        db.execute("DELETE FROM " + table + " WHERE " + col + "=?", (removed,))
                    changed = True
                for name, path in zip(names, paths):
                    changed |= _scan(db, log, path, name, known.get(name))
                if changed:
                    db.commit()
                    atomic_write_text(
                        anchor,
                        json.dumps(
                            {
                                "version": SCHEMA,
                                "db": signature(dbpath),
                                "signer": signer,
                                "unsigned": log.allow_unsigned,
                            }
                        ),
                    )
                    os.chmod(anchor, 0o600)
                    arm_witness(log.path, signature(log.path))
                order = {name: i for i, name in enumerate(names)}
                handles = {}
                with ExitStack() as stack:

                    def hydrate(locations):
                        selected = []
                        for file, pos, length in sorted(
                            set(locations), key=lambda row: (order[row[0]], row[1])
                        ):
                            if file not in handles:
                                handles[file] = stack.enter_context((self.state / file).open("rb"))
                            handle = handles[file]
                            handle.seek(pos)
                            row = log.decode_line(handle.read(length).decode("utf-8", "replace").strip())
                            if row is None or row.type == "event.malformed":
                                raise ValueError("mailbox authority ledger unavailable")
                            selected.append(((order[file], pos), row))
                        return selected

                    if open_only:
                        self.offset = log.current_offset()
                        return [row for _, row in hydrate(_open_rows(db))]
                    run, locations = _read_rows(
                        db, log, identity, identity.get("workflow_run_id") if identity else "", None, all_mail
                    )
                    facts = hydrate(locations)
                    if select_versions is not None:
                        versions = select_versions(run, [event for _, event in facts])
                        _, locations = _read_rows(db, log, identity, run, versions, False)
                        facts.extend(hydrate(locations))
                    self.offset = log.current_offset()
                    return [event for _, event in sorted(facts, key=lambda row: row[0])]
