"""Coalesce mirror intake after the HTTP refresh response has been sent."""
from __future__ import annotations

import logging
from threading import Lock
from time import sleep

from fastapi import BackgroundTasks

from zf.runtime.external_issue_ingress import ExternalIssueIngressService

_LOG = logging.getLogger(__name__)


class IssueIngressBackground:
    def __init__(self) -> None:
        self._lock = Lock()
        self._pending: dict[str, ExternalIssueIngressService | None] = {}
        self._status: dict[str, dict] = {}

    def status(self, state_dir) -> dict:
        with self._lock:
            return dict(self._status.get(str(state_dir.resolve()), {"status": "idle"}))

    def schedule(self, service: ExternalIssueIngressService, tasks: BackgroundTasks) -> None:
        key = str(service.state_dir.resolve())
        with self._lock:
            active = key in self._pending
            self._pending[key] = service
            self._status[key] = {"status": "processing"}
        if not active:
            tasks.add_task(self._run, key)

    def _run(self, key: str) -> None:
        while True:
            with self._lock:
                service = self._pending[key]
                self._pending[key] = None
            try:
                result = service.process_mirrors()
                if result.status == "syncing":
                    # A poller/manual admission owns the same intake lock.
                    # Keep the latest request and retry after it releases;
                    # contention is not an evidence collection failure.
                    with self._lock:
                        if self._pending[key] is None:
                            self._pending[key] = service
                    sleep(1)
                    continue
                status = {"status": "ready" if result.ok else "failed", "error": result.error}
            except Exception as exc:
                _LOG.exception("Background Issue intake failed")
                status = {"status": "failed", "error": str(exc)[:300]}
            with self._lock:
                # Another refresh arrived during capture: consume its latest mirror.
                if self._pending[key] is not None:
                    continue
                del self._pending[key]
                self._status[key] = status
                return
