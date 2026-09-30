"""S2 multiprocess conformance for the append-only EventLog boundary."""

from __future__ import annotations

import multiprocessing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence
from zf.core.state.locks import locked_path
from zf.core.security.signing import EventSigner
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def _append_events(path_text: str, worker: int, count: int) -> None:
    log = EventLog(Path(path_text))
    for index in range(count):
        log.append(ZfEvent(
            id=f"mp-{worker}-{index}",
            type="worker.heartbeat",
            actor=f"worker-{worker}",
            payload={"worker": worker, "index": index},
        ))
    log.close()


def _read_signed_cache_during_rewrite(
    path_text: str,
    cached: object,
    rewritten: object,
    release_writer: object,
    result_queue: object,
) -> None:
    """Keep a reader cache alive while another process rewrites the file."""
    try:
        log = EventLog(
            Path(path_text),
            signer=EventSigner(b"s2-cross-process-race-secret"),
        )
        before = log.read_all()
        cached.set()  # type: ignore[attr-defined]
        if not rewritten.wait(timeout=15):  # type: ignore[attr-defined]
            raise TimeoutError("writer did not publish the rewrite")
        # The writer still owns the advisory lock here.  read_all() must not
        # return a cached pre-tamper event just because size/mtime are stable.
        after = log.read_all()
        result_queue.put({  # type: ignore[attr-defined]
            "before": [event.id for event in before],
            "after": [event.id for event in after],
            "after_types": [event.type for event in after],
            "error": "",
        })
    except BaseException as exc:  # pragma: no cover - parent reports details
        result_queue.put({"before": [], "after": [], "after_types": [], "error": repr(exc)})  # type: ignore[attr-defined]
    finally:
        release_writer.set()  # type: ignore[attr-defined]


def _rewrite_signed_while_reader_is_cached(
    path_text: str,
    cached: object,
    rewritten: object,
    release_writer: object,
) -> None:
    """Rewrite signed bytes under the shared lock and hold it for the read."""
    path = Path(path_text)
    if not cached.wait(timeout=15):  # type: ignore[attr-defined]
        raise TimeoutError("reader did not establish its cache")
    raw = path.read_text(encoding="utf-8")
    tampered = raw.replace("true", "null", 1)
    if len(tampered) != len(raw):
        raise AssertionError("tamper fixture must preserve file size")
    stat = path.stat()
    with locked_path(path):
        path.write_text(tampered, encoding="utf-8")
        os.utime(path, (stat.st_atime, stat.st_mtime))
        rewritten.set()  # type: ignore[attr-defined]
        if not release_writer.wait(timeout=15):  # type: ignore[attr-defined]
            raise TimeoutError("reader did not finish while rewrite lock held")


def _update_task_in_process(
    store_path_text: str,
    task_id: str,
    barrier: object,
    result_queue: object,
) -> None:
    try:
        barrier.wait(timeout=15)  # type: ignore[attr-defined]
        updated = TaskStore(Path(store_path_text)).update(
            task_id,
            status="in_progress",
            blocked_reason=f"updated-by-{task_id}",
        )
        result_queue.put({  # type: ignore[attr-defined]
            "task_id": task_id,
            "updated": updated is not None,
            "error": "",
        })
    except BaseException as exc:  # pragma: no cover - parent reports details
        result_queue.put({"task_id": task_id, "updated": False, "error": repr(exc)})  # type: ignore[attr-defined]


def _crash_update_task_before_replace(
    store_path_text: str,
    task_id: str,
    barrier: object,
) -> None:
    """Crash after writing a store temp file while another writer competes."""
    from zf.core.state import atomic_io

    barrier.wait(timeout=15)  # type: ignore[attr-defined]
    atomic_io.os.replace = lambda _src, _dst: os._exit(73)
    TaskStore(Path(store_path_text)).update(task_id, status="in_progress")


def _read_while_appending(
    path_text: str,
    barrier: object,
    result_queue: object,
    reader_id: int,
) -> None:
    """Poll an EventLog while independent writers append complete rows."""
    try:
        log = EventLog(Path(path_text))
        barrier.wait(timeout=20)  # type: ignore[attr-defined]
        snapshots: list[list[str]] = []
        for _ in range(80):
            events = log.read_all()
            ids = [event.id for event in events]
            if any(event.type == "event.malformed" for event in events):
                raise AssertionError(f"reader {reader_id} observed malformed row")
            if len(ids) != len(set(ids)):
                raise AssertionError(f"reader {reader_id} observed duplicate ids")
            snapshots.append(ids)
            time.sleep(0.001)
        result_queue.put({"reader_id": reader_id, "snapshots": snapshots, "error": ""})  # type: ignore[attr-defined]
    except BaseException as exc:  # pragma: no cover - parent reports details
        result_queue.put({"reader_id": reader_id, "snapshots": [], "error": repr(exc)})  # type: ignore[attr-defined]


def _append_events_with_barrier(
    path_text: str,
    worker: int,
    count: int,
    barrier: object,
) -> None:
    """Synchronize writers with readers before appending a short burst."""
    log = EventLog(Path(path_text))
    barrier.wait(timeout=20)  # type: ignore[attr-defined]
    for index in range(count):
        log.append(ZfEvent(
            id=f"pressure-{worker}-{index}",
            type="worker.heartbeat",
            actor=f"pressure-worker-{worker}",
            payload={"worker": worker, "index": index},
        ))
        time.sleep(0.002)
    log.close()


def test_multiprocess_event_writers_preserve_complete_unique_log(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    workers = 4
    per_worker = 8
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_append_events,
            args=(str(path), worker, per_worker),
        )
        for worker in range(workers)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert all(not process.is_alive() for process in processes)
    assert [process.exitcode for process in processes] == [0] * workers
    events = EventLog(path).read_all()
    assert len(events) == workers * per_worker
    assert {event.id for event in events} == {
        f"mp-{worker}-{index}"
        for worker in range(workers)
        for index in range(per_worker)
    }
    assert all(event.type == "worker.heartbeat" for event in events)


def test_concurrent_readers_observe_only_complete_unique_event_rows(
    tmp_path: Path,
) -> None:
    """Readers must not surface partial or duplicated rows during appends."""
    path = tmp_path / "events-reader-pressure.jsonl"
    context = multiprocessing.get_context("spawn")
    writer_count = 2
    reader_count = 2
    per_writer = 20
    barrier = context.Barrier(writer_count + reader_count)
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_append_events_with_barrier,
            args=(str(path), worker, per_writer, barrier),
        )
        for worker in range(writer_count)
    ] + [
        context.Process(
            target=_read_while_appending,
            args=(str(path), barrier, result_queue, reader_id),
        )
        for reader_id in range(reader_count)
    ]
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join(timeout=30)
        assert all(not process.is_alive() for process in processes)
        assert [process.exitcode for process in processes] == [0] * len(processes)
        results = [result_queue.get(timeout=3) for _ in range(reader_count)]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()

    assert all(not result["error"] for result in results), results
    events = EventLog(path).read_all()
    assert len(events) == writer_count * per_writer
    assert len({event.id for event in events}) == len(events)


def test_multiprocess_size_rotation_keeps_archives_replayable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events-rotated.jsonl"
    monkeypatch.setenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", "1")
    workers = 3
    per_worker = 4
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_append_events,
            args=(str(path), worker, per_worker),
        )
        for worker in range(workers)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)

    assert [process.exitcode for process in processes] == [0] * workers
    archives = sorted((tmp_path / "events-rotated").glob("*.jsonl"))
    assert archives
    events = EventLog(path).read_all()
    assert len(events) == workers * per_worker
    assert len({event.id for event in events}) == workers * per_worker


def test_date_rotation_collision_merges_active_content_for_replay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events-date.jsonl"
    archive_dir = tmp_path / "events-date"
    old_day = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    archive_dir.mkdir()
    (archive_dir / f"{old_day}.jsonl").write_text(
        ZfEvent(type="archived", id="evt-archived").to_json() + "\n",
        encoding="utf-8",
    )
    path.write_text(
        ZfEvent(type="active-old", id="evt-active-old").to_json() + "\n",
        encoding="utf-8",
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(path, (old_timestamp, old_timestamp))

    EventLog(path).append(ZfEvent(type="active-new", id="evt-active-new"))

    assert path.exists()
    events = EventLog(path).read_all()
    assert [event.id for event in events] == [
        "evt-archived",
        "evt-active-old",
        "evt-active-new",
    ]


def test_four_process_date_rotation_preserves_every_event(
    tmp_path: Path,
) -> None:
    """Several writers rotating the same old active file must not lose rows."""
    path = tmp_path / "events-date-mp.jsonl"
    seed = EventLog(path)
    seed.append(ZfEvent(type="seed", id="evt-seed"))
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(path, (old_timestamp, old_timestamp))
    seed.close()

    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_append_events, args=(str(path), worker, 3))
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert [process.exitcode for process in processes] == [0] * len(processes)
    events = EventLog(path).read_all()
    assert len(events) == 1 + 4 * 3
    assert {event.id for event in events} == {
        "evt-seed",
        *(f"mp-{worker}-{index}" for worker in range(4) for index in range(3)),
    }


def test_fresh_process_rejects_signed_tamper_after_prior_reader_exit(
    tmp_path: Path,
) -> None:
    """A new reader must verify the bytes, independent of another process cache."""
    path = tmp_path / "signed-cross-process.jsonl"
    signer = EventSigner(b"s2-cross-process-secret")
    log = EventLog(path, signer=signer)
    log.append(ZfEvent(type="task.created", payload={"safe": True}))
    raw = path.read_text(encoding="utf-8")
    repo_root = Path(__file__).resolve().parents[2]
    reader = (
        "from pathlib import Path; "
        "from zf.core.events.log import EventLog; "
        "from zf.core.security.signing import EventSigner; "
        "import sys; "
        "print(len(EventLog(Path(sys.argv[1]), "
        "signer=EventSigner(b's2-cross-process-secret')).read_all()))"
    )
    first = subprocess.run(
        [sys.executable, "-c", reader, str(path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0
    assert first.stdout.strip() == "1"

    tampered = raw.replace("true", "null")
    assert len(tampered) == len(raw)
    path.write_text(tampered, encoding="utf-8")
    second = subprocess.run(
        [sys.executable, "-c", reader, str(path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0
    assert second.stdout.strip() == "0"


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-001: signed EventLog fold cache can mask cross-process rewrite",
)
def test_signed_reader_rejects_same_size_rewrite_while_writer_holds_lock(
    tmp_path: Path,
) -> None:
    """A live reader must reverify bytes changed by another process."""
    path = tmp_path / "signed-cross-process-race.jsonl"
    signer = EventSigner(b"s2-cross-process-race-secret")
    EventLog(path, signer=signer).append(
        ZfEvent(type="task.created", payload={"safe": True}),
    )

    context = multiprocessing.get_context("spawn")
    cached = context.Event()
    rewritten = context.Event()
    release_writer = context.Event()
    result_queue = context.Queue()
    reader = context.Process(
        target=_read_signed_cache_during_rewrite,
        args=(str(path), cached, rewritten, release_writer, result_queue),
    )
    writer = context.Process(
        target=_rewrite_signed_while_reader_is_cached,
        args=(str(path), cached, rewritten, release_writer),
    )
    reader.start()
    writer.start()
    try:
        reader.join(timeout=20)
        writer.join(timeout=20)
        assert not reader.is_alive() and not writer.is_alive()
        assert reader.exitcode == 0
        assert writer.exitcode == 0
        result = result_queue.get(timeout=2)
    finally:
        release_writer.set()
        for process in (reader, writer):
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()

    assert not result["error"], result
    assert result["before"]
    # A tampered signed line may be dropped or surfaced as event.malformed, but
    # the cached valid task event must never survive the rewrite.
    assert result["after"] == [] or result["after_types"] == ["event.malformed"]


def test_multiprocess_task_store_updates_preserve_every_task(
    tmp_path: Path,
) -> None:
    """Concurrent atomic updates must not overwrite another task's change."""
    path = tmp_path / "kanban.json"
    task_ids = [f"TASK-S2-STORE-{index}" for index in range(8)]
    TaskStore(path).add_many([
        Task(id=task_id, title=task_id, status="backlog")
        for task_id in task_ids
    ])

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(len(task_ids))
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_update_task_in_process,
            args=(str(path), task_id, barrier, result_queue),
        )
        for task_id in task_ids
    ]
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join(timeout=20)
        assert [process.exitcode for process in processes] == [0] * len(processes)
        results = [result_queue.get(timeout=2) for _ in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()

    assert all(result["updated"] and not result["error"] for result in results)
    tasks = {task.id: task for task in TaskStore(path).list_all()}
    assert set(tasks) == set(task_ids)
    assert all(task.status == "in_progress" for task in tasks.values())
    assert {
        task.blocked_reason for task in tasks.values()
    } == {f"updated-by-{task_id}" for task_id in task_ids}


def test_task_store_subprocess_crash_before_replace_preserves_snapshot(
    tmp_path: Path,
) -> None:
    """A process dying at the atomic replace seam leaves recoverable state."""
    path = tmp_path / "kanban-crash.json"
    TaskStore(path).add(Task(id="TASK-S2-CRASH", title="crash recovery"))

    # Use a fresh interpreter so the process really dies between writing the
    # temporary file and replacing the canonical snapshot.  This models the
    # narrow crash window that an in-process mock cannot exercise.
    script = (
        "import os, sys; "
        "from pathlib import Path; "
        "from zf.core.state import atomic_io; "
        "from zf.core.task.store import TaskStore; "
        "atomic_io.os.replace = lambda _src, _dst: os._exit(73); "
        "TaskStore(Path(sys.argv[1])).update("
        "'TASK-S2-CRASH', status='in_progress')"
    )
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert crashed.returncode == 73
    # The old canonical snapshot remains readable and is not half-written.
    recovered = TaskStore(path).get("TASK-S2-CRASH")
    assert recovered is not None
    assert recovered.status == "backlog"
    # A hard process exit can leave the temporary write behind.  Recovery code
    # must tolerate it; remove this crash residue before the normal retry.
    orphans = list(tmp_path.glob("kanban-crash.json.*.tmp"))
    assert orphans
    for orphan in orphans:
        orphan.unlink()

    retried = TaskStore(path).update("TASK-S2-CRASH", status="in_progress")
    assert retried is not None
    assert retried.status == "in_progress"


def test_task_store_concurrent_update_survives_one_crashing_writer(
    tmp_path: Path,
) -> None:
    """One hard-exiting writer must not corrupt a concurrent writer's update."""
    path = tmp_path / "kanban-concurrent-crash.json"
    crashing_id = "TASK-S2-CRASHING-WRITER"
    healthy_id = "TASK-S2-HEALTHY-WRITER"
    TaskStore(path).add_many([
        Task(id=crashing_id, title="crashing writer"),
        Task(id=healthy_id, title="healthy writer"),
    ])

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    crashing = context.Process(
        target=_crash_update_task_before_replace,
        args=(str(path), crashing_id, barrier),
    )
    healthy = context.Process(
        target=_update_task_in_process,
        args=(str(path), healthy_id, barrier, result_queue),
    )
    for process in (crashing, healthy):
        process.start()
    try:
        for process in (crashing, healthy):
            process.join(timeout=20)
        assert not crashing.is_alive() and not healthy.is_alive()
        assert crashing.exitcode == 73
        assert healthy.exitcode == 0
        result = result_queue.get(timeout=2)
    finally:
        for process in (crashing, healthy):
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        result_queue.close()
        result_queue.join_thread()

    assert result == {"task_id": healthy_id, "updated": True, "error": ""}
    store = TaskStore(path)
    assert store.get(crashing_id).status == "backlog"
    assert store.get(healthy_id).status == "in_progress"
    orphans = list(tmp_path.glob("kanban-concurrent-crash.json.*.tmp"))
    assert orphans
    for orphan in orphans:
        orphan.unlink()

    retried = store.update(crashing_id, status="in_progress")
    assert retried is not None and retried.status == "in_progress"
