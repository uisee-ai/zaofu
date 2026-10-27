"""One bounded, Web-owned process for disposable read projections.

Only registered read jobs cross the pipe. The child never creates an ASGI app,
starts a runtime, or returns projection bodies over IPC. SQLite owns results.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import logging
import multiprocessing
import threading
import time
from pathlib import Path

from zf.core.config.schema import ZfConfig


@dataclass(frozen=True)
class ProjectionJob:
    state_dir: Path
    root: Path
    group: str
    config: ZfConfig | None = None
    project_id: str = ""
    trigger: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return str(self.state_dir.resolve()), self.group


def _serve(connection) -> None:
    from zf.web.dashboard_projection import ProjectionSourceChanged, rebuild_group
    from zf.web.projection_jobs import build_job
    from zf.web.projections.dashboard_store import quick_fingerprint
    try:
        while (job := connection.recv()) is not None:
            started = time.monotonic()
            try:
                if job.trigger and quick_fingerprint(job.state_dir, job.root, job.config) != job.trigger:
                    raise ProjectionSourceChanged("queued source became obsolete")
                if job.group == "board":
                    cards_job = replace(job, group="board-cards")
                    rebuild_group(job.state_dir, job.root, job.config, cards_job.group,
                                  lambda: build_job(cards_job))
                rebuild_group(job.state_dir, job.root, job.config, job.group,
                              lambda: build_job(job))
                result = {"state": "ready"}
            except ProjectionSourceChanged:
                result = {"state": "refreshing"}
            except Exception:
                logging.getLogger(__name__).exception("Projection %s failed", job.group)
                result = {"state": "error", "error": "投影更新失败，请查看服务日志后重试"}
            connection.send({**result, "build_ms": round((time.monotonic() - started) * 1000, 2)})
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class ProjectionWorker:
    """Coalesce by resource; bounded admission, recovery and shutdown.

    Config is the already resolved read-only snapshot, never a closure or an
    alternate control plane. It is serialized by the coordinator, not by HTTP.
    """

    def __init__(self, *, capacity: int = 16, timeout_seconds: float = 120) -> None:
        self.capacity = capacity
        self.timeout_seconds = timeout_seconds
        self._condition = threading.Condition()
        self._pending: OrderedDict[tuple, tuple[ProjectionJob, str, float]] = OrderedDict()
        self._active: tuple[tuple, str] | None = None
        self._results: OrderedDict[tuple, dict] = OrderedDict()
        self._thread: threading.Thread | None = None
        self._process = None
        self._pid: int | None = None
        self._connection = None
        self._closed = False

    @property
    def started(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._closed

    def start(self) -> None:
        with self._condition:
            if self.started:
                return
            self._closed = False
            self._thread = threading.Thread(target=self._run, name="zf-projection-coordinator", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def request(self, job: ProjectionJob, source: str) -> dict:
        job = replace(job, trigger=source)
        key = job.key
        with self._condition:
            if not self.started:
                return {"state": "error", "error": "投影计算进程尚未启动"}
            last = self._results.get(key, {})
            # Limit retries without keeping a failed source stuck forever.
            if last.get("state") == "error" and time.monotonic() - last.get("finished", 0) < 1:
                return dict(last)
            if self._active == (key, source):
                return {"state": "refreshing"}
            if key in self._pending:
                _, _, queued = self._pending[key]
                self._pending[key] = job, source, queued
            elif len(self._pending) < self.capacity:
                self._pending[key] = job, source, time.monotonic()
            else:
                return {"state": "refreshing", "queue_full": True}
            self._condition.notify_all()
            return {"state": "refreshing"}

    def status(self) -> dict:
        with self._condition:
            return {"started": self.started, "pid": self._pid,
                    "queued": len(self._pending), "running": self._active is not None,
                    "capacity": self.capacity}

    def result(self, key: tuple) -> dict:
        with self._condition:
            return dict(self._results.get(key, {}))

    def _disconnect(self) -> None:
        self._pid = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._process is not None:
            process = self._process
            self._process = None
            if process.pid is not None:
                process.join(timeout=0.2)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)
            process.close()

    def _execute(self, job: ProjectionJob) -> dict:
        if self._process is None or not self._process.is_alive():
            self._disconnect()
            context = multiprocessing.get_context("spawn")
            self._connection, child = context.Pipe()
            self._process = context.Process(target=_serve, args=(child,), name="zf-projection-worker", daemon=True)
            try:
                self._process.start()
            finally:
                child.close()
            self._pid = self._process.pid
        self._connection.send(job)
        deadline = time.monotonic() + self.timeout_seconds
        while not self._closed:
            if self._connection.poll(0.1):
                return self._connection.recv()
            if not self._process.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("projection worker exited or timed out")
        return {"state": "refreshing"}

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending)
                    if self._closed:
                        return
                    key, (job, source, queued) = self._pending.popitem(last=False)
                    self._active = key, source
                wait_ms = round((time.monotonic() - queued) * 1000, 2)
                try:
                    result = self._execute(job)
                except Exception:
                    logging.getLogger(__name__).exception("Projection process unavailable")
                    self._disconnect()
                    result = {"state": "error", "error": "投影计算进程中断，正在等待重试"}
                with self._condition:
                    self._results[key] = {**result, "wait_ms": wait_ms, "finished": time.monotonic()}
                    self._results.move_to_end(key)
                    while len(self._results) > self.capacity * 4:
                        self._results.popitem(last=False)
                    self._active = None
        finally:
            self._disconnect()


def register_projection_worker(app) -> None:
    worker = ProjectionWorker()
    app.state.projection_worker = worker
    app.router.add_event_handler("startup", worker.start)
    app.router.add_event_handler("shutdown", worker.stop)


def active_worker(app) -> ProjectionWorker | None:
    worker = getattr(app.state, "projection_worker", None)
    return worker if worker is not None and worker.started else None
