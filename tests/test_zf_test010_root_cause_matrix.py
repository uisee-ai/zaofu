"""ZF-TEST-010 diagnostic matrix.

These tests intentionally do not change the read-model implementation.  They
record the ownership/timing boundaries that can explain a WAL initialisation
lock, while keeping the original strict-xfail regression as the failure
detector for the production symptom.
"""

from __future__ import annotations

import multiprocessing as mp
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web.projections import read_model
from zf.web.server import create_app


def _connect_read_model_child(db_path: str, start_gate, result_queue) -> None:
    """Open the same projection from an independent process at one barrier."""

    start_gate.wait()
    try:
        connection = read_model._connect(Path(db_path))
    except BaseException as exc:  # pragma: no cover - exercised in child process
        result_queue.put({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    else:
        connection.close()
        result_queue.put({"status": "ok"})


def _connect_with_phase_child(db_path: str, start_gate, result_queue) -> None:
    """Run the production connection-init sequence and report its phase."""

    start_gate.wait()
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = None
    phase = "connect"
    try:
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
        phase = "journal_mode_wal"
        connection.execute("PRAGMA journal_mode=WAL")
        phase = "synchronous"
        connection.execute("PRAGMA synchronous=NORMAL")
        phase = "busy_timeout"
        connection.execute("PRAGMA busy_timeout=30000")
    except BaseException as exc:  # pragma: no cover - exercised in child process
        result_queue.put({
            "status": "error",
            "phase": phase,
            "error": f"{type(exc).__name__}: {exc}",
        })
    else:
        result_queue.put({"status": "ok", "phase": "complete"})
    finally:
        if connection is not None:
            connection.close()


def _seed(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "kanban.json").write_text("[]", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="loop.started", actor="test"))
    log.append(
        ZfEvent(
            type="channel.created",
            actor="test",
            payload={"channel_id": "ch-rootcause", "name": "Root cause"},
        )
    )


def test_zf_test010_records_web_prewarm_and_explicit_rebuild_overlap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The Web startup prewarm can open the same SQLite projection as rebuild.

    This is a timing diagnostic, not a production-behaviour assertion: the
    test holds the prewarm connection open briefly, starts the explicit
    rebuild, and proves both operations target the same database concurrently.
    """

    state_dir = tmp_path / ".zf"
    _seed(state_dir)
    original_connect = read_model._connect
    prewarm_entered = threading.Event()
    release_prewarm = threading.Event()
    overlap: dict[str, object] = {}

    def instrumented_connect(path: Path):
        connection = original_connect(path)
        if threading.current_thread().name == "zf-web-prewarm" and not prewarm_entered.is_set():
            overlap["path"] = str(path)
            overlap["prewarm_thread"] = threading.current_thread().name
            prewarm_entered.set()
            release_prewarm.wait(5)
        return connection

    monkeypatch.setattr(read_model, "_connect", instrumented_connect)
    app = create_app(state_dir, project_root=tmp_path)
    client = TestClient(app)
    try:
        assert prewarm_entered.wait(5), "Web prewarm did not reach read-model _connect"
        rebuild_started = threading.Event()
        rebuild_finished = threading.Event()

        def explicit_rebuild() -> None:
            rebuild_started.set()
            read_model.rebuild(state_dir)
            rebuild_finished.set()

        worker = threading.Thread(target=explicit_rebuild, name="explicit-rebuild")
        worker.start()
        assert rebuild_started.wait(1)
        time.sleep(0.05)
        overlap["explicit_rebuild_alive_while_prewarm_connection_open"] = worker.is_alive()
        release_prewarm.set()
        worker.join(10)
        assert rebuild_finished.is_set()
        assert overlap["explicit_rebuild_alive_while_prewarm_connection_open"] is True
        assert overlap["prewarm_thread"] == "zf-web-prewarm"
        assert str(read_model.db_path(state_dir)) == overlap["path"]
    finally:
        client.close()


def test_zf_test010_concurrent_rebuilds_are_serialized_by_state_lock(tmp_path: Path) -> None:
    """Two explicit rebuild callers are a control case for the same state."""

    state_dir = tmp_path / ".zf"
    _seed(state_dir)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def rebuild() -> None:
        try:
            barrier.wait()
            read_model.rebuild(state_dir)
        except BaseException as exc:  # pragma: no cover - diagnostic guard
            errors.append(exc)

    workers = [threading.Thread(target=rebuild, name=f"rebuild-{i}") for i in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert not errors


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-010: concurrent independent WAL initialization must not raise database locked",
)
def test_zf_test010_independent_wal_initializers_fail_closed_without_lock_errors(
    tmp_path: Path,
) -> None:
    """The smallest cross-process WAL initialization race must be observable.

    This is intentionally an xfail detector rather than a production workaround:
    eight independent readers open one new projection at the same instant.  The
    assertion requires every initializer to succeed; current SQLite/WAL setup
    reliably exposes the same ``database is locked`` boundary seen in ZF-TEST-010.
    """

    context = mp.get_context("fork")
    worker_count = 8
    errors: list[dict[str, object]] = []
    for round_no in range(5):
        state_dir = tmp_path / f"round-{round_no}" / ".zf"
        state_dir.mkdir(parents=True)
        db_path = read_model.db_path(state_dir)
        start_gate = context.Barrier(worker_count)
        result_queue = context.Queue()
        workers = [
            context.Process(
                target=_connect_read_model_child,
                args=(str(db_path), start_gate, result_queue),
                name=f"read-model-wal-init-{round_no}-{index}",
            )
            for index in range(worker_count)
        ]
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(20)
            assert all(not worker.is_alive() for worker in workers), "WAL initializer hung"
            results = [result_queue.get(timeout=2) for _ in workers]
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.kill()
                worker.join(5)
        errors.extend(
            {"round": round_no, **item}
            for item in results
            if item.get("status") != "ok"
        )

    assert not errors, f"independent WAL initializers failed: {errors}"


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-010: WAL initializer lock phase must not fail",
)
def test_zf_test010_wal_initializer_lock_phase_is_explicit(tmp_path: Path) -> None:
    """Keep the WAL failure phase observable instead of a generic init error."""

    context = mp.get_context("fork")
    errors: list[dict[str, object]] = []
    for round_no in range(3):
        state_dir = tmp_path / f"phase-round-{round_no}" / ".zf"
        state_dir.mkdir(parents=True)
        gate = context.Barrier(8)
        queue = context.Queue()
        workers = [
            context.Process(
                target=_connect_with_phase_child,
                args=(str(read_model.db_path(state_dir)), gate, queue),
            )
            for _ in range(8)
        ]
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(20)
            assert all(not worker.is_alive() for worker in workers), "WAL phase probe hung"
            results = [queue.get(timeout=2) for _ in workers]
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.kill()
                worker.join(5)
        errors.extend(
            {"round": round_no, **result}
            for result in results
            if result.get("status") != "ok"
        )

    assert not errors, f"WAL initializer phase failures: {errors}"


def test_zf_test010_background_catchup_and_explicit_rebuild_control(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The catch-up scheduler and a caller rebuild share one isolated state."""

    state_dir = tmp_path / ".zf"
    _seed(state_dir)
    read_model.rebuild(state_dir)
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(
            type="channel.message.posted",
            actor="test",
            payload={
                "channel_id": "ch-rootcause",
                "message_id": "tail",
                "thread_id": "main",
                "text": "tail",
            },
        )
    )
    monkeypatch.setattr(read_model, "_CATCH_UP_MIN_INTERVAL_S", 0.0)
    read_model.request_catch_up(state_dir)
    key = str(read_model.db_path(state_dir).resolve())
    for _ in range(100):
        job = read_model._JOBS.get(key)
        if job is None or not job.is_alive():
            break
        time.sleep(0.01)
    else:  # pragma: no cover - diagnostic timeout
        raise AssertionError("background catch-up did not settle")
    result = read_model.rebuild(state_dir)
    assert result["projection_state"] == "ready"
    assert read_model.projection_status(state_dir)["projected_seq"] >= 3
