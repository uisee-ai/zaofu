"""TaskStore — kanban.json backed task CRUD with terminal-state archival.

Layout:

    .zf/kanban.json                   ← active tasks only (non-terminal)
    .zf/kanban/<YYYY-MM-DD>.json      ← tasks that reached terminal state that day
    .zf/kanban-terminal-index.json    ← {task_id: "YYYY-MM-DD"} for fast lookup

Terminal states (``done``, ``cancelled``) leave the active board immediately
on transition — the active kanban.json stays small and interesting, while
historical tasks stay reachable for audit, progress.md, and blocked_by
resolution via the terminal index.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.state.rotation import list_archives
from zf.core.task.schema import Task, TaskContract, TaskEvidence


TERMINAL_STATES = {"done", "cancelled"}


class TaskStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    # ---- layout helpers ----

    @property
    def _archive_dir(self) -> Path:
        return self.path.parent / self.path.stem

    @property
    def _index_path(self) -> Path:
        return self.path.parent / f"{self.path.stem}-terminal-index.json"

    def _locked(self):
        return locked_path(self.path)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _archive_file(self, date: str) -> Path:
        return self._archive_dir / f"{date}.json"

    # ---- active file I/O ----

    def _load_raw(self) -> list[dict]:
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else []
        return data if isinstance(data, list) else []

    def _save_raw(self, tasks: list[dict]) -> None:
        atomic_write_text(
            self.path,
            json.dumps(tasks, ensure_ascii=False, indent=2) + "\n",
        )

    # ---- archive file I/O ----

    def _load_archive_file(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else []
        return data if isinstance(data, list) else []

    def _append_archive(self, date: str, record: dict) -> None:
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        path = self._archive_file(date)
        existing = self._load_archive_file(path)
        existing.append(record)
        atomic_write_text(
            path,
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        )

    # ---- terminal index I/O ----

    def _load_terminal_index(self) -> dict[str, str]:
        path = self._index_path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save_terminal_index(self, index: dict[str, str]) -> None:
        atomic_write_text(
            self._index_path,
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        )

    def rebuild_terminal_index_from_archive(self) -> dict[str, str]:
        """K4(I1 可执行性):terminal index 全量重建自 kanban 归档。

        index 是纯查询辅助(task_id → 归档日期);本方法证明它可丢弃
        重建:扫 kanban/<YYYY-MM-DD>.json 归档,逐任务回填,原子重写。
        """
        index: dict[str, str] = {}
        archive_dir = self._archive_dir
        if archive_dir.exists():
            for path in sorted(archive_dir.glob("*.json")):
                date = path.stem
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                records = data.get("tasks", data) if isinstance(data, dict) else data
                if isinstance(records, dict):
                    ids = records.keys()
                elif isinstance(records, list):
                    ids = (
                        str(r.get("id") or r.get("task_id") or "")
                        for r in records if isinstance(r, dict)
                    )
                else:
                    continue
                for task_id in ids:
                    if task_id:
                        index[str(task_id)] = date
        self._save_terminal_index(index)
        return index

    def _mark_terminal(self, task_id: str, date: str) -> None:
        index = self._load_terminal_index()
        index[task_id] = date
        self._save_terminal_index(index)

    # ---- record → Task ----

    def _to_task(self, data: dict) -> Task:
        data = dict(data)
        contract_data = data.pop("contract", None) or {}
        evidence_data = data.pop("evidence", None)
        contract = TaskContract(**contract_data) if contract_data else TaskContract()
        evidence = TaskEvidence(**evidence_data) if evidence_data else None
        return Task(**data, contract=contract, evidence=evidence)

    # ---- public API ----

    def list_all(self) -> list[Task]:
        """Active (non-terminal) tasks only. Archived tasks are not included."""
        return [self._to_task(d) for d in self._load_raw()]

    def list_all_with_archive(self, *, last_days: int | None = None) -> list[Task]:
        """Active tasks plus archived terminal tasks.

        ``last_days`` limits how far back archive files are read. ``None``
        means read every archive file. ``last_days=1`` means today + the
        most recent archive day (today's archive is usually the only one
        with today's date).
        """
        tasks = [self._to_task(d) for d in self._load_raw()]
        archives: list[Path]
        if last_days is None:
            archives = list_archives(self._archive_dir, suffix=".json")
        else:
            archives = list_archives(
                self._archive_dir,
                last_days=last_days,
                suffix=".json",
            )
        for f in archives:
            for d in self._load_archive_file(f):
                tasks.append(self._to_task(d))
        return tasks

    def get(self, task_id: str) -> Task | None:
        """Look up by id. Falls back to archive scan for terminal tasks."""
        for d in self._load_raw():
            if d.get("id") == task_id:
                return self._to_task(d)
        index = self._load_terminal_index()
        date = index.get(task_id)
        if date is not None:
            for d in self._load_archive_file(self._archive_file(date)):
                if d.get("id") == task_id:
                    return self._to_task(d)
        # Final fallback: scan every archive file (handles orphaned records
        # whose index entry was lost).
        for f in list_archives(self._archive_dir, suffix=".json"):
            for d in self._load_archive_file(f):
                if d.get("id") == task_id:
                    return self._to_task(d)
        return None

    def add(self, task: Task) -> Task:
        with self._locked():
            raw = self._load_raw()
            existing_ids = {d["id"] for d in raw}
            terminal_ids = set(self._load_terminal_index().keys())
            known_ids = existing_ids | terminal_ids
            for ref in task.blocked_by:
                if ref not in known_ids:
                    raise ValueError(f"blocked_by reference not found: {ref}")
            raw.append(asdict(task))
            self._save_raw(raw)
        return task

    def reopen(self, task: Task) -> Task:
        """Put a previously terminal task back on the active board.

        The archived record is kept for audit, but the terminal index entry is
        removed so dependency resolution no longer treats this task as done.
        """
        if task.status in TERMINAL_STATES:
            raise ValueError("reopened task must not be terminal")
        with self._locked():
            raw = self._load_raw()
            record = asdict(task)
            for i, existing in enumerate(raw):
                if existing.get("id") == task.id:
                    raw[i] = record
                    break
            else:
                raw.append(record)
            self._save_raw(raw)
            index = self._load_terminal_index()
            if task.id in index:
                index.pop(task.id, None)
                self._save_terminal_index(index)
        return task

    def update(self, task_id: str, **kwargs) -> Task | None:
        from dataclasses import asdict, fields
        with self._locked():
            raw = self._load_raw()
            for i, d in enumerate(raw):
                if d["id"] == task_id:
                    for key, value in kwargs.items():
                        if hasattr(value, "__dataclass_fields__"):
                            value = asdict(value)
                        d[key] = value
                    new_status = d.get("status")
                    if new_status in TERMINAL_STATES:
                        today = self._today()
                        # P1-1 (2026-07-09): archive + terminal-index BEFORE
                        # removing from active. The old order (pop → save_raw
                        # first) meant a crash between the active-delete and the
                        # archive-write left the task in *neither* active nor the
                        # terminal index → its blocked_by dependents never resolved
                        # (ready() checks both sets) → silent deadlock. Archiving
                        # first means a crash leaves a harmless active+archive
                        # duplicate (both paths mark it terminal), self-healing on
                        # the next save, instead of a vanished task.
                        self._append_archive(today, d)
                        self._mark_terminal(task_id, today)
                        raw.pop(i)
                        self._save_raw(raw)
                    else:
                        self._save_raw(raw)
                    return self._to_task(dict(d))
        return None

    def ready(self) -> list[Task]:
        """Backlog tasks whose entire blocked_by chain is resolved.

        Resolution uses active tasks (done/cancelled in-place) plus the
        terminal index (archived tasks). This lets us keep kanban.json
        small without losing dependency resolution for follow-up tasks.
        """
        tasks = self.list_all()
        terminal_active = {
            t.id for t in tasks if t.status in TERMINAL_STATES
        }
        terminal_archived = set(self._load_terminal_index().keys())
        terminal = terminal_active | terminal_archived
        return [
            t for t in tasks
            if t.status == "backlog" and all(b in terminal for b in t.blocked_by)
        ]

    def filter(self, *, status: str | None = None) -> list[Task]:
        """Filter active tasks by status.

        For terminal states (done/cancelled) this returns an empty list
        from the active file — use ``list_all_with_archive`` to see them.
        """
        tasks = self.list_all()
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    def ensure(self, key: str, title: str, **kwargs) -> Task:
        with self._locked():
            raw = self._load_raw()
            for d in raw:
                if d.get("key") == key:
                    d["title"] = title
                    for k, v in kwargs.items():
                        if is_dataclass(v):
                            v = asdict(v)
                        d[k] = v
                    self._save_raw(raw)
                    return self._to_task(dict(d))
            task = Task(title=title, key=key, **kwargs)
            raw.append(asdict(task))
            self._save_raw(raw)
            return task
