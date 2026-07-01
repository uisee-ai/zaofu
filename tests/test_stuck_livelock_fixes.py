"""B-STUCK-* : false-stuck livelock fixes (prod-flow e2e finding).

A real coding agent does long single turns emitting agent.usage but sparse
worker.heartbeat. The heartbeat sweep false-declares it stuck → respawn rotates
the dispatch_id → the worker's valid completion carries the pre-respawn id →
kernel rejects it → task never advances → livelock. These tests pin the fixes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig, RoleConfig, SessionConfig, ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _config(token_required: bool = False):
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock",
                          publishes=["workflow.child.completed"])],
    )
    if token_required:
        cfg.verification.contract.dispatch_token_required = True
    return cfg


# --------------------------------------------------------------- B-STUCK-1


def test_remember_dispatch_id_bounded_history(state_dir, transport):
    orch = Orchestrator(state_dir, _config(), transport)
    for d in ["d1", "d2", "d3", "d4"]:
        orch._remember_dispatch_id("T1", d)
    assert orch._active_dispatch_ids["T1"] == "d4"          # latest is active
    assert orch._recent_dispatch_ids["T1"] == ["d2", "d3", "d4"]  # last 3 kept


def test_recent_dispatch_id_is_graced_after_respawn(state_dir, transport):
    # task got re-dispatched A -> B (respawn). The worker's valid completion
    # still carries A. With B-STUCK-1 the kernel grace-accepts A.
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="in_progress",
                   assigned_to="dev", active_dispatch_id="disp-B"))
    orch = Orchestrator(state_dir, _config(token_required=True), transport)
    task = orch.task_store.get("T1")
    orch._remember_dispatch_id("T1", "disp-A")
    orch._remember_dispatch_id("T1", "disp-B")

    graced = ZfEvent(type="workflow.child.completed", actor="dev", task_id="T1",
                     payload={"dispatch_id": "disp-A"})
    decision = orch._reject_invalid_lifecycle_event(graced)
    assert decision is None or decision.action != "block"  # accepted, not stranded


def test_stranger_dispatch_id_still_blocked(state_dir, transport):
    # a dispatch_id never issued for this task must still be rejected (anti-zombie)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="in_progress",
                   assigned_to="dev", active_dispatch_id="disp-B"))
    orch = Orchestrator(state_dir, _config(token_required=True), transport)
    task = orch.task_store.get("T1")
    orch._remember_dispatch_id("T1", "disp-A")
    orch._remember_dispatch_id("T1", "disp-B")

    stranger = ZfEvent(type="workflow.child.completed", actor="dev", task_id="T1",
                       payload={"dispatch_id": "disp-ZZZ"})
    decision = orch._reject_invalid_lifecycle_event(stranger)
    assert decision is not None and decision.action == "block"


# --------------------------------------------------------------- B-STUCK-2


def test_agent_usage_refreshes_liveness(state_dir, transport):
    # a worker emitting agent.usage must refresh its liveness clock so the
    # heartbeat sweep does not false-declare it stuck mid coding-turn.
    from zf.core.state.role_sessions import RoleSessionRegistry
    orch = Orchestrator(state_dir, _config(), transport)
    orch._apply_housekeeping(ZfEvent(type="agent.usage", actor="dev", task_id="T1",
                                     payload={"tokens": 100}))
    reg = RoleSessionRegistry(state_dir / "role_sessions.yaml",
                              project_root=str(state_dir.parent))
    ts, payload = reg.get_last_heartbeat("dev")
    assert ts, "agent.usage did not refresh last_heartbeat_at"


def test_agent_usage_liveness_survives_rich_path_failure(state_dir, transport, monkeypatch):
    # even if the richer apply_agent_usage_liveness throws, the guaranteed
    # minimal touch must still refresh liveness (B-STUCK-2 root guarantee).
    import zf.runtime.usage_liveness as ul
    monkeypatch.setattr(ul, "apply_agent_usage_liveness",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    from zf.core.state.role_sessions import RoleSessionRegistry
    orch = Orchestrator(state_dir, _config(), transport)
    orch._apply_housekeeping(ZfEvent(type="agent.usage", actor="dev", task_id="T1",
                                     payload={"tokens": 100}))
    reg = RoleSessionRegistry(state_dir / "role_sessions.yaml",
                              project_root=str(state_dir.parent))
    ts, _ = reg.get_last_heartbeat("dev")
    assert ts, "minimal liveness touch must survive rich-path failure"
