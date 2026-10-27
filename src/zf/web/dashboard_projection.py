"""Request-time SQLite reads; serialized background projection updates."""
from __future__ import annotations

import json
import threading
import time
import queue
import logging
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from zf.core.config.schema import ZfConfig

from zf.core.state.locks import locked_path
from zf.web.projections import dashboard_store as store
from zf.web.projections import read_model
from zf.web.projections.sqlite_reader import connect_readonly

_QUEUE: queue.Queue = queue.Queue()
_WORKER: threading.Thread | None = None
_LOCK = threading.Lock()
_JOBS: dict[tuple, object] = {}
_ERRORS: dict[tuple, tuple[float, str]] = {}


class ProjectionPending(Exception):
    def __init__(self, state: dict) -> None:
        self.state = state


class ProjectionSourceChanged(RuntimeError):
    """Keep the previous complete generation when its sources moved."""


def register_pending_handler(app: FastAPI) -> None:
    @app.exception_handler(ProjectionPending)
    async def pending(_request: Request, exc: ProjectionPending) -> JSONResponse:
        return JSONResponse({"read_projection": exc.state}, status_code=503 if exc.state["state"] == "error" else 202,
                            headers={"Retry-After": "1", "Cache-Control": "no-store"})


def _receipt(state_dir: Path) -> dict:
    try:
        with connect_readonly(read_model.db_path(state_dir)) as conn:
            meta = read_model._meta(conn)
        return {"source_seq": int(meta.get("source_seq") or 0),
                "segment": meta.get("tail_segment", ""), "byte_offset": int(meta.get("tail_offset") or 0),
                "manifest_digest": meta.get("manifest_digest", ""), "last_event_id": meta.get("tail_event_id", "")}
    except Exception:
        return {}


def rebuild_group(state_dir: Path, root: Path, config: ZfConfig | None, group: str, build: Callable[[], dict]) -> dict:
    """One owner computes outside the short SQLite commit transaction."""
    lock_path = state_dir / "projections" / f"dashboard-{group}"
    with locked_path(lock_path, timeout_seconds=5):
        scope = store.scope_identity(state_dir, root, config)
        before = store.source_fingerprint(state_dir, root, config, scope=scope)
        trigger = store.quick_fingerprint(state_dir, root, config, scope=scope)
        existing = store.read_group(state_dir, group)
        if existing and existing[1].get("scope") == scope and existing[1]["fingerprint"] == before and existing[1].get("trigger_fingerprint") == trigger and not existing[1]["source_changed"] and time.time() - existing[1]["built_at"] < store.refresh_seconds(group):
            return existing[0]
        agent_group = group.startswith("agent-monitoring:")
        if not agent_group:
            status = read_model.projection_status(state_dir)
            if status["projection_state"] != "ready" or status["tail_behind"]:
                read_model.rebuild(state_dir, config=config)
        # Agents already advances its metric fact cursor; building a second
        # generic event index first doubles the empty-database startup work.
        cursor = {} if agent_group else _receipt(state_dir)
        from zf.web.projections.projection_failures import collect_projection_failures
        with collect_projection_failures() as failures:
            data = build()
        if failures or any(isinstance(value, dict) and value.get("error") for value in data.values()):
            raise ValueError("dashboard source projection failed; retaining the last complete generation")
        after = store.source_fingerprint(state_dir, root, config)
        if before != after:
            raise ProjectionSourceChanged("projection sources changed during build")
        if agent_group:
            cursor = {"source_seq": data["source_seq"], "source_version": data["source_version"]}
        store.write_group(state_dir, group, data, fingerprint=before, cursor=cursor, source_changed=False, scope=scope, trigger=trigger)
        return data


def read_dashboard(state_dir: Path, root: Path, config: ZfConfig | None, group: str, build: Callable[[], dict],
                   *, worker=None, project_id: str = "") -> dict:
    scope = store.scope_identity(state_dir, root, config)
    fingerprint = store.quick_fingerprint(state_dir, root, config, scope=scope)
    existing = store.read_group(state_dir, group)
    if existing and existing[1].get("scope") != scope:
        existing = None
    key = (str(state_dir.resolve()), group)
    fresh = bool(existing and existing[1].get("trigger_fingerprint") == fingerprint and not existing[1]["source_changed"]
                 and time.time() - existing[1]["built_at"] < store.refresh_seconds(group))
    job_state = {}
    if worker is not None:
        from zf.web.projection_worker import ProjectionJob
        if not fresh:
            job_state = worker.request(ProjectionJob(state_dir, root, group, config, project_id), fingerprint)
        else:
            job_state = worker.result(key)
    global _WORKER
    with _LOCK:
        error = ((0, job_state["error"]) if job_state.get("error") else None) if worker else _ERRORS.get(key)
        if fresh:
            error = None
        if worker is None and not fresh and key not in _JOBS and (not error or time.monotonic() - error[0] >= 5):
            _JOBS[key] = True
            _QUEUE.put((key, state_dir, root, config, group, build))
            if _WORKER is None or not _WORKER.is_alive():
                _WORKER = threading.Thread(target=_run_jobs, name="zf-dashboard-projector", daemon=True)
                _WORKER.start()
    meta = existing[1] if existing else {}
    state = {"state": "ready" if fresh else "error" if error else "refreshing" if existing else "building",
             "source": "read_model.sqlite", "version": store.VERSION,
             "generated_at_epoch": meta.get("built_at"), "source_cursor": json.loads(meta.get("cursor", "{}")),
             "source_changed_during_build": bool(meta.get("source_changed")),
             "error": error[1] if error else None}
    if worker is not None:
        state["worker"] = {key: value for key, value in job_state.items() if key in {"wait_ms", "build_ms", "queue_full"}}
    if existing is None:
        raise ProjectionPending(state)
    return {**existing[0], "read_projection": state}


def snapshot(state_dir: Path, *, slice_name: str, config: ZfConfig | None, project_root: Path, worker=None) -> dict:
    from zf.web import server
    return read_dashboard(state_dir, project_root, config, slice_name,
                          lambda: server._snapshot_slice(state_dir, slice_name=slice_name, config=config,
                                                         project_root=project_root, web_session_token=None), worker=worker)


def _run_jobs() -> None:
    while True:
        key, state_dir, root, config, group, build = _QUEUE.get()
        try:
            rebuild_group(state_dir, root, config, group, build)
            with _LOCK:
                _ERRORS.pop(key, None)
        except ProjectionSourceChanged:
            pass  # The next read retries; no mixed generation was published.
        except Exception:
            logging.getLogger(__name__).exception("Dashboard projection %s failed", group)
            with _LOCK:
                _ERRORS[key] = (time.monotonic(), "投影更新失败，请查看服务日志后重试")
        finally:
            with _LOCK:
                _JOBS.pop(key, None)
            _QUEUE.task_done()
