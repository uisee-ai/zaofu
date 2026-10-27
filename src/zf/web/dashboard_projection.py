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
        before = store.source_fingerprint(state_dir, root, config)
        trigger = store.quick_fingerprint(state_dir, root, config)
        existing = store.read_group(state_dir, group)
        if existing and existing[1].get("scope") == store.scope_identity(state_dir, root, config) and existing[1]["fingerprint"] == before and existing[1].get("trigger_fingerprint") == trigger and not existing[1]["source_changed"] and time.time() - existing[1]["built_at"] < store.REFRESH_SECONDS:
            return existing[0]
        status = read_model.projection_status(state_dir)
        if status["projection_state"] != "ready" or status["tail_behind"]:
            read_model.rebuild(state_dir, config=config)
        # Capture the indexed byte watermark before the semantic builders run.
        cursor = _receipt(state_dir)
        from zf.web.projections.projection_failures import collect_projection_failures
        with collect_projection_failures() as failures:
            data = build()
        if failures or any(isinstance(value, dict) and value.get("error") for value in data.values()):
            raise ValueError("dashboard source projection failed; retaining the last complete generation")
        after = store.source_fingerprint(state_dir, root, config)
        store.write_group(state_dir, group, data, fingerprint=before, cursor=cursor, source_changed=before != after, scope=store.scope_identity(state_dir, root, config), trigger=trigger)
        return data


def read_dashboard(state_dir: Path, root: Path, config: ZfConfig | None, group: str, build: Callable[[], dict]) -> dict:
    fingerprint = store.quick_fingerprint(state_dir, root, config)
    existing = store.read_group(state_dir, group)
    if existing and existing[1].get("scope") != store.scope_identity(state_dir, root, config):
        existing = None
    key = (str(state_dir.resolve()), group)
    fresh = bool(existing and existing[1].get("trigger_fingerprint") == fingerprint and not existing[1]["source_changed"]
                 and time.time() - existing[1]["built_at"] < store.REFRESH_SECONDS)
    global _WORKER
    with _LOCK:
        error = _ERRORS.get(key)
        if not fresh and key not in _JOBS and (not error or time.monotonic() - error[0] >= 5):
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
    if existing is None:
        raise ProjectionPending(state)
    return {**existing[0], "read_projection": state}


def snapshot(state_dir: Path, *, slice_name: str, config: ZfConfig | None, project_root: Path) -> dict:
    from zf.web import server
    return read_dashboard(state_dir, project_root, config, slice_name,
                          lambda: server._snapshot_slice(state_dir, slice_name=slice_name, config=config,
                                                         project_root=project_root, web_session_token=None))


def _run_jobs() -> None:
    while True:
        key, state_dir, root, config, group, build = _QUEUE.get()
        try:
            rebuild_group(state_dir, root, config, group, build)
            with _LOCK:
                _ERRORS.pop(key, None)
        except Exception:
            logging.getLogger(__name__).exception("Dashboard projection %s failed", group)
            with _LOCK:
                _ERRORS[key] = (time.monotonic(), "投影更新失败，请查看服务日志后重试")
        finally:
            with _LOCK:
                _JOBS.pop(key, None)
            _QUEUE.task_done()
