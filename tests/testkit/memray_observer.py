"""Explicit, single-cycle Memray backend for TestKit's owned work-loop."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from tests.testkit.evidence import write_json_atomic
from tests.testkit.workloop_resources import observe_event_caches

MAX_CAPTURE_BYTES = 2 * 1024**3
MAX_SNAPSHOTS = 200000


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def select_snapshot(times: list[int], window: dict) -> int:
    """Select only a frame strictly inside a stable forward-clock window."""
    start, end = window["wall_start_ns"], window["wall_end_ns"]
    mono_delta = window["monotonic_end_ns"] - window["monotonic_start_ns"]
    if end <= start or mono_delta <= 0 or abs(end - start - mono_delta) > 2_000_000:
        raise AssertionError("unstable observation clock")
    inside = [i for i, ms in enumerate(times) if start < ms * 1_000_000 < end]
    if not inside:
        raise AssertionError("no strictly interior memory snapshot")
    return inside[len(inside) // 2]


class MemrayWorkloopObserver:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.capture = root / "memray.bin"
        self.windows: list[dict] = []
        self.tracker = None
        self.closed = False
        self.tracker_started_ns: int | None = None
        self.tracker_pid: int | None = None

    def start(self) -> None:
        import memray

        if self.tracker is not None or self.closed:
            raise AssertionError("cannot restart an allocation Tracker")
        if memray.__version__ != "1.20.0":
            raise AssertionError("this calibrated backend requires memray==1.20.0")
        self.root.mkdir(parents=True, exist_ok=False)
        self.tracker = memray.Tracker(
            destination=memray.FileDestination(self.capture, compress_on_exit=True),
            file_format=memray.FileFormat.ALL_ALLOCATIONS,
            trace_python_allocators=True, native_traces=False, memory_interval_ms=10,
        )
        self.tracker.__enter__()
        self.tracker_started_ns = time.time_ns()
        self.tracker_pid = os.getpid()

    def observe(self, state: Path) -> dict:
        if self.tracker is None or self.closed:
            raise AssertionError("observation outside active Tracker")
        if self.capture.exists() and self.capture.stat().st_size > MAX_CAPTURE_BYTES:
            raise AssertionError("allocation capture exceeds 2GiB checkpoint budget")
        started = time.monotonic()
        before = observe_event_caches(state)
        window = {"wall_start_ns": time.time_ns(), "monotonic_start_ns": time.monotonic_ns()}
        time.sleep(0.06)
        window.update(wall_end_ns=time.time_ns(), monotonic_end_ns=time.monotonic_ns())
        after = observe_event_caches(state)
        if before != after:
            raise AssertionError("owned cache counts changed within observation window")
        self.windows.append(window)
        write_json_atomic(self.root / "windows.json", {"pid": os.getpid(), "windows": self.windows})
        return {**before, "backend": "memray", "checkpoint": len(self.windows) - 1,
                "window": window, "observation_seconds": time.monotonic() - started,
                "tracker_pid": self.tracker_pid,
                "tracker_started_ns": self.tracker_started_ns,
                "capture_path": str(self.capture.resolve())}

    def close(self) -> None:
        if self.tracker is not None and not self.closed:
            self.tracker.__exit__(None, None, None)
            self.closed = True

    def finish(self) -> dict:
        self.close()
        if not self.closed or not self.windows:
            raise AssertionError("incomplete allocation observation")
        if not 0 < self.capture.stat().st_size <= MAX_CAPTURE_BYTES:
            raise AssertionError("allocation capture outside byte budget")
        manifest = self.root / "windows.json"
        if json.loads(manifest.read_text()) != {"pid": os.getpid(), "windows": self.windows}:
            raise AssertionError("allocation observation windows changed")
        if (self.root / "analysis.json").exists():
            raise AssertionError("refusing existing allocation analysis")
        windows_before = file_sha256(manifest)
        before = file_sha256(self.capture)
        subprocess.run(
            [sys.executable, "-m", "tests.testkit.memray_observer", str(self.root)],
            check=True, timeout=120,
        )
        result = json.loads((self.root / "analysis.json").read_text())
        if before != file_sha256(self.capture) or windows_before != file_sha256(manifest):
            raise AssertionError("allocation evidence changed during analysis")
        return {"directory": str(self.root), "capture_sha256": before,
                "windows_sha256": file_sha256(self.root / "windows.json"),
                "analysis_sha256": file_sha256(self.root / "analysis.json"), **result}


def analyze(root: Path) -> None:
    import memray

    if memray.__version__ != "1.20.0":
        raise AssertionError("analysis requires calibrated memray==1.20.0")
    manifest = json.loads((root / "windows.json").read_text())
    windows = manifest["windows"]
    if not windows:
        raise AssertionError("missing allocation observation windows")
    capture = root / "memray.bin"
    if not 0 < capture.stat().st_size <= MAX_CAPTURE_BYTES:
        raise AssertionError("allocation capture outside byte budget")
    with capture.open("rb") as stream:
        if stream.read(4) != b"\x04\x22\x4d\x18":
            raise AssertionError("expected compressed capture, refusing tail-scan path")
    with memray.FileReader(capture, max_memory_records=MAX_SNAPSHOTS) as reader:
        if (reader.metadata.file_format != memray.FileFormat.ALL_ALLOCATIONS
                or not reader.metadata.trace_python_allocators
                or reader.metadata.has_native_traces):
            raise AssertionError("capture does not trace individual Python allocations")
        if reader.metadata.pid != manifest["pid"]:
            raise AssertionError("capture PID differs from observation owner")
        # FileReader may downsample before returning fewer than MAX_SNAPSHOTS.
        duration = (reader.metadata.end_time - reader.metadata.start_time).total_seconds()
        if not 0 < duration * 1000 / 10 < MAX_SNAPSHOTS:
            raise AssertionError("capture duration risks downsampled memory snapshots")
        snapshots = list(reader.get_memory_snapshots())
        if not 0 < len(snapshots) < MAX_SNAPSHOTS:
            raise AssertionError("missing or possibly downsampled memory snapshots")
        times = [s.time for s in snapshots]
        if any(b < a for a, b in zip(times, times[1:])):
            raise AssertionError("snapshot wall clock went backwards")
        indices = [select_snapshot(times, window) for window in windows]
        rows = [{"checkpoint": point, "snapshot_index": i,
                 "epoch_ms": snapshots[i].time, "heap_bytes": snapshots[i].heap,
                 "rss_bytes": snapshots[i].rss, "by_filename_bytes": {}}
                for point, i in enumerate(indices)]
        site_count = 0
        for record in reader.get_temporal_allocation_records():
            site_count += 1
            stack = record.stack_trace()
            filename = stack[0][1] if stack else "<native-or-unknown>"
            for interval in record.intervals:
                for row, i in zip(rows, indices):
                    if (interval.allocated_before_snapshot <= i
                            and (interval.deallocated_before_snapshot is None
                                 or i < interval.deallocated_before_snapshot)):
                        counts = row["by_filename_bytes"]
                        counts[filename] = counts.get(filename, 0) + interval.n_bytes
        for row in rows:
            counts = row.pop("by_filename_bytes")
            row["temporal_live_bytes"] = sum(counts.values())
            row["top"] = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)[:8]
        write_json_atomic(root / "analysis.json", {
            "schema_version": "zf-testkit-memray-analysis.v1",
            "snapshot_count": len(snapshots), "temporal_sites": site_count,
            "total_allocations": reader.metadata.total_allocations,
            "observations": rows,
            "scope": "parent-process allocations; not a no-leak verdict or worker coverage",
        })


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    analyze(Path(sys.argv[1]))
