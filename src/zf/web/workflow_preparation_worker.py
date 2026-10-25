"""Host accepted preparation operations before a project Run/runtime exists."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.workspace.registry import WorkspaceRegistry
from zf.runtime.workflow_synthesis_consumer import consume_workflow_synthesis_operations

log = logging.getLogger(__name__)


class WorkflowPreparationWorker:
    """Bounded host for the existing durable consumer, sharing its process lease."""

    def __init__(self) -> None:
        self._roots: set[Path] = set()
        self._active: set[Path] = set()
        self._scheduled: dict[Path, float] = {}
        self._seen: dict[Path, tuple[int, int]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def watch(self, root: Path) -> None:
        with self._lock:
            self._roots.add(root.resolve())

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="workflow-preparation", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _run(self) -> None:
        while not self._stop.is_set():
            # Kanban/CLI can enqueue work before any Workflow page is opened.
            # Re-read the registry to include projects admitted after startup.
            try:
                for project in WorkspaceRegistry().list_projects():
                    self.watch(Path(project.root))
            except (OSError, ValueError):
                log.exception("Workflow preparation could not read workspace projects")
            with self._lock:
                available = sorted(
                    self._roots - self._active,
                    key=lambda root: self._scheduled.get(root, 0),
                )[: max(0, 2 - len(self._active))]
                self._active.update(available)
                self._scheduled.update({root: time.monotonic() for root in available})
            for root in available:
                threading.Thread(
                    target=self._consume, args=(root,), daemon=True
                ).start()
            self._stop.wait(2)

    def _consume(self, root: Path) -> None:
        try:
            path = root / "zf.yaml"
            if not path.is_file():
                return
            config = load_config(path)
            state = Path(config.project.state_dir)
            if not state.is_absolute():
                state = root / state
            if not (state / "events.jsonl").is_file():
                return
            stat = (state / "events.jsonl").stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if self._seen.get(root) == signature:
                return
            writer = EventWriter(event_log_from_project(state, config=config))
            consume_workflow_synthesis_operations(
                state_dir=state,
                project_root=root,
                config=config,
                writer=writer,
            )
            self._seen[root] = signature
        except TimeoutError:
            pass  # The runtime or another Web process owns the same lease.
        except Exception:
            log.exception("Workflow preparation consumer failed for %s", root)
        finally:
            with self._lock:
                self._active.discard(root)
