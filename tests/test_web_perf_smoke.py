"""RF-9: perf budget smoke on a generated fixture (opt-in via -m perf).

Budgets are deliberately generous (shared dev boxes are noisy); the point is
catching order-of-magnitude regressions like the O(events x tasks) scans, not
millisecond drift. Run: pytest -m perf tests/test_web_perf_smoke.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent

pytestmark = pytest.mark.perf


@pytest.fixture
def perf_state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    tasks = [
        {"id": f"T-{i:03d}", "title": f"task {i}", "status": "in_progress",
         "assigned_to": f"dev-{i % 3}", "contract": {}}
        for i in range(12)
    ]
    (sd / "kanban.json").write_text(json.dumps(tasks), encoding="utf-8")
    (sd / "feature_list.json").write_text("[]", encoding="utf-8")
    log = EventLog(sd / "events.jsonl")
    for i in range(3000):
        log.append(ZfEvent(
            type="dev.build.done" if i % 7 else "verify.child.completed",
            actor=f"dev-{i % 3}",
            task_id=f"T-{i % 12:03d}",
            payload={"summary": f"step {i}", "refs": {"task_ref": f"T-{i % 12:03d}"}},
        ))
    return sd


def _timed(fn, budget_s: float, label: str):
    fn()  # warm
    start = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - start
    assert elapsed < budget_s, f"{label}: {elapsed:.2f}s over budget {budget_s}s"


def test_perf_budgets(perf_state_dir: Path):
    import zf.web.server as srv

    _timed(lambda: srv._kanban(perf_state_dir), 2.0, "kanban(3k events x 12 tasks)")
    _timed(
        lambda: srv._snapshot_slice(perf_state_dir, slice_name="light"),
        3.0, "snapshot/light",
    )
    _timed(
        lambda: srv._task_detail(perf_state_dir, "T-001"),
        3.0, "task_detail",
    )
