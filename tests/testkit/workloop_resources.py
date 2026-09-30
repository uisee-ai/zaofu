"""Linux observations for the owned TestKit work-loop process, not projections."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import time
import tracemalloc


def observe_process(pid: int) -> dict:
    root = Path(f"/proc/{pid}")
    # Missing process/sensor is an error, never a fabricated zero measurement.
    status = dict(line.split(":", 1) for line in (root / "status").read_text().splitlines())
    fds = []
    sockets = []
    for entry in (root / "fd").iterdir():
        try:
            target = os.readlink(entry)
        except FileNotFoundError:
            continue  # The directory reader's own FD or a concurrently closed descriptor.
        fds.append(int(entry.name))
        if target.startswith("socket:["):
            sockets.append(target)
    return {
        "pid": pid, "monotonic": time.monotonic(),
        "rss_kib": int(status["VmRSS"].split()[0]),
        "fds": len(fds), "socket_inodes": sorted(sockets),
        "threads": int(status["Threads"]),
    }


def observe_tree_bytes(root: Path) -> dict:
    """Measure owned regular files without following links or temporary files."""
    root = root.resolve()
    total = 0
    files = 0
    by_top_level: dict[str, int] = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.name.endswith(".tmp"):
            continue
        size = path.stat().st_size
        files += 1
        total += size
        relative = path.relative_to(root)
        top = relative.parts[0] if relative.parts else "."
        by_top_level[top] = by_top_level.get(top, 0) + size
    return {"root": str(root), "files": files, "bytes": total,
            "by_top_level": dict(sorted(by_top_level.items()))}


def observe_tcp_sockets(pid: int) -> list[dict]:
    """Join this process's socket FDs to kernel TCP states, never all host sockets."""
    inodes = {entry[8:-1] for entry in observe_process(pid)["socket_inodes"]}
    rows = []
    for family in ("tcp", "tcp6"):
        table = Path(f"/proc/{pid}/net/{family}").read_text().splitlines()[1:]
        for line in table:
            fields = line.split()
            if fields[9] in inodes:
                rows.append({"inode": fields[9], "state": fields[3],
                             "local": fields[1], "remote": fields[2]})
    return rows


def observe_python_allocations(state: Path, *, limit: int = 8) -> dict:
    """Observe allocation sites and owned-state cache counts, not causal ownership."""
    started = time.monotonic()
    if not tracemalloc.is_tracing():
        raise RuntimeError("tracemalloc must be explicitly started before observing allocations")
    if not 1 <= limit <= 32:
        raise ValueError("allocation site limit must be between 1 and 32")
    current, peak = tracemalloc.get_traced_memory()
    overhead = tracemalloc.get_tracemalloc_memory()
    snapshot = tracemalloc.take_snapshot()
    stats = snapshot.statistics("filename")
    repo = Path(__file__).resolve().parents[2]
    by_domain = {"zf_source": 0, "testkit": 0, "other": 0}
    for stat in stats:
        filename = stat.traceback[0].filename
        domain = "zf_source" if filename.startswith(str(repo / "src") + "/") else (
            "testkit" if filename.startswith(str(repo / "tests") + "/") else "other"
        )
        by_domain[domain] += stat.size
    top = []
    for stat in stats[:limit]:
        top.append({
            "filename": stat.traceback[0].filename,
            "size_bytes": stat.size,
            "count": stat.count,
        })
    observed = {
        "current_bytes": current,
        "peak_bytes": peak,
        "tracer_overhead_bytes": overhead,
        "by_allocation_domain_bytes": by_domain,
        "top": top,
        **observe_event_caches(state),
    }
    observed["observation_seconds"] = time.monotonic() - started
    return observed


def observe_event_caches(state: Path) -> dict:
    """Read only this state's cache counts, independently of allocation tracing."""
    from zf.core.events import index as event_index
    from zf.core.events import log as event_log

    log_path = str((state / "events.jsonl").resolve())
    index_path = str((state / "event_index.json").resolve())
    reads = [v for k, v in event_log._READ_ALL_CACHE.items() if k[0] == log_path]
    folds = [v for k, v in event_log._READ_ALL_FOLD.items() if k[0] == log_path]
    indexes = [v for k, v in event_index._INDEX_FILE_CACHE.items() if k[0] == index_path]
    return {
        "event_log_read_cache_entries": len(reads),
        "event_log_fold_entries": len(folds),
        "event_index_file_cache_entries": len(indexes),
        # References can share objects; do not sum them as unique retained bytes.
        "cached_event_references": sum(map(len, reads)) + sum(len(v["events"]) for v in folds),
        "cached_index_event_dicts": sum(len(v.get("event_by_id", {})) for v in indexes),
    }


def assert_locks_released(root: Path) -> list[str]:
    """Persistent lock inodes are allowed; an independently opened lock must be free."""
    checked = []
    for path in sorted(root.rglob("*.lock")):
        with path.open("rb") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AssertionError(f"owned state lock is still held: {path}") from exc
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
        checked.append(path.relative_to(root).as_posix())
    return checked


def assert_workers_exited(pids: set[str], *, timeout_seconds: float = 3) -> list[dict]:
    """Reject running workers; return unreaped exits for separate reclamation checks."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = []
        for pid in sorted(pids):
            try:
                status = Path(f"/proc/{pid}/status").read_text()
            except FileNotFoundError:
                continue
            fields = dict(line.split(":", 1) for line in status.splitlines())
            remaining.append({"pid": pid, **{
                key: fields[key].strip() for key in ("Name", "State", "PPid", "Threads")
            }})
        if all(row["State"].startswith("Z") for row in remaining):
            return remaining
        assert time.monotonic() < deadline, f"owned workers still running: {remaining}"
        time.sleep(0.05)
