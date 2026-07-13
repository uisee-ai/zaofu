"""α-2: worker.heartbeat protocol.

Per docs/design/36-zero-touch-long-horizon-roadmap.md §4.3 + backlog
backlogs/2026-05-17-1447-zero-touch-alpha-2-3-heartbeat-and-proactive-dispatch.md.

The protocol: every worker pane emits ``zf emit worker.heartbeat``
periodically (~60s) with `{instance_id, current_task_id, state,
last_action_ts, context_used_ratio?, checkpoint_ref?}`. Kernel stores
the latest `last_heartbeat_at` per instance into role_sessions.yaml.
α-3 (later) consumes the timestamps to detect idle / silent / stuck.

This file tests the protocol's primitives:
  - event type registered (known_types + wake_patterns)
  - housekeeping helper writes role_sessions metadata
  - generate_task_briefing injects the heartbeat skill instruction for
    non-orchestrator roles
  - orchestrator wires worker.heartbeat → housekeeping helper

α-3 sweep + proactive dispatch lives in a separate test file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task, TaskContract


# ─── event registration ──────────────────────────────────────────────────


def test_worker_heartbeat_in_known_types():
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    assert "worker.heartbeat" in KNOWN_EVENT_TYPES


def test_worker_heartbeat_in_wake_patterns():
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "worker.heartbeat" in WAKE_PATTERNS


# ─── housekeeping helper ─────────────────────────────────────────────────


def test_apply_worker_heartbeat_writes_role_sessions_metadata(tmp_path: Path):
    from zf.runtime.housekeeping import apply_worker_heartbeat_event

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    # Pre-create the instance so meta entry exists
    reg.get_or_create("dev-1")

    event = ZfEvent(
        type="worker.heartbeat",
        actor="dev-1",
        payload={
            "instance_id": "dev-1",
            "current_task_id": "TASK-T1",
            "state": "busy",
            "last_action_ts": "2026-05-17T14:00:00+00:00",
            "context_used_ratio": 0.32,
        },
    )

    apply_worker_heartbeat_event(reg, event)

    last_at, last_payload = reg.get_last_heartbeat("dev-1")
    assert last_at, "heartbeat timestamp must be persisted"
    assert last_payload["current_task_id"] == "TASK-T1"
    assert last_payload["state"] == "busy"
    assert last_payload["context_used_ratio"] == pytest.approx(0.32)


def test_apply_worker_heartbeat_multiple_emits_keep_latest(tmp_path: Path):
    from zf.runtime.housekeeping import apply_worker_heartbeat_event

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    reg.get_or_create("dev-1")

    for state, ts in [
        ("idle", "2026-05-17T14:00:00+00:00"),
        ("busy", "2026-05-17T14:01:00+00:00"),
        ("idle", "2026-05-17T14:02:00+00:00"),
    ]:
        apply_worker_heartbeat_event(reg, ZfEvent(
            type="worker.heartbeat",
            actor="dev-1",
            payload={
                "instance_id": "dev-1",
                "current_task_id": "",
                "state": state,
                "last_action_ts": ts,
            },
        ))

    last_at, last_payload = reg.get_last_heartbeat("dev-1")
    assert last_payload["state"] == "idle"
    assert "14:02:00" in last_payload["last_action_ts"]


def test_apply_worker_heartbeat_missing_instance_creates_meta_row(tmp_path: Path):
    """If a heartbeat arrives for an instance not yet in role_sessions.yaml,
    don't crash — create the meta row implicitly."""
    from zf.runtime.housekeeping import apply_worker_heartbeat_event

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    # NOT calling get_or_create("dev-99")

    apply_worker_heartbeat_event(reg, ZfEvent(
        type="worker.heartbeat",
        actor="dev-99",
        payload={"instance_id": "dev-99", "state": "idle"},
    ))

    last_at, last_payload = reg.get_last_heartbeat("dev-99")
    assert last_at, "should still record heartbeat for unknown instance"


def test_apply_task_dispatched_seeds_busy_heartbeat(tmp_path: Path):
    from zf.runtime.housekeeping import apply_task_dispatched_heartbeat_seed

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )

    apply_task_dispatched_heartbeat_seed(reg, ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-T1",
        payload={
            "assignee": "arch",
            "role": "arch",
            "dispatch_id": "disp-1",
        },
    ))

    last_at, last_payload = reg.get_last_heartbeat("arch")
    assert last_at, "task.dispatched should seed worker liveness"
    assert last_payload["current_task_id"] == "TASK-T1"
    assert last_payload["state"] == "busy"
    assert last_payload["dispatch_id"] == "disp-1"
    assert last_payload["source"] == "task.dispatched"


def test_apply_fanout_child_dispatched_seeds_busy_heartbeat(tmp_path: Path):
    from zf.runtime.housekeeping import apply_task_dispatched_heartbeat_seed

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )

    apply_task_dispatched_heartbeat_seed(reg, ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "task_id": "TASK-FANOUT",
            "role_instance": "dev-lane-1",
            "run_id": "run-fanout-child",
        },
    ))

    last_at, last_payload = reg.get_last_heartbeat("dev-lane-1")
    assert last_at, "fanout.child.dispatched should seed worker liveness"
    assert last_payload["current_task_id"] == "TASK-FANOUT"
    assert last_payload["state"] == "busy"
    assert last_payload["run_id"] == "run-fanout-child"
    assert last_payload["source"] == "fanout.child.dispatched"


def test_apply_reader_fanout_child_without_task_id_seeds_busy_heartbeat(
    tmp_path: Path,
):
    from zf.runtime.housekeeping import apply_task_dispatched_heartbeat_seed

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )

    apply_task_dispatched_heartbeat_seed(reg, ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-judge-1",
            "child_id": "judge-refactor",
            "stage_id": "final-judge",
            "role_instance": "judge-refactor",
            "run_id": "run-fanout-judge-1-judge-refactor",
        },
    ))

    last_at, last_payload = reg.get_last_heartbeat("judge-refactor")
    assert last_at, "reader fanout without task_id should still seed liveness"
    assert last_payload["current_task_id"] == "fanout:fanout-judge-1:judge-refactor"
    assert last_payload["state"] == "busy"
    assert last_payload["fanout_id"] == "fanout-judge-1"
    assert last_payload["child_id"] == "judge-refactor"
    assert last_payload["source"] == "fanout.child.dispatched"


def test_worker_state_busy_preserves_reader_fanout_dispatch_binding(
    tmp_path: Path,
):
    from zf.runtime.housekeeping import (
        apply_task_dispatched_heartbeat_seed,
        apply_worker_state_changed_event,
    )

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )

    apply_task_dispatched_heartbeat_seed(reg, ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-judge-1",
            "child_id": "judge-refactor",
            "stage_id": "final-judge",
            "role_instance": "judge-refactor",
            "run_id": "run-fanout-judge-1-judge-refactor",
        },
    ))

    apply_worker_state_changed_event(reg, ZfEvent(
        type="worker.state.changed",
        actor="judge-refactor",
        payload={
            "from": "idle",
            "to": "busy",
            "reason": "dispatched fanout child fanout-judge-1/judge-refactor",
        },
    ))

    last_at, last_payload = reg.get_last_heartbeat("judge-refactor")
    assert last_at
    assert last_payload["current_task_id"] == "fanout:fanout-judge-1:judge-refactor"
    assert last_payload["state"] == "busy"
    assert last_payload["run_id"] == "run-fanout-judge-1-judge-refactor"
    assert last_payload["fanout_id"] == "fanout-judge-1"
    assert last_payload["child_id"] == "judge-refactor"
    assert last_payload["source"] == "worker.state.changed"


def test_agent_usage_keeps_latest_fanout_dispatch_task_when_lane_has_old_tasks(
    tmp_path: Path,
):
    from zf.runtime.housekeeping import apply_task_dispatched_heartbeat_seed
    from zf.runtime.usage_liveness import apply_agent_usage_liveness

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    old_task = Task(
        id="TASK-OLD",
        title="old",
        status="in_progress",
        assigned_to="dev-lane-1",
    )
    new_task = Task(
        id="TASK-NEW",
        title="new",
        status="in_progress",
        assigned_to="dev-lane-1",
    )
    apply_task_dispatched_heartbeat_seed(reg, ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "task_id": "TASK-NEW",
            "role_instance": "dev-lane-1",
            "run_id": "run-new",
        },
    ))

    apply_agent_usage_liveness(
        reg,
        ZfEvent(
            type="agent.usage",
            actor="dev-lane-1",
            payload={"context_usage_ratio": 0.42},
        ),
        tasks=[old_task, new_task],
    )

    _last_at, last_payload = reg.get_last_heartbeat("dev-lane-1")
    assert last_payload["current_task_id"] == "TASK-NEW"
    assert last_payload["state"] == "busy"
    assert last_payload["source"] == "agent.usage"


def test_agent_usage_ignores_terminal_fanout_task_id(
    tmp_path: Path,
):
    from zf.runtime.housekeeping import apply_task_dispatched_heartbeat_seed
    from zf.runtime.usage_liveness import apply_agent_usage_liveness

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    task = Task(
        id="TASK-OLD",
        title="old",
        status="in_progress",
        assigned_to="dev-lane-1",
    )
    dispatched = ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "queued-TASK-OLD-1",
            "run_id": "run-old",
            "task_id": "TASK-OLD",
            "role_instance": "dev-lane-1",
        },
    )
    completed = ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-1",
            "child_id": "queued-TASK-OLD-1",
            "run_id": "run-old",
            "task_id": "TASK-OLD",
            "role_instance": "dev-lane-1",
        },
    )
    apply_task_dispatched_heartbeat_seed(reg, dispatched)

    apply_agent_usage_liveness(
        reg,
        ZfEvent(
            type="agent.usage",
            actor="dev-lane-1",
            task_id="TASK-OLD",
            payload={"task_id": "TASK-OLD", "context_usage_ratio": 0.42},
        ),
        tasks=[task],
        events=[dispatched, completed],
    )

    _last_at, last_payload = reg.get_last_heartbeat("dev-lane-1")
    assert last_payload["current_task_id"] == ""
    assert last_payload["state"] == "active"
    assert last_payload["source"] == "agent.usage"


def test_apply_worker_state_changed_updates_role_session_state(tmp_path: Path):
    from zf.runtime.housekeeping import apply_worker_state_changed_event

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    reg.record_heartbeat("critic", {
        "instance_id": "critic",
        "current_task_id": "TASK-T1",
        "state": "busy",
        "last_action_ts": "2026-05-22T10:35:00+00:00",
    })

    apply_worker_state_changed_event(reg, ZfEvent(
        type="worker.state.changed",
        actor="critic",
        payload={
            "from": "busy",
            "to": "idle",
            "reason": "design.critique.done already recorded",
        },
    ))

    last_at, last_payload = reg.get_last_heartbeat("critic")
    assert last_at, "state change should refresh worker liveness metadata"
    assert last_payload["state"] == "idle"
    assert last_payload["source"] == "worker.state.changed"
    assert last_payload["reason"] == "design.critique.done already recorded"


def test_apply_worker_heartbeat_no_actor_is_noop(tmp_path: Path):
    """Heartbeat with empty actor → silently skip (defensive). The kernel
    should never reject the event, just discard it."""
    from zf.runtime.housekeeping import apply_worker_heartbeat_event

    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )

    apply_worker_heartbeat_event(reg, ZfEvent(
        type="worker.heartbeat",
        actor="",  # empty actor
        payload={"instance_id": "", "state": "idle"},
    ))

    # No instance recorded
    assert reg.get_last_heartbeat("") == (None, None) or \
        reg.get_last_heartbeat("") is None or \
        reg.get_last_heartbeat("")[0] is None


# ─── get_last_heartbeat returns sentinel when missing ────────────────────


def test_get_last_heartbeat_missing_returns_none(tmp_path: Path):
    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    reg.get_or_create("dev-1")

    ts, payload = reg.get_last_heartbeat("dev-1")
    assert ts is None
    assert payload is None


# ─── briefing injection ──────────────────────────────────────────────────


def _make_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t", state_dir=".zf"),
        roles=[],
    )


def _make_role(name: str, publishes: list[str]) -> RoleConfig:
    return RoleConfig(name=name, backend="mock", role_kind="writer", publishes=publishes)


def _make_task() -> Task:
    return Task(
        id="TASK-T1",
        title="task",
        contract=TaskContract(behavior="do thing"),
    )


@pytest.mark.parametrize(
    "role_name,publishes",
    [
        ("dev", ["dev.build.done"]),
        ("arch", ["arch.proposal.done"]),
        ("critic", ["design.critique.done"]),
        ("review", ["review.approved"]),
        ("test", ["test.passed"]),
        ("judge", ["judge.passed"]),
    ],
)
def test_briefing_includes_heartbeat_instruction_for_worker_roles(role_name, publishes):
    from zf.runtime.injection import generate_task_briefing

    briefing = generate_task_briefing(
        _make_config(),
        _make_role(role_name, publishes),
        _make_task(),
        feature=None,
    )

    assert "worker.heartbeat" in briefing
    assert "zf emit worker.heartbeat --task <task-id>" in briefing
    # And a hint about cadence (~60s)
    assert "60" in briefing or "heartbeat" in briefing.lower()


def test_orchestrator_role_briefing_omits_heartbeat_instruction():
    """The orchestrator role doesn't emit worker.heartbeat (it's the
    decision-maker, not a worker). Briefing should not nag it to."""
    from zf.runtime.injection import generate_task_briefing

    briefing = generate_task_briefing(
        _make_config(),
        _make_role("orchestrator", ["orchestrator.decision"]),
        _make_task(),
        feature=None,
    )

    assert "worker.heartbeat" not in briefing


# ─── wire-up: _apply_housekeeping must route worker.heartbeat ───────────


def test_wire_up_apply_housekeeping_handles_worker_heartbeat():
    """α-2 wire-up grep proof. orchestrator._apply_housekeeping must
    have a branch that handles worker.heartbeat events."""
    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/orchestrator.py"
    text = src.read_text(encoding="utf-8")

    assert "worker.heartbeat" in text, (
        "α-2 wire-up missing: orchestrator.py has no worker.heartbeat handling"
    )
    assert "apply_worker_heartbeat_event" in text, (
        "α-2 wire-up missing: orchestrator.py does not call "
        "apply_worker_heartbeat_event"
    )


def _seed_busy(reg, instance_id, *, stale=False, tmp_path=None):
    """Seed a busy worker heartbeat; if stale, backdate it past the throttle."""
    reg.get_or_create(instance_id)
    reg.record_heartbeat(instance_id, {"state": "busy", "current_task_id": "T1"})
    if stale:
        import yaml
        p = reg.path
        data = yaml.safe_load(p.read_text())
        data["instance_meta"][instance_id]["last_heartbeat_at"] = "2020-01-01T00:00:00+00:00"
        p.write_text(yaml.safe_dump(data))
        return RoleSessionRegistry(p, project_root=str(p.parent))
    return reg


def test_activity_heartbeat_refreshes_stale_busy_worker_from_hook(tmp_path: Path):
    """A tool-call hook proves an active worker is alive: refresh its liveness
    so the sweep does not falsely respawn it mid-long-turn (E2E 2026-07-09)."""
    from zf.runtime.housekeeping import apply_worker_activity_heartbeat

    reg = _seed_busy(
        RoleSessionRegistry(tmp_path / "role_sessions.yaml", project_root=str(tmp_path)),
        "dev-1", stale=True,
    )
    apply_worker_activity_heartbeat(
        reg, ZfEvent(type="codex.hook.post_tool_use", actor="dev-1",
                     ts="2026-07-09T04:00:00+00:00"),
    )
    _, payload = reg.get_last_heartbeat("dev-1")
    assert payload["source"] == "activity_liveness"
    assert payload["state"] == "busy"


def test_activity_heartbeat_claude_backend_parity(tmp_path: Path):
    from zf.runtime.housekeeping import apply_worker_activity_heartbeat

    reg = _seed_busy(
        RoleSessionRegistry(tmp_path / "role_sessions.yaml", project_root=str(tmp_path)),
        "dev-1", stale=True,
    )
    apply_worker_activity_heartbeat(
        reg, ZfEvent(type="claude.hook.pre_tool_use", actor="dev-1",
                     ts="2026-07-09T04:00:00+00:00"),
    )
    _, payload = reg.get_last_heartbeat("dev-1")
    assert payload["source"] == "activity_liveness"


def test_activity_heartbeat_throttled_when_fresh(tmp_path: Path):
    """A fresh heartbeat (< throttle gap) is not rewritten on every hook."""
    from zf.runtime.housekeeping import apply_worker_activity_heartbeat

    reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", project_root=str(tmp_path))
    reg.get_or_create("dev-1")
    reg.record_heartbeat("dev-1", {"state": "busy", "current_task_id": "T1"})
    apply_worker_activity_heartbeat(
        reg, ZfEvent(type="codex.hook.pre_tool_use", actor="dev-1"),
    )
    _, payload = reg.get_last_heartbeat("dev-1")
    assert payload.get("source") != "activity_liveness"  # throttled, kept original


def test_activity_heartbeat_noop_for_idle_infra_and_non_activity(tmp_path: Path):
    from zf.runtime.housekeeping import apply_worker_activity_heartbeat

    # idle worker → not gated by stuck sweep → no refresh
    reg = RoleSessionRegistry(tmp_path / "role_sessions.yaml", project_root=str(tmp_path))
    reg.get_or_create("dev-1")
    reg.record_heartbeat("dev-1", {"state": "idle"})
    apply_worker_activity_heartbeat(reg, ZfEvent(type="codex.hook.stop", actor="dev-1"))
    _, p = reg.get_last_heartbeat("dev-1")
    assert p.get("source") != "activity_liveness"

    # infra actor → never counts as agent activity
    reg2 = _seed_busy(
        RoleSessionRegistry(tmp_path / "rs2.yaml", project_root=str(tmp_path)),
        "run-manager", stale=True,
    )
    apply_worker_activity_heartbeat(
        reg2, ZfEvent(type="codex.hook.pre_tool_use", actor="run-manager",
                      ts="2026-07-09T04:00:00+00:00"))
    _, p2 = reg2.get_last_heartbeat("run-manager")
    assert p2.get("source") != "activity_liveness"

    # non-activity event → no-op
    reg3 = _seed_busy(
        RoleSessionRegistry(tmp_path / "rs3.yaml", project_root=str(tmp_path)),
        "dev-2", stale=True,
    )
    apply_worker_activity_heartbeat(
        reg3, ZfEvent(type="worker.state.changed", actor="dev-2",
                      ts="2026-07-09T04:00:00+00:00"))
    _, p3 = reg3.get_last_heartbeat("dev-2")
    assert p3.get("source") != "activity_liveness"
