"""Process-local Issue workers backed by cross-process execution locks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import wraps
from pathlib import Path
import threading
import time
from typing import Any

from issue_core import SessionStatus
from zf.core.state.locks import FileLock


@dataclass
class SessionExecution:
    host: Any
    session_id: str
    thread: threading.Thread
    cancel: threading.Event


_LOCK = threading.RLock()
_RUNS: dict[tuple[Path, str], SessionExecution] = {}
_STOPPING: set[str] = set()


def admitted(method):
    """Fence state preparation and scheduling together during host shutdown."""
    @wraps(method)
    def wrapped(host, *args, **kwargs):
        with _LOCK:
            if host.execution_owner in _STOPPING:
                raise ValueError("Issue 执行宿主正在关闭，请稍后恢复")
            return method(host, *args, **kwargs)
    return wrapped


def _key(host: Any, session_id: str) -> tuple[Path, str]:
    # Validate before using the Session ID in a lock path.
    host.store._session_path(session_id)
    return host.state_dir.resolve(), session_id


def _lease(host: Any, session_id: str) -> FileLock:
    state_dir, sid = _key(host, session_id)
    return FileLock(state_dir / "self-issues/portable-sessions" / f"{sid}.execution.lock", timeout_seconds=0)


def _provider_running(host: Any, session_id: str) -> bool:
    # Also protect workers started by an older host without an execution lease.
    from zf.web.headless_agent import HeadlessThreadStore
    store = HeadlessThreadStore(state_dir=host.state_dir, project_root=host.project_root)
    thread_id = store.thread_id(scope="project", thread_key=f"self-issue-session:{session_id}")
    try:
        with FileLock(store.thread_dir / f"{thread_id}.lock", timeout_seconds=0):
            return False
    except TimeoutError:
        return True


def running(host: Any, session_id: str) -> bool:
    with _LOCK:
        if _key(host, session_id) in _RUNS:
            return True
    try:
        with _lease(host, session_id):
            return _provider_running(host, session_id)
    except TimeoutError:
        return True


def cancellation(host: Any, session_id: str) -> threading.Event | None:
    with _LOCK:
        run = _RUNS.get(_key(host, session_id))
        return run.cancel if run else None


def start(host: Any, session_id: str, *, automatic: bool = False) -> dict[str, Any]:
    with _LOCK:
        if host.execution_owner in _STOPPING:
            raise ValueError("Issue 执行宿主正在关闭，请稍后恢复")
        key = _key(host, session_id)
        if key in _RUNS:
            return host.get(session_id)
        lease = _lease(host, session_id)
        try:
            lease.__enter__()
        except TimeoutError:
            return host.get(session_id)
        try:
            if _provider_running(host, session_id):
                lease.__exit__(None, None, None)
                return host.get(session_id)
            with host.store.transaction(session_id):
                session = host.store.load_session(session_id)
                if session.status not in {SessionStatus.CREATED, SessionStatus.INVESTIGATING,
                                          SessionStatus.PAUSED, SessionStatus.FAILED}:
                    lease.__exit__(None, None, None)
                    return host.get(session_id)
                working = host.store.load_working_set(session_id)
                if automatic and (
                    working.workflow.get("execution_owner") != host.execution_owner
                    or session.status == SessionStatus.FAILED
                    or (session.status == SessionStatus.PAUSED and not working.workflow.get("resume_after_restart"))
                ):
                    lease.__exit__(None, None, None)
                    return host.get(session_id)
                if session.status != SessionStatus.INVESTIGATING or working.workflow.get("phase") != "queued":
                    host._mark_investigating(session_id)
                    working = host.store.load_working_set(session_id)
                host.store.save_working_set(replace(working, workflow={
                    **working.workflow, "execution_owner": host.execution_owner,
                    "resume_after_restart": False,
                }), expected_revision=working.revision)

            def run() -> None:
                try:
                    host._run_background(session_id)
                finally:
                    with _LOCK:
                        _RUNS.pop(key, None)
                        lease.__exit__(None, None, None)

            thread = threading.Thread(target=run, daemon=True, name=f"zf-issue-{session_id}")
            _RUNS[key] = SessionExecution(host, session_id, thread, threading.Event())
            try:
                thread.start()
            except Exception as exc:
                host._mark_failed(session_id, exc)
                raise
        except BaseException:
            _RUNS.pop(key, None)
            lease.__exit__(None, None, None)
            raise
    return host.get(session_id)


def open_owner(owner: str) -> None:
    with _LOCK:
        _STOPPING.discard(owner)


def stop_owner(owner: str) -> list[SessionExecution]:
    """Fence new starts before the bridge drains outstanding dispatch futures."""
    with _LOCK:
        _STOPPING.add(owner)
        return [run for run in _RUNS.values() if run.host.execution_owner == owner]


def join_runs(runs: list[SessionExecution], *, timeout: float = 10.0) -> list[str]:
    deadline = time.monotonic() + timeout
    for run in runs:
        run.thread.join(timeout=max(0, deadline - time.monotonic()))
    return [run.session_id for run in runs if run.thread.is_alive()]
