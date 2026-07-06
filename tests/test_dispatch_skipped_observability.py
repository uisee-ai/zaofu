"""B2 (review 2026-05-18): widened ``orchestrator.dispatch_skipped`` emit
coverage.

The original ``_emit_dispatch_skipped`` was wired into 3 of 8 silent skip
sites in ``_dispatch_ready``. The unobserved 5 paths (WIP busy, worker
not dispatchable, no available role, cycle WIP exhausted,
reassign-role-unresolved) are the exact class B-NEW-6 fell into:
``task.assigned`` fires but no ``task.dispatched`` and no other signal,
operator stares at a stuck pane.

This test exercises the new signature ``role: RoleConfig | None`` and
confirms ``no_available_role`` skips do emit the event.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import ZfConfig, ProjectConfig, RoleConfig, SessionConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path):
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    (sd / "logs").mkdir()
    event_log = EventLog(sd / "events.jsonl")
    event_log.append(ZfEvent(type="session.started", actor="zf-cli"))
    event_log.append(ZfEvent(type="loop.started", actor="zf-cli"))
    session_store = SessionStore(sd / "session.yaml")
    session_store.create(project_root=str(tmp_path))
    session_store.update(runtime_state="active")
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config_dev_only():
    return ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[
            RoleConfig(
                name="dev", backend="mock", stages=["implement"],
                publishes=["dev.build.done", "dev.blocked"],
            ),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True))


def test_emit_dispatch_skipped_accepts_none_role(
    state_dir, config_dev_only, transport,
):
    """The signature change (role: RoleConfig | None) must permit
    role=None — that's the shape the new no_available_role caller
    requires.

    Without this, the original strict type would force every silent-skip
    path to construct a fake RoleConfig, which is exactly the kind of
    accidental coupling that kept dispatch_skipped wired to only 3 of 8
    skip sites in the first place.
    """
    orch = Orchestrator(state_dir, config_dev_only, transport)

    # Construct a task and a 'no role found' situation by hand
    task = Task(
        id="T-norole-test",
        title="no-role probe",
        status="backlog",
        assigned_to="",
    )
    orch._emit_dispatch_skipped(
        task=task, role=None, reason="no_available_role",
    )

    skips = [
        e for e in orch.event_log.read_all()
        if e.type == "orchestrator.dispatch_skipped"
        and e.task_id == "T-norole-test"
    ]
    assert len(skips) == 1
    payload = skips[0].payload
    assert payload["reason"] == "no_available_role"
    assert payload["role"] == ""
    assert payload["assignee"] == ""
    assert payload["status"] == "backlog"


def test_emit_dispatch_skipped_dedup_60s_per_key(
    state_dir, config_dev_only, transport,
):
    """Cooldown is per (task_id, instance_id, reason, assigned_to,
    status). Identical re-skips within 30s collapse to one event so
    we don't flood events.jsonl when the dispatcher cycles every few
    seconds on the same stuck task."""
    orch = Orchestrator(state_dir, config_dev_only, transport)

    task = Task(id="T-dedup-test", title="dedup", status="backlog")
    for _ in range(5):
        orch._emit_dispatch_skipped(
            task=task, role=None, reason="no_available_role",
        )

    skips = [
        e for e in orch.event_log.read_all()
        if e.type == "orchestrator.dispatch_skipped"
        and e.task_id == "T-dedup-test"
    ]
    assert len(skips) == 1, (
        f"expected 1 skip event under 30s cooldown; got {len(skips)}"
    )


def test_emit_dispatch_skipped_distinct_reasons_not_deduped(
    state_dir, config_dev_only, transport,
):
    """Different reasons against the same task are separate dedup keys
    (operator needs to see each cause)."""
    orch = Orchestrator(state_dir, config_dev_only, transport)

    task = Task(id="T-multi-reason", title="multi", status="backlog")
    orch._emit_dispatch_skipped(task=task, role=None, reason="no_available_role")
    orch._emit_dispatch_skipped(task=task, role=None, reason="cycle_wip_exhausted")
    orch._emit_dispatch_skipped(task=task, role=None, reason="worker_not_dispatchable")

    skips = [
        e for e in orch.event_log.read_all()
        if e.type == "orchestrator.dispatch_skipped"
        and e.task_id == "T-multi-reason"
    ]
    reasons = {e.payload["reason"] for e in skips}
    assert reasons == {
        "no_available_role",
        "cycle_wip_exhausted",
        "worker_not_dispatchable",
    }


def test_repeated_dispatch_skipped_emits_actionable_dispatch_blocked(
    state_dir,
    config_dev_only,
    transport,
):
    orch = Orchestrator(state_dir, config_dev_only, transport)
    task = Task(
        id="T-blocked-observable",
        title="blocked",
        status="backlog",
        assigned_to="dev",
    )

    for _ in range(3):
        orch._emit_dispatch_skipped(
            task=task,
            role=None,
            reason="no_available_role",
        )
        orch._dispatch_skip_last_emit = {}

    blocked = [
        e for e in orch.event_log.read_all()
        if e.type == "dispatch.blocked"
        and e.task_id == "T-blocked-observable"
    ]
    assert len(blocked) == 1
    payload = blocked[0].payload
    assert payload["reason"] == "no_available_role"
    assert payload["target_role"] == "dev"
    assert payload["skip_count"] == 3
    assert "start, recycle, or free" in payload["recommended_action"]


def test_transition_only_one_pair_per_block_episode(
    state_dir, config_dev_only, transport,
):
    """RF-7B: 阻塞持续期间静默——同 (task, reason) 连续 skip 只发
    1 条 dispatch_skipped(进入)+ 1 条 dispatch.blocked(证实持续),
    解除(成功派发路径 _clear_dispatch_failure)发 1 条 dispatch.unblocked;
    新一轮阻塞开启新 episode。r10 取证:旧 30s cooldown 在 19h 阻塞期
    产出 ~3.3k 对(占 30k 事件日志 53%)。"""
    orch = Orchestrator(state_dir, config_dev_only, transport)
    task = Task(id="T-transition", title="transition", status="backlog")

    for _ in range(20):  # 模拟 20 个 tick 同因跳过
        orch._emit_dispatch_skipped(
            task=task, role=None, reason="wip_busy_reassign_branch",
        )

    events = orch.event_log.read_all()
    skipped = [e for e in events if e.type == "orchestrator.dispatch_skipped" and e.task_id == "T-transition"]
    blocked = [e for e in events if e.type == "dispatch.blocked" and e.task_id == "T-transition"]
    assert len(skipped) == 1, f"entry emits once, got {len(skipped)}"
    assert len(blocked) == 1, f"blocked emits once when persistent, got {len(blocked)}"
    assert blocked[0].payload["skip_count"] >= 3

    orch._clear_dispatch_failure("T-transition")
    events = orch.event_log.read_all()
    unblocked = [e for e in events if e.type == "dispatch.unblocked" and e.task_id == "T-transition"]
    assert len(unblocked) == 1
    assert unblocked[0].payload["reason"] == "wip_busy_reassign_branch"
    assert unblocked[0].payload["observations"] == 20

    # 解除后再次阻塞 = 新 episode,重新发进入事件
    orch._emit_dispatch_skipped(
        task=task, role=None, reason="wip_busy_reassign_branch",
    )
    events = orch.event_log.read_all()
    skipped = [e for e in events if e.type == "orchestrator.dispatch_skipped" and e.task_id == "T-transition"]
    assert len(skipped) == 2


def test_transition_only_distinct_reasons_independent_episodes(
    state_dir, config_dev_only, transport,
):
    orch = Orchestrator(state_dir, config_dev_only, transport)
    task = Task(id="T-two-reasons", title="two", status="backlog")
    for _ in range(4):
        orch._emit_dispatch_skipped(task=task, role=None, reason="wip_busy_reassign_branch")
        orch._emit_dispatch_skipped(task=task, role=None, reason="no_available_role")
    events = orch.event_log.read_all()
    skipped = [e for e in events if e.type == "orchestrator.dispatch_skipped" and e.task_id == "T-two-reasons"]
    assert len(skipped) == 2  # 每个 reason 各 1 条进入
    orch._clear_dispatch_failure("T-two-reasons")
    unblocked = [e for e in orch.event_log.read_all() if e.type == "dispatch.unblocked" and e.task_id == "T-two-reasons"]
    assert len(unblocked) == 2  # 两个 episode 各自闭合
