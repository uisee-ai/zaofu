"""Session state store — manages .zf/session.yaml."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.git_state import GitState
from zf.core.state.locks import locked_path


class ZfNotInitialized(Exception):
    pass


@dataclass
class WorkerState:
    role: str = ""
    state: str = "idle"  # idle | working | refreshing | blocked | crashed | stopping
    session_id: str = ""
    turn_count: int = 0
    consecutive_failures: int = 0
    last_dispatch: str = ""  # task id
    git_state: GitState = field(default_factory=GitState)


@dataclass
class SessionState:
    session_id: str = ""
    started_at: str = ""
    project_root: str = ""
    runtime_state: str = "created"
    latest_event_offset: int = 0
    workers: list[dict] = field(default_factory=list)


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _locked(self):
        return locked_path(self.path)

    def create(self, project_root: str) -> SessionState:
        with self._locked():
            state = SessionState(
                session_id=f"sess-{uuid.uuid4().hex[:8]}",
                started_at=datetime.now(timezone.utc).isoformat(),
                project_root=project_root,
            )
            self._save(state)
            return state

    def load(self) -> SessionState:
        if not self.path.exists():
            raise ZfNotInitialized(f"Session not initialized: {self.path} not found")
        data = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not data:
            raise ZfNotInitialized("Session file is empty")
        return SessionState(**data)

    def update(self, **kwargs) -> SessionState:
        with self._locked():
            state = self.load()
            for key, value in kwargs.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            self._save(state)
            return state

    def _save(self, state: SessionState) -> None:
        atomic_write_text(
            self.path,
            yaml.dump(asdict(state), default_flow_style=False, allow_unicode=True),
        )

    # -- worker state helpers --

    def upsert_worker(self, worker: WorkerState) -> SessionState:
        with self._locked():
            state = self.load()
            new_data = asdict(worker)
            for i, w in enumerate(state.workers):
                if w.get("role") == worker.role:
                    state.workers[i] = new_data
                    break
            else:
                state.workers.append(new_data)
            self._save(state)
            return state

    def get_worker(self, role: str) -> WorkerState | None:
        try:
            state = self.load()
        except ZfNotInitialized:
            return None
        for w in state.workers:
            if w.get("role") == role:
                return _worker_from_dict(w)
        return None


def _worker_from_dict(d: dict) -> WorkerState:
    git_data = d.get("git_state") or {}
    return WorkerState(
        role=d.get("role", ""),
        state=d.get("state", "idle"),
        session_id=d.get("session_id", ""),
        turn_count=d.get("turn_count", 0),
        consecutive_failures=d.get("consecutive_failures", 0),
        last_dispatch=d.get("last_dispatch", ""),
        git_state=GitState(
            branch=git_data.get("branch"),
            head=git_data.get("head"),
            dirty_files=git_data.get("dirty_files", []),
            last_commit_msg=git_data.get("last_commit_msg", ""),
            ts=git_data.get("ts", ""),
        ),
    )
