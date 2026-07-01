"""α-1 backlog: task-independence checker for fanout dispatch.

Per docs/design/36-zero-touch-long-horizon-roadmap.md §4.2 + the
backlog at backlogs/2026-05-17-1447-zero-touch-alpha-1-task-independence-checker.md.

Phase α-1 防灾测试：multi-dev fanout 在 task contract 的
shared_files / exclusive_files 有交集时**不应该** dispatch，应该 emit
fanout.serialize 让 backlog scheduler 串行派。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


# ─── unit tests for the pure independence check ──────────────────────────


def _make_task(
    task_id: str,
    *,
    exclusive_files: list[str] | None = None,
    shared_files: list[str] | None = None,
    fanout_force: bool = False,
) -> Task:
    contract = TaskContract(
        exclusive_files=exclusive_files or [],
        shared_files=shared_files or [],
        fanout_force=fanout_force,
    )
    return Task(id=task_id, title=task_id, contract=contract)


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "kanban.json")


def _make_orchestrator(store: TaskStore):
    """Build a minimal Orchestrator-shaped object with just the bits the
    function under test needs (task_store)."""
    from zf.runtime.orchestrator_dispatch import DispatchMixin

    class _StubOrch(DispatchMixin):
        def __init__(self, store: TaskStore) -> None:
            self.task_store = store

    return _StubOrch(store)


def test_two_clean_tasks_are_independent(store):
    store.add(_make_task("T-A", exclusive_files=["a.py"]))
    store.add(_make_task("T-B", exclusive_files=["b.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}, {"task_id": "T-B"}]
    independent, reason = orch._check_fanout_independence(items)

    assert independent is True
    assert reason == ""


def test_exclusive_exclusive_overlap_blocks_fanout(store):
    store.add(_make_task("T-A", exclusive_files=["a.py", "shared.py"]))
    store.add(_make_task("T-B", exclusive_files=["shared.py", "b.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}, {"task_id": "T-B"}]
    independent, reason = orch._check_fanout_independence(items)

    assert independent is False
    assert "shared.py" in reason
    assert "T-A" in reason and "T-B" in reason


def test_exclusive_shared_overlap_blocks_fanout(store):
    store.add(_make_task("T-A", exclusive_files=["a.py"]))
    store.add(_make_task("T-B", shared_files=["a.py", "b.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}, {"task_id": "T-B"}]
    independent, reason = orch._check_fanout_independence(items)

    assert independent is False
    assert "a.py" in reason


def test_shared_exclusive_overlap_in_either_direction_blocks(store):
    store.add(_make_task("T-A", shared_files=["lib.py"]))
    store.add(_make_task("T-B", exclusive_files=["lib.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}, {"task_id": "T-B"}]
    independent, reason = orch._check_fanout_independence(items)

    assert independent is False
    assert "lib.py" in reason


def test_shared_shared_overlap_is_allowed(store):
    """If both tasks merely READ the same file (both shared), parallel
    dispatch is safe — they don't write conflicting changes."""
    store.add(_make_task("T-A", shared_files=["readme.md", "common.py"]))
    store.add(_make_task("T-B", shared_files=["common.py", "extra.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}, {"task_id": "T-B"}]
    independent, reason = orch._check_fanout_independence(items)

    assert independent is True
    assert reason == ""


def test_three_way_overlap_detected(store):
    store.add(_make_task("T-A", exclusive_files=["a.py"]))
    store.add(_make_task("T-B", exclusive_files=["b.py"]))
    store.add(_make_task("T-C", exclusive_files=["a.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}, {"task_id": "T-B"}, {"task_id": "T-C"}]
    independent, reason = orch._check_fanout_independence(items)

    assert independent is False
    # Either (A,C) or (C,A) ordering — both reference a.py
    assert "a.py" in reason


def test_fanout_force_overrides_check(store):
    """contract.fanout_force=True is the operator escape hatch: the
    forced task self-attests and is removed from the pairwise gate.
    With only one non-forced task left, the group is trivially
    independent."""
    store.add(_make_task("T-A", exclusive_files=["a.py"], fanout_force=True))
    store.add(_make_task("T-B", exclusive_files=["a.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}, {"task_id": "T-B"}]
    independent, reason = orch._check_fanout_independence(items)

    assert independent is True


def test_fanout_force_does_not_exempt_siblings(store):
    """A1 narrowing (2026-05-18): fanout_force on one task does NOT
    short-circuit the whole group. Non-forced siblings still get
    pairwise-checked against each other so a real B-vs-C conflict
    cannot be silently papered over by a force flag on A."""
    store.add(_make_task("T-A", exclusive_files=["a.py"], fanout_force=True))
    store.add(_make_task("T-B", exclusive_files=["b.py"]))
    store.add(_make_task("T-C", exclusive_files=["b.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}, {"task_id": "T-B"}, {"task_id": "T-C"}]
    independent, reason = orch._check_fanout_independence(items)

    assert independent is False
    assert "b.py" in reason
    # Forced task T-A is exempt — the conflict is between B and C
    assert "T-B" in reason and "T-C" in reason


def test_single_task_is_trivially_independent(store):
    store.add(_make_task("T-A", exclusive_files=["a.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}]
    independent, reason = orch._check_fanout_independence(items)

    assert independent is True


def test_empty_task_list_is_trivially_independent(store):
    orch = _make_orchestrator(store)
    independent, reason = orch._check_fanout_independence([])

    assert independent is True


def test_missing_task_id_in_store_is_skipped(store):
    """If task_item references a task_id not in the store (race?), skip
    it rather than crashing the fanout decision."""
    store.add(_make_task("T-A", exclusive_files=["a.py"]))
    # T-MISSING never added

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-A"}, {"task_id": "T-MISSING"}]
    independent, reason = orch._check_fanout_independence(items)

    # Only T-A is checked → trivially independent
    assert independent is True


def test_task_with_no_contract_is_skipped(store):
    """Tasks without a contract have no file claims — skipped from check."""
    bare = Task(id="T-BARE", title="bare", contract=None)
    store.add(bare)
    store.add(_make_task("T-B", exclusive_files=["b.py"]))

    orch = _make_orchestrator(store)
    items = [{"task_id": "T-BARE"}, {"task_id": "T-B"}]
    independent, reason = orch._check_fanout_independence(items)

    # T-BARE has no file claims → independent of T-B
    assert independent is True


# ─── known_types + wake_patterns coverage ────────────────────────────────


def test_fanout_serialize_event_in_known_types():
    """The fanout.serialize event must be registered (defense against
    silent drop)."""
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    assert "fanout.serialize" in KNOWN_EVENT_TYPES


def test_fanout_serialize_event_in_wake_patterns():
    """fanout.serialize must wake run_once so the kernel can re-route
    the affected tasks to serial dispatch."""
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "fanout.serialize" in WAKE_PATTERNS


# ─── wire-up grep proof (CLAUDE.md anti-orphan discipline) ────────────────


def test_wire_up_check_fanout_independence_referenced_from_orchestrator():
    """α-1 backlog requires runtime-import grep proof — the caller
    `_maybe_start_writer_fanout` lives in FanoutCoordinationMixin
    (orchestrator_fanout.py, moved verbatim from orchestrator.py in P3)."""
    runtime = Path(__file__).resolve().parents[1] / "src/zf/runtime"
    text = "\n".join(
        (runtime / name).read_text(encoding="utf-8")
        for name in ("orchestrator.py", "orchestrator_fanout.py")
    )
    assert "_check_fanout_independence" in text, (
        "α-1 wire-up missing: the orchestrator runtime does not call "
        "_check_fanout_independence — library-without-callers anti-pattern"
    )
    assert "fanout.serialize" in text, (
        "α-1 wire-up missing: the orchestrator runtime does not emit "
        "fanout.serialize"
    )
