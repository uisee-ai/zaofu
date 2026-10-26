"""Task status/column facts, derived through the canonical TaskStore helpers."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.task.store import TaskStore
from zf.core.task.kanban_projection import kanban_column_projection
from zf.web.projections.metric_facts import writer, meta, put_meta, load_meta
from zf.web.projections.sqlite_reader import connect_readonly
from zf.web.projections.read_model import db_path
from zf.core.state.locks import locked_path


def fingerprint(state_dir: Path) -> list:
    paths = [state_dir / "kanban.json"] + sorted((state_dir / "kanban").glob("*.json"))
    return [[str(p), p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns] for p in paths if p.exists()]


def refresh(state_dir: Path) -> None:
    if load_meta(state_dir, "tasks") == fingerprint(state_dir): return
    with locked_path(state_dir / "projections" / "metric-tasks"), writer(state_dir) as conn:
        conn.executescript("""CREATE TABLE IF NOT EXISTS metric_task (
            id TEXT PRIMARY KEY, ordinal INTEGER, active INTEGER, status TEXT,
            board_column TEXT, verified INTEGER, payload TEXT
        );
        CREATE INDEX IF NOT EXISTS metric_task_status ON metric_task(active, status);
        CREATE INDEX IF NOT EXISTS metric_task_column ON metric_task(active, board_column);
        """)
        before = fingerprint(state_dir)
        if meta(conn, "tasks") == before: return
        store = TaskStore(state_dir / "kanban.json")
        active_ids = {task.id for task in store.list_all()}
        rows = []
        for i, task in enumerate(store.list_all_with_archive()):
            data = {"id": task.id, "status": task.status, "assigned_to": task.assigned_to,
                    "created_at": task.created_at, "dispatched_at": task.dispatched_at,
                    "blocked_by": task.blocked_by,
                    "owner_role": getattr(task.contract, "owner_role", "") if task.contract else ""}
            rows.append((task.id, i, int(task.id in active_ids), task.status,
                         kanban_column_projection(task).column, int(task.evidence is not None), json.dumps(data)))
        old = {row["id"]: tuple(row) for row in conn.execute("SELECT * FROM metric_task")}
        changed = [row for row in rows if old.get(row[0]) != row]
        removed = old.keys() - {row[0] for row in rows}
        with conn:
            conn.executemany("DELETE FROM metric_task WHERE id=?", [(key,) for key in removed])
            conn.executemany("INSERT OR REPLACE INTO metric_task VALUES (?,?,?,?,?,?,?)", changed)
            put_meta(conn, "tasks", before)


def rows(state_dir: Path, *, active: bool = True) -> list:
    with connect_readonly(db_path(state_dir)) as conn:
        selected = conn.execute("SELECT * FROM metric_task" + (" WHERE active=1" if active else "") + " ORDER BY ordinal").fetchall()
    result = []
    for row in selected:
        data = json.loads(row["payload"])
        data["contract"] = SimpleNamespace(owner_role=data.pop("owner_role"))
        data["_metric_column"] = row["board_column"]
        result.append(SimpleNamespace(**data))
    return result


def counts(state_dir: Path) -> dict:
    with connect_readonly(db_path(state_dir)) as conn:
        return {row[0]: row[1] for row in conn.execute("SELECT board_column, COUNT(*) FROM metric_task WHERE active=1 GROUP BY board_column")}


def core_inputs(state_dir: Path) -> tuple[dict, list]:
    with connect_readonly(db_path(state_dir)) as conn:
        stats = conn.execute("""SELECT COALESCE(SUM(status='done'),0) AS done,
            COALESCE(SUM(status IN ('review','testing','done')),0) AS attempted,
            COALESCE(SUM(status='done' AND verified=1),0) AS verified FROM metric_task""").fetchone()
        totals = conn.execute("SELECT COUNT(*), COALESCE(SUM(json_extract(payload,'$.count')),0) FROM metric_task_trace").fetchone()
        traces = conn.execute("""SELECT t.payload FROM metric_task_trace t JOIN metric_task k ON k.id=t.task_id
            WHERE k.status='done' ORDER BY k.ordinal""").fetchall()
    return {**dict(stats), "trace_tasks": totals[0], "trace_events": totals[1]}, [json.loads(row[0]) for row in traces]
