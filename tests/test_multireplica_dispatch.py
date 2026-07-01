"""B-MULTIREPLICA-01 — Layer 2 assigns by role.name ("dev") but
Layer 1 only did exact instance_id lookup ("dev-1" / "dev-2"), so
replicas>=2 roles never received briefings in practice.

Surfaced by the 2026-04-22 mixed-multidev run with dev (claude, ×2) +
dev_codex (codex, ×2): all 4 dev instances spawned but ``zf kanban
assign dev`` never led to a ``task.dispatched`` event because the
dispatch lookup fell through.

Fix: ``_find_role_by_instance`` falls back to role.name match when
exact instance_id misses, preferring a WIP-available replica so
parallel assigns distribute naturally across the pool.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig, RoleConfig, SessionConfig, WorkflowConfig, ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


def _stub_transport():
    class _T:
        def __init__(self):
            self.sends = []
        def send_task(self, name, path, prompt):
            self.sends.append(name)
        def is_alive(self, n): return True
        def capture_log(self, n, lines=200): return ""
    return _T()


def _config_multi_dev(tmp_path):
    cfg = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(tmp_path)),
        session=SessionConfig(),
        roles=[
            RoleConfig(name="orchestrator", backend="claude-code"),
            # replicas=2 → schema __post_init__ expands to dev-1, dev-2
            RoleConfig(name="dev", backend="claude-code", replicas=2,
                       permission_mode="bypass"),
        ],
        workflow=WorkflowConfig(),
    )
    return cfg


def test_find_role_by_instance_exact_match_still_works(tmp_path: Path):
    """Regression guard: explicit instance_id ("dev-1") still resolves."""
    state_dir = tmp_path / ".zf"; state_dir.mkdir()
    cfg = _config_multi_dev(state_dir)
    # Verify expansion happened
    assert {r.instance_id for r in cfg.roles} == {"orchestrator", "dev-1", "dev-2"}

    orch = Orchestrator(state_dir, cfg, _stub_transport())
    r = orch._find_role_by_instance("dev-1")  # type: ignore[attr-defined]
    assert r is not None
    assert r.instance_id == "dev-1"


def test_find_role_by_instance_falls_back_to_role_name(tmp_path: Path):
    """B-MULTIREPLICA-01: lookup by role.name ("dev") must succeed when
    replicas>1 — return any WIP-available replica."""
    state_dir = tmp_path / ".zf"; state_dir.mkdir()
    cfg = _config_multi_dev(state_dir)
    orch = Orchestrator(state_dir, cfg, _stub_transport())

    r = orch._find_role_by_instance("dev")  # type: ignore[attr-defined]
    assert r is not None, (
        "fallback must return a replica when role.name is passed"
    )
    # Either dev-1 or dev-2 is acceptable (both idle, both valid)
    assert r.instance_id in ("dev-1", "dev-2")


def test_find_role_by_instance_prefers_wip_available(tmp_path: Path):
    """If dev-1 is already busy, fallback should return dev-2."""
    state_dir = tmp_path / ".zf"; state_dir.mkdir()
    cfg = _config_multi_dev(state_dir)

    # Simulate dev-1 busy with a task
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T-existing", title="busy",
                    status="in_progress", assigned_to="dev-1"))

    orch = Orchestrator(state_dir, cfg, _stub_transport())
    r = orch._find_role_by_instance("dev")  # type: ignore[attr-defined]
    assert r is not None
    assert r.instance_id == "dev-2", (
        f"expected dev-2 (dev-1 busy), got {r.instance_id}"
    )


def test_find_role_by_instance_returns_none_when_all_replicas_busy(
    tmp_path: Path,
):
    """All replicas at WIP limit → None so caller waits instead of
    over-dispatching."""
    state_dir = tmp_path / ".zf"; state_dir.mkdir()
    cfg = _config_multi_dev(state_dir)

    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T-A", title="a", status="in_progress", assigned_to="dev-1"))
    store.add(Task(id="T-B", title="b", status="in_progress", assigned_to="dev-2"))

    orch = Orchestrator(state_dir, cfg, _stub_transport())
    r = orch._find_role_by_instance("dev")  # type: ignore[attr-defined]
    assert r is None, (
        "both replicas busy → fallback returns None to defer dispatch"
    )


def test_find_role_by_instance_unknown_name_returns_none(tmp_path: Path):
    """Negative case: unknown name → None."""
    state_dir = tmp_path / ".zf"; state_dir.mkdir()
    cfg = _config_multi_dev(state_dir)

    orch = Orchestrator(state_dir, cfg, _stub_transport())
    r = orch._find_role_by_instance("phantom")  # type: ignore[attr-defined]
    assert r is None


def test_dispatch_updates_task_assigned_to_concrete_instance(tmp_path: Path):
    """After first successful dispatch via role-name fallback, the task's
    kanban assigned_to must be updated to the concrete instance_id so
    subsequent cycles take the exact-match path.
    """
    state_dir = tmp_path / ".zf"; state_dir.mkdir()
    cfg = _config_multi_dev(state_dir)

    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T-1", title="one",
                    status="backlog", assigned_to="dev"))

    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task.assigned", actor="orchestrator",
                        task_id="T-1", payload={"assignee": "dev"}))

    orch = Orchestrator(state_dir, cfg, _stub_transport())
    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]
    assert len(decisions) == 1
    # Task is now in_progress, assigned to a concrete replica
    t = store.get("T-1")
    assert t.status == "in_progress"
    assert t.assigned_to in ("dev-1", "dev-2"), (
        f"expected concrete instance_id, got {t.assigned_to!r}"
    )


def test_parallel_assigns_distribute_across_replicas(tmp_path: Path):
    """3 backlog tasks all assigned to role.name='dev' → across 3 wake
    cycles, 2 should land in different replicas (WIP=1 per replica,
    2 replicas total), the 3rd waits."""
    state_dir = tmp_path / ".zf"; state_dir.mkdir()
    cfg = _config_multi_dev(state_dir)

    store = TaskStore(state_dir / "kanban.json")
    for tid in ("T-1", "T-2", "T-3"):
        store.add(Task(id=tid, title=tid,
                        status="backlog", assigned_to="dev"))

    log = EventLog(state_dir / "events.jsonl")
    for tid in ("T-1", "T-2", "T-3"):
        log.append(ZfEvent(type="task.assigned", actor="orchestrator",
                            task_id=tid,
                            payload={"assignee": "dev"}))

    orch = Orchestrator(state_dir, cfg, _stub_transport())
    for _ in range(3):
        orch._dispatch_ready()  # type: ignore[attr-defined]

    in_progress = [t for t in store.list_all() if t.status == "in_progress"]
    # Exactly 2 (one per replica) — third waits for a replica to free
    assert len(in_progress) == 2, (
        f"expected 2 concurrent (one per replica, WIP=1 each), "
        f"got {len(in_progress)}"
    )
    # Two different replicas each took one
    assignees = {t.assigned_to for t in in_progress}
    assert assignees == {"dev-1", "dev-2"}, (
        f"expected work spread across both replicas, got {assignees}"
    )
