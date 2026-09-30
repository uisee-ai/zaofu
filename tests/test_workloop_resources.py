"""Prove the host probe detects an actual held lock and live FD/socket changes."""

import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tracemalloc

import pytest

from tests.testkit.workloop_resources import (
    assert_locks_released, assert_workers_exited, observe_process, observe_tree_bytes,
    observe_python_allocations, observe_tcp_sockets,
)
from zf.core.events import EventLog, EventWriter, ZfEvent


def test_workloop_probe_observes_and_releases_owned_descriptors(tmp_path):
    before = observe_process(os.getpid())
    with socket.socket() as sock, (tmp_path / "probe.lock").open("wb") as lock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        during = observe_process(os.getpid())
        assert during["fds"] >= before["fds"] + 2
        assert len(during["socket_inodes"]) == len(before["socket_inodes"]) + 1
        assert during["rss_kib"] > 0
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(AssertionError, match="still held"):
            assert_locks_released(tmp_path)
    assert assert_locks_released(tmp_path) == ["probe.lock"]
    after = observe_process(os.getpid())
    assert after["fds"] == before["fds"]
    assert after["socket_inodes"] == before["socket_inodes"]


def test_workloop_probe_missing_process_is_not_zero_measurement():
    with pytest.raises(FileNotFoundError):
        observe_process(-1)


def test_workloop_probe_attributes_owned_state_bytes(tmp_path):
    (tmp_path / "events.jsonl").write_bytes(b"event\n")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "body.json").write_bytes(b"{}")
    (tmp_path / "ignored.tmp").write_bytes(b"must not count")
    (tmp_path / "link").symlink_to(tmp_path / "events.jsonl")
    observed = observe_tree_bytes(tmp_path)
    assert observed["files"] == 2
    assert observed["bytes"] == 8
    assert observed["by_top_level"] == {"artifacts": 2, "events.jsonl": 6}


def test_workloop_tcp_probe_distinguishes_listener_and_connections():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        [row] = [r for r in observe_tcp_sockets(os.getpid())
                 if int(r["local"].split(":")[1], 16) == port]
        assert row["state"] == "0A"
        with socket.create_connection(("127.0.0.1", port)) as client:
            peer, _ = listener.accept()
            with peer:
                rows = [r for r in observe_tcp_sockets(os.getpid())
                        if int(r["local"].split(":")[1], 16) == port]
                assert {r["state"] for r in rows} == {"0A", "01"}
                assert client.fileno() >= 0
    assert not {r["inode"] for r in rows}.intersection(
        r["inode"] for r in observe_tcp_sockets(os.getpid())
    )


def test_workloop_probe_observes_bounded_python_allocations_and_event_caches(tmp_path):
    tracemalloc.start()
    try:
        log = EventLog(tmp_path / "events.jsonl")
        writer = EventWriter(log)
        for index in range(12):
            writer.append(ZfEvent(type="probe.event", actor="testkit", payload={"index": index}))
        assert len(log.read_all()) == 12
        observed = observe_python_allocations(tmp_path, limit=4)
        assert observed["current_bytes"] > 0
        assert observed["peak_bytes"] >= observed["current_bytes"]
        assert observed["event_log_read_cache_entries"] >= 1
        assert observed["event_log_fold_entries"] >= 1
        assert observed["event_index_file_cache_entries"] >= 0
        assert observed["cached_event_references"] >= 12
        assert observed["tracer_overhead_bytes"] > 0
        assert observed["observation_seconds"] > 0
        assert sum(observed["by_allocation_domain_bytes"].values()) > 0
        assert 0 < len(observed["top"]) <= 4
        isolated = observe_python_allocations(tmp_path / "other-state")
        assert isolated["event_log_read_cache_entries"] == 0
        assert isolated["event_log_fold_entries"] == 0
        assert isolated["cached_index_event_dicts"] == 0
        retained = bytearray(1024 * 1024)
        grown = observe_python_allocations(tmp_path)
        assert grown["current_bytes"] >= isolated["current_bytes"] + len(retained)
        del retained
        released = observe_python_allocations(tmp_path)
        assert released["current_bytes"] < grown["current_bytes"] - 512 * 1024
        with pytest.raises(ValueError, match="limit"):
            observe_python_allocations(tmp_path, limit=-1)
    finally:
        tracemalloc.stop()


def test_workloop_allocation_probe_requires_enabled_tracer(tmp_path, monkeypatch):
    monkeypatch.setattr(tracemalloc, "is_tracing", lambda: False)
    with pytest.raises(RuntimeError, match="explicitly started"):
        observe_python_allocations(tmp_path)


@pytest.mark.parametrize("interval", ["0", "-1", "3", "invalid"])
def test_workloop_host_rejects_invalid_allocation_interval(monkeypatch, interval):
    from tests.conformance.test_zaofu_testkit_s4_workloop_host import (
        test_sustained_task_pipeline_releases_owned_resources as run_workloop,
    )

    monkeypatch.setenv("ZF_RUN_MOCK_WORKLOOP", "1")
    monkeypatch.setenv("ZF_RUN_MOCK_WORKLOOP_ALLOC", "1")
    monkeypatch.setenv("ZF_MOCK_WORKLOOP_TASKS", "2")
    monkeypatch.setenv("ZF_MOCK_WORKLOOP_ALLOC_SAMPLE_EVERY", interval)
    monkeypatch.setattr("shutil.which", lambda _: "/unused/tmux")
    # Validation must reject before any project, tracer, or tmux resources exist.
    with pytest.raises((AssertionError, ValueError)):
        run_workloop(monkeypatch, None, None)


def test_workloop_exit_probe_rejects_running_worker():
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    try:
        with pytest.raises(AssertionError, match="still running"):
            assert_workers_exited({str(child.pid)}, timeout_seconds=0)
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stdin.close()
    assert assert_workers_exited({str(child.pid)}, timeout_seconds=0) == []


def test_workloop_exit_probe_keeps_zombie_distinct_from_reaped():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        [zombie] = assert_workers_exited({str(child.pid)}, timeout_seconds=0)
        assert zombie["State"].startswith("Z")
        assert zombie["PPid"] == str(os.getpid())
        assert Path(f"/proc/{child.pid}").exists()
    finally:
        child.wait(timeout=5)
    assert assert_workers_exited({str(child.pid)}, timeout_seconds=0) == []


@pytest.mark.parametrize("damage", [None, "no_frame", "wall_reverse", "mono_reverse", "clock_jump"])
def test_memray_window_requires_strict_interior_and_consistent_clocks(damage):
    from tests.testkit.memray_observer import select_snapshot

    window = {"wall_start_ns": 10_000_000, "wall_end_ns": 30_000_000,
              "monotonic_start_ns": 100_000_000, "monotonic_end_ns": 120_000_000}
    times = [10, 20, 30]
    if damage == "no_frame":
        times = [10, 30]  # Boundary equality is not an interior observation.
    elif damage == "wall_reverse":
        window["wall_end_ns"] = 1
    elif damage == "mono_reverse":
        window["monotonic_end_ns"] = 1
    elif damage == "clock_jump":
        window["wall_end_ns"] += 10_000_000
    if damage:
        with pytest.raises(AssertionError):
            select_snapshot(times, window)
    else:
        assert select_snapshot(times, window) == 1


def test_cache_counts_do_not_require_or_start_tracemalloc(tmp_path, monkeypatch):
    from tests.testkit.workloop_resources import observe_event_caches

    def forbidden(*args, **kwargs):
        raise AssertionError("allocation hook must not run")

    monkeypatch.setattr(tracemalloc, "start", forbidden)
    monkeypatch.setattr(tracemalloc, "take_snapshot", forbidden)
    log = EventLog(tmp_path / "events.jsonl")
    EventWriter(log).append(ZfEvent(type="probe.event", actor="testkit"))
    log.read_all()
    assert observe_event_caches(tmp_path)["cached_event_references"] >= 1
    assert observe_event_caches(tmp_path / "unrelated")["cached_event_references"] == 0


def test_memray_observer_close_failure_cannot_claim_closed(tmp_path):
    from tests.testkit.memray_observer import MemrayWorkloopObserver

    class BrokenTracker:
        def __exit__(self, *args):
            raise RuntimeError("close failed")

    observer = MemrayWorkloopObserver(tmp_path / "capture")
    observer.tracker = BrokenTracker()
    with pytest.raises(RuntimeError, match="close failed"):
        observer.close()
    assert not observer.closed


@pytest.mark.parametrize("allocation", ["0", "1"])
def test_memray_workloop_rejects_missing_lifecycle_or_output_before_setup(monkeypatch, allocation):
    from tests.conformance.test_zaofu_testkit_s4_workloop_host import (
        test_sustained_task_pipeline_releases_owned_resources as run_workloop,
    )

    monkeypatch.setenv("ZF_RUN_MOCK_WORKLOOP", "1")
    monkeypatch.setenv("ZF_RUN_MOCK_WORKLOOP_MEMRAY", "1")
    monkeypatch.setenv("ZF_RUN_MOCK_WORKLOOP_ALLOC", allocation)
    monkeypatch.delenv("ZF_MOCK_WORKLOOP_MEMRAY_OUTPUT", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/unused/tmux")
    with pytest.raises(AssertionError, match="requires|output required"):
        run_workloop(monkeypatch, None, None)


@pytest.mark.parametrize("failure", ["timeout", "parser_exit", "window_changed", "capture_changed"])
def test_memray_finish_rejects_bad_analysis_and_stops_tracker(tmp_path, monkeypatch, failure):
    from tests.testkit import memray_observer as module

    root = tmp_path / "capture"
    root.mkdir()
    observer = module.MemrayWorkloopObserver(root)
    closed = []

    class Tracker:
        def __exit__(self, *args):
            closed.append(True)

    observer.tracker = Tracker()
    observer.windows = [{"window": "fixture"}]
    observer.capture.write_bytes(b"capture")
    (root / "windows.json").write_text(json.dumps({"pid": os.getpid(), "windows": observer.windows}))

    def parse(command, **kwargs):
        assert closed == [True]
        assert kwargs == {"check": True, "timeout": 120}
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 120)
        if failure == "parser_exit":
            raise subprocess.CalledProcessError(1, command)
        (root / "analysis.json").write_text("{}")
        target = root / ("windows.json" if failure == "window_changed" else "memray.bin")
        target.write_bytes(b"changed")

    monkeypatch.setattr(module.subprocess, "run", parse)
    with pytest.raises((AssertionError, subprocess.SubprocessError)):
        observer.finish()
    observer.close()
    assert closed == [True]
    assert observer.capture.exists()  # Failures preserve raw evidence.
    with pytest.raises(AssertionError, match="outside active"):
        observer.observe(tmp_path)


@pytest.mark.parametrize("damage", ["empty_windows", "uncompressed", "corrupt"])
def test_memray_analysis_rejects_missing_windows_or_bad_capture(tmp_path, monkeypatch, damage):
    from types import SimpleNamespace
    from tests.testkit.memray_observer import analyze

    def reader(*args, **kwargs):
        assert damage == "corrupt", "invalid input must be rejected before FileReader"
        raise OSError("corrupt Memray capture")

    monkeypatch.setitem(sys.modules, "memray", SimpleNamespace(__version__="1.20.0", FileReader=reader))
    windows = [] if damage == "empty_windows" else [{}]
    (tmp_path / "windows.json").write_text(json.dumps({"pid": os.getpid(), "windows": windows}))
    (tmp_path / "memray.bin").write_bytes(b"\x04\x22\x4d\x18bad" if damage == "corrupt" else b"raw")
    with pytest.raises((AssertionError, OSError)):
        analyze(tmp_path)
    assert not (tmp_path / "analysis.json").exists()
