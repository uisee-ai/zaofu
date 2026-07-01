"""Regression test for B-MEM-02: concurrent MemoryStore.add() across the
UTC day-boundary rotate must not lose blocks, drop seed memory, or raise.

Before the fix (no lock around rotate+append in MemoryStore.add), a smoke
reproduced ~29/30 rounds with block loss, the seed memory destroyed, and
FileNotFoundError raised from add() when two processes raced the rotate
rename. The fix wraps the rotate+append critical section in
``locked_path(active)``.

Evidence: docs/records/2026-06-16-axisB-code-debt-smoke-REPORT.md
"""

from __future__ import annotations

import multiprocessing as mp
import os
import re
import time
from pathlib import Path

import pytest

from zf.core.memory.store import MemoryStore

_BLOCK_RE = re.compile(r"<!-- type: (\w+); max_days: \d+; last_updated: [^>]+ -->")
_SEED = "seed-content-MUST-SURVIVE"


def _worker(memory_dir: str, role: str, n: int, marker: str, barrier, q) -> None:
    store = MemoryStore(Path(memory_dir))
    barrier.wait()  # align all procs onto the rotate-triggering first add()
    for i in range(n):
        try:
            store.add(role, "fix", f"{marker}-{i:04d}")
        except Exception as e:  # noqa: BLE001 - test records any add() failure
            q.put(("EXC", type(e).__name__))


def _one_round(mem_dir: Path, nproc: int, per: int) -> tuple[int, int, bool, int]:
    role = "dev"
    active = mem_dir / f"{role}.md"
    mem_dir.mkdir(parents=True, exist_ok=True)
    # seed an active file dated yesterday so every proc's first add() rotates
    active.write_text(
        "<!-- type: fix; max_days: 7; last_updated: 2026-06-14T00:00:00+00:00 -->\n"
        f"## seed\n{_SEED}\n",
        encoding="utf-8",
    )
    yesterday = time.time() - 86400
    os.utime(active, (yesterday, yesterday))

    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(nproc)
    q: mp.Queue = ctx.Queue()
    procs = [
        ctx.Process(target=_worker, args=(str(mem_dir), role, per, f"W{p}", barrier, q))
        for p in range(nproc)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    exc = 0
    while not q.empty():
        if q.get()[0] == "EXC":
            exc += 1

    files: list[Path] = []
    if active.exists():
        files.append(active)
    adir = mem_dir / role
    if adir.exists():
        files.extend(sorted(adir.glob("*.md")))
    got = sum(len(_BLOCK_RE.findall(f.read_text(encoding="utf-8"))) for f in files)
    seed = any(_SEED in f.read_text(encoding="utf-8") for f in files)
    expected = 1 + nproc * per
    return expected, got, seed, exc


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork multiprocessing")
def test_concurrent_add_across_rotate_no_loss(tmp_path: Path) -> None:
    """Multiple panes bursting add() across a yesterday->today rotate must not
    lose blocks, must preserve the seed memory, and must never raise."""
    nproc, per, rounds = 6, 25, 3
    for r in range(rounds):
        expected, got, seed, exc = _one_round(tmp_path / f"r{r}", nproc, per)
        assert exc == 0, f"round {r}: add() raised {exc} time(s)"
        assert got == expected, f"round {r}: lost {expected - got} of {expected} blocks"
        assert seed, f"round {r}: seed memory was destroyed by the rotate race"
