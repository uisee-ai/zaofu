"""α-3: EventWatcher heartbeat sweep + proactive dispatch.

Per docs/design/36-zero-touch-long-horizon-roadmap.md §4.3 + backlog
backlogs/2026-05-17-1447-zero-touch-alpha-2-3-heartbeat-and-proactive-dispatch.md.

Builds on α-2 (worker.heartbeat protocol). Each kernel tick (~60s),
sweep role_sessions.yaml's last_heartbeat_at:

  - never heartbeated         → noop (not yet active)
  - busy + age > 90s          → emit worker.probe.silent (warn)
  - busy + age > 180s         → escalate to worker.stuck (existing)
  - idle + ready backlog item → proactive dispatch (auto-pick next task)

Tests focus on the pure sweep function. Wire-up integration with
EventWatcher.on_tick is covered by a separate test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _NoopTransport:
    pass


# ─── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def registry(tmp_path: Path) -> RoleSessionRegistry:
    reg = RoleSessionRegistry(
        tmp_path / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    return reg


def _heartbeat(
    registry: RoleSessionRegistry,
    instance_id: str,
    *,
    state: str,
    age_seconds: float,
    current_task_id: str = "",
) -> None:
    """Plant a heartbeat at (now - age_seconds) for the given instance."""
    registry.get_or_create(instance_id)
    payload = {
        "instance_id": instance_id,
        "state": state,
        "current_task_id": current_task_id,
        "last_action_ts": "n/a",
    }
    registry.record_heartbeat(instance_id, payload)
    # Override the kernel timestamp to simulate age
    fake_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    registry._meta[instance_id]["last_heartbeat_at"] = fake_ts
    registry._save()


# ─── event registration ──────────────────────────────────────────────────


def test_worker_probe_silent_in_known_types():
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    assert "worker.probe.silent" in KNOWN_EVENT_TYPES


def test_worker_probe_silent_in_wake_patterns():
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "worker.probe.silent" in WAKE_PATTERNS


# ─── sweep classification ────────────────────────────────────────────────


def test_sweep_fresh_busy_worker_is_quiet(registry):
    """A worker that heartbeated <90s ago is healthy — no signal."""
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    _heartbeat(registry, "dev-1", state="busy", age_seconds=30)

    result = sweep_heartbeats(registry=registry)

    assert "dev-1" not in result.silent_instances
    assert "dev-1" not in result.stuck_instances


def test_sweep_busy_at_silent_threshold_flagged(registry):
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    _heartbeat(registry, "dev-1", state="busy", age_seconds=120)

    result = sweep_heartbeats(registry=registry)

    assert "dev-1" in result.silent_instances
    # 120s < 180s → not yet stuck
    assert "dev-1" not in result.stuck_instances


def test_sweep_busy_at_stuck_threshold_flagged(registry):
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    _heartbeat(registry, "dev-1", state="busy", age_seconds=240)

    result = sweep_heartbeats(registry=registry)

    # Stuck implies silent (both reported, so the orchestrator can pick
    # the right severity branch)
    assert "dev-1" in result.stuck_instances


def test_sweep_idle_worker_listed_for_proactive_dispatch(registry):
    """An idle worker (per its own heartbeat) is a candidate for the
    next backlog item, regardless of last_heartbeat_at age."""
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    _heartbeat(registry, "dev-1", state="idle", age_seconds=30)

    result = sweep_heartbeats(registry=registry)

    assert "dev-1" in result.idle_instances


def test_sweep_idle_silent_worker_is_still_idle_not_silent(registry):
    """When a worker's last heartbeat said state=idle, "silent" is the
    expected behavior (idle workers may stop emitting frequent
    heartbeats). Don't double-flag as silent."""
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    _heartbeat(registry, "dev-1", state="idle", age_seconds=200)

    result = sweep_heartbeats(registry=registry)

    assert "dev-1" in result.idle_instances
    assert "dev-1" not in result.silent_instances
    assert "dev-1" not in result.stuck_instances


def test_sweep_never_heartbeated_worker_is_quiet(registry):
    """A registered instance that never sent a heartbeat is not yet
    actively dispatched — silent classification would over-escalate."""
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    registry.get_or_create("dev-1")  # registered but never heartbeated

    result = sweep_heartbeats(registry=registry)

    assert "dev-1" not in result.silent_instances
    assert "dev-1" not in result.stuck_instances
    assert "dev-1" not in result.idle_instances


def test_sweep_handles_mixed_population(registry):
    """Multiple instances with different states are classified
    independently."""
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    _heartbeat(registry, "dev-1", state="busy", age_seconds=30)   # fresh
    _heartbeat(registry, "dev-2", state="busy", age_seconds=120)  # silent
    _heartbeat(registry, "dev-3", state="busy", age_seconds=300)  # stuck
    _heartbeat(registry, "dev-4", state="idle", age_seconds=10)   # idle

    result = sweep_heartbeats(registry=registry)

    assert result.silent_instances == ["dev-2"]
    assert result.stuck_instances == ["dev-3"]
    assert result.idle_instances == ["dev-4"]


def test_sweep_thresholds_configurable(registry):
    """Operator can tune silent/stuck thresholds for slow-LLM scenarios."""
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    _heartbeat(registry, "dev-1", state="busy", age_seconds=120)

    # With higher silent_threshold, 120s is not silent
    result = sweep_heartbeats(
        registry=registry,
        silent_threshold_s=150.0,
        stuck_threshold_s=300.0,
    )
    assert "dev-1" not in result.silent_instances

    # With lower threshold, 120s is silent
    result = sweep_heartbeats(
        registry=registry,
        silent_threshold_s=60.0,
        stuck_threshold_s=300.0,
    )
    assert "dev-1" in result.silent_instances


def test_sweep_per_instance_stuck_thresholds(registry):
    """The runtime can honor role-specific stuck thresholds."""
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    _heartbeat(registry, "arch", state="busy", age_seconds=240)
    _heartbeat(registry, "dev", state="busy", age_seconds=240)

    result = sweep_heartbeats(
        registry=registry,
        stuck_threshold_s=180.0,
        stuck_thresholds_s={"arch": 300.0},
    )

    assert "arch" not in result.stuck_instances
    assert "arch" in result.silent_instances
    assert "dev" in result.stuck_instances


def test_sweep_malformed_timestamp_is_safe(registry):
    """A garbled last_heartbeat_at must not crash the sweep."""
    from zf.runtime.heartbeat_sweep import sweep_heartbeats

    registry.get_or_create("dev-1")
    registry._meta["dev-1"]["last_heartbeat_at"] = "not a timestamp"
    registry._meta["dev-1"]["last_heartbeat_payload"] = {"state": "busy"}
    registry._save()

    # No raise, instance excluded from all categories.
    result = sweep_heartbeats(registry=registry)

    assert "dev-1" not in result.silent_instances
    assert "dev-1" not in result.stuck_instances


# ─── wire-up: emit events ───────────────────────────────────────────────


def test_wire_up_orchestrator_emits_probe_silent_for_silent_workers(tmp_path: Path):
    """When the orchestrator runs a tick sweep and a busy worker has
    been silent past threshold, a worker.probe.silent event must hit
    events.jsonl."""
    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/orchestrator.py"
    text = src.read_text(encoding="utf-8")
    assert "worker.probe.silent" in text, (
        "α-3 wire-up missing: orchestrator.py does not emit worker.probe.silent"
    )
    assert "sweep_heartbeats" in text or "heartbeat_sweep" in text, (
        "α-3 wire-up missing: orchestrator.py does not call sweep_heartbeats"
    )


def test_orchestrator_sweep_ignores_stale_completed_handoff_worker(
    tmp_path: Path,
) -> None:
    """Heartbeat sweep must not flag a role that no longer owns a task."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _heartbeat(
        registry,
        "arch",
        state="busy",
        age_seconds=400,
        current_task_id="TASK-1",
    )
    # The task has already been reassigned away from arch after handoff.
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="handoff",
        status="in_progress",
        assigned_to="critic",
    ))
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="arch", backend="mock", instance_id="arch"),
            RoleConfig(name="critic", backend="mock", instance_id="critic"),
        ],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]

    orch._run_heartbeat_sweep()  # type: ignore[attr-defined]

    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "worker.stuck" not in events
    assert "worker.probe.silent" not in events


def test_orchestrator_sweep_ignores_busy_worker_for_missing_task(
    tmp_path: Path,
) -> None:
    """A worker heartbeat tied to a removed terminal task is stale evidence."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _heartbeat(
        registry,
        "dev-1",
        state="busy",
        age_seconds=400,
        current_task_id="TASK-DONE",
    )
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock", instance_id="dev-1")],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]

    orch._run_heartbeat_sweep()  # type: ignore[attr-defined]

    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "worker.stuck" not in events
    assert "worker.probe.silent" not in events


def test_task_dispatched_resets_stale_busy_heartbeat_before_sweep(
    tmp_path: Path,
) -> None:
    """A fresh dispatch is kernel evidence that the worker just got work."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _heartbeat(
        registry,
        "arch",
        state="busy",
        age_seconds=400,
        current_task_id="TASK-1",
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="handoff",
        status="in_progress",
        assigned_to="arch",
    ))
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="arch", backend="mock", instance_id="arch")],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]

    orch._apply_housekeeping(ZfEvent(  # type: ignore[attr-defined]
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-1",
        payload={
            "assignee": "arch",
            "role": "arch",
            "dispatch_id": "disp-1",
        },
    ))
    orch._run_heartbeat_sweep()  # type: ignore[attr-defined]

    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "worker.stuck" not in events
    assert "worker.probe.silent" not in events
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _, payload = registry.get_last_heartbeat("arch")
    assert payload["source"] == "task.dispatched"


def test_claude_watchdog_respawn_clears_session_for_fresh_spawn(
    tmp_path: Path,
) -> None:
    """R17/R18 (1b): a watchdog respawn of a dead claude worker must CLEAR the
    session so the respawn is a fresh `claude --session-id <new>`, not
    `--resume <uuid>` which fails 'session <uuid> is already in use' when the old
    session lock survives the pane termination → 'did not become ready after
    spawn' → infra cascade → safe-halt (R17 dev-lane-4)."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(tmp_path),
    )
    registry.get_or_create("dev-1", backend="claude-code")  # prior spawn session
    assert registry.get("dev-1") is not None
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="claude-code", instance_id="dev-1")],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]

    # stub the heavy spawn chain. --resume (first spawn) does NOT become ready
    # (simulates "session already in use"); the fresh-session fallback does.
    spawns: list[str] = []

    class _FakeCoord:
        def spawn(self, role, cwd=None):  # noqa: ANN001
            spawns.append(str(cwd))

    ready_results = iter([False, True])
    orch._get_spawn_coordinator = lambda: _FakeCoord()         # type: ignore[assignment]
    orch._wait_role_ready = lambda role: next(ready_results)   # type: ignore[assignment]
    orch._inject_recovery_briefing = lambda role: True         # type: ignore[assignment]

    orch._respawn_instance(cfg.roles[0])  # type: ignore[attr-defined]

    # --resume didn't become ready → fell back: cleared the session + respawned
    assert len(spawns) == 2  # resume attempt, then fresh
    # the claude session was cleared on disk → the fallback spawn is a fresh
    # --session-id (is_respawn=False), no "already in use"
    fresh = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(tmp_path),
    )
    assert fresh.get("dev-1") is None


def test_heartbeat_stuck_skips_worker_that_finished_its_dispatch(
    tmp_path: Path,
) -> None:
    """R18: a writer that emitted dev.build.done (task still in_progress,
    awaiting review/integration) legitimately stops heartbeating — it has no
    active work. The heartbeat-age stuck path (orchestrator.py, "no heartbeat
    in Xs") must NOT flag it. Fix 0886466 only covered the pane-output stuck
    path; this is the second mechanism that fired 20× in R18. A false heartbeat
    stuck → respawn cascade → safe_halt on a process death."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(tmp_path),
    )
    # stale heartbeat (400s) > stuck_threshold (300s) → lands in stuck_instances
    _heartbeat(registry, "dev-1", state="busy", age_seconds=400, current_task_id="T")
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T", title="impl", status="in_progress",
        assigned_to="dev-1", active_dispatch_id="disp-1",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id="T",
        payload={"dispatch_id": "disp-1", "role": "dev-1"},
    ))
    log.append(ZfEvent(  # the writer finished its build — now legitimately quiet
        type="dev.build.done", actor="dev-1", task_id="T", payload={},
    ))
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(
            name="dev", backend="mock", instance_id="dev-1",
            stuck_threshold_seconds=300,
        )],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]

    orch._run_heartbeat_sweep()  # type: ignore[attr-defined]

    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"type": "worker.stuck"' not in events and '"type":"worker.stuck"' not in events


def test_progress_event_without_dispatch_id_credits_current_dispatch(
    tmp_path: Path,
) -> None:
    """R17 dev-lane-4: a writer's ``dev.build.done`` carries ``task_id`` but no
    ``payload.dispatch_id``. The dispatch-scoped progress lookup must still
    credit it to the current dispatch (it sits AFTER the task.dispatched by
    index, so dispatch_idx already scopes it), else a writer that finished its
    build looks "silent with no progress" → flagged stuck → respawn → infra
    cascade → safe_halt, preempting the legitimate review→rework loop."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.dispatched", actor="orchestrator", task_id="T",
        payload={"dispatch_id": "disp-1", "role": "dev-1"},
    ))
    log.append(ZfEvent(  # writer completion — note: NO dispatch_id in payload
        type="dev.build.done", actor="dev-1", task_id="T", payload={},
    ))
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock", instance_id="dev-1")],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]

    ev = orch._latest_unrejected_progress_event_for_dispatch(  # type: ignore[attr-defined]
        "T", "disp-1",
    )

    assert ev is not None, "dev.build.done (no dispatch_id) must count as progress"
    assert ev.type == "dev.build.done"


def test_agent_usage_refreshes_busy_worker_liveness_before_sweep(
    tmp_path: Path,
) -> None:
    """Provider activity during a long turn should not look stuck."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _heartbeat(
        registry,
        "arch",
        state="busy",
        age_seconds=400,
        current_task_id="TASK-1",
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="handoff",
        status="in_progress",
        assigned_to="arch",
    ))
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="arch", backend="mock", instance_id="arch")],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]

    orch._apply_housekeeping(ZfEvent(  # type: ignore[attr-defined]
        type="agent.usage",
        actor="arch",
        task_id="TASK-1",
        payload={
            "backend": "codex",
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "context_usage_ratio": 0.72,
        },
    ))
    orch._run_heartbeat_sweep()  # type: ignore[attr-defined]

    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "worker.stuck" not in events
    assert "worker.probe.silent" not in events
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _, payload = registry.get_last_heartbeat("arch")
    assert payload["source"] == "agent.usage"
    assert payload["current_task_id"] == "TASK-1"


def test_orchestrator_sweep_suppresses_stuck_during_open_codex_turn(
    tmp_path: Path,
) -> None:
    """A long Codex model turn is liveness evidence even without heartbeat."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _heartbeat(
        registry,
        "arch",
        state="busy",
        age_seconds=400,
        current_task_id="TASK-1",
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="handoff",
        status="in_progress",
        assigned_to="arch",
    ))
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(
                name="arch",
                backend="codex",
                instance_id="arch",
                stuck_threshold_seconds=300.0,
            )
        ],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]
    orch.event_writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="arch",
        payload={"session_id": "s1", "turn_id": "t1"},
    ))

    orch._run_heartbeat_sweep()  # type: ignore[attr-defined]

    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "worker.stuck" not in events
    assert "worker.probe.silent" in events
    assert "provider_turn_in_flight" in events


def test_orchestrator_sweep_allows_stuck_after_codex_stop(
    tmp_path: Path,
) -> None:
    """A closed Codex turn must not mask a stale busy heartbeat."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _heartbeat(
        registry,
        "arch",
        state="busy",
        age_seconds=400,
        current_task_id="TASK-1",
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="handoff",
        status="in_progress",
        assigned_to="arch",
    ))
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(
                name="arch",
                backend="codex",
                instance_id="arch",
                stuck_threshold_seconds=300.0,
            )
        ],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]
    orch.event_writer.append(ZfEvent(
        type="codex.hook.user_prompt_submit",
        actor="arch",
        payload={"session_id": "s1", "turn_id": "t1"},
    ))
    orch.event_writer.append(ZfEvent(
        type="codex.hook.stop",
        actor="arch",
        payload={"session_id": "s1", "turn_id": "t1"},
    ))

    orch._run_heartbeat_sweep()  # type: ignore[attr-defined]

    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "worker.stuck" in events


def test_worker_state_changed_idle_resets_stale_busy_heartbeat_before_sweep(
    tmp_path: Path,
) -> None:
    """A completed gate may transition busy -> idle without another heartbeat."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _heartbeat(
        registry,
        "critic",
        state="busy",
        age_seconds=400,
        current_task_id="TASK-1",
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="handoff",
        status="in_progress",
        assigned_to="critic",
    ))
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="critic", backend="mock", instance_id="critic")],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]

    orch._apply_housekeeping(ZfEvent(  # type: ignore[attr-defined]
        type="worker.state.changed",
        actor="critic",
        payload={"from": "busy", "to": "idle", "reason": "gate completed"},
    ))
    orch._run_heartbeat_sweep()  # type: ignore[attr-defined]

    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "worker.stuck" not in events
    assert "worker.probe.silent" not in events
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _, payload = registry.get_last_heartbeat("critic")
    assert payload["state"] == "idle"
    assert payload["source"] == "worker.state.changed"


def test_stage_completion_releases_actor_and_usage_preserves_idle(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "memory").mkdir()
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _heartbeat(
        registry,
        "critic",
        state="busy",
        age_seconds=400,
        current_task_id="TASK-1",
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="handoff",
        status="in_progress",
        assigned_to="critic",
    ))
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock", instance_id="orchestrator"),
            RoleConfig(name="critic", backend="mock", instance_id="critic"),
        ],
    )
    orch = Orchestrator(state_dir, cfg, _NoopTransport())  # type: ignore[arg-type]

    orch._apply_housekeeping(ZfEvent(  # type: ignore[attr-defined]
        type="design.critique.done",
        actor="critic",
        task_id="TASK-1",
        payload={"verdict": "approve"},
    ))
    orch._apply_housekeeping(ZfEvent(  # type: ignore[attr-defined]
        type="agent.usage",
        actor="critic",
        payload={
            "usage": {"input_tokens": 10, "output_tokens": 1},
            "context_usage_ratio": 0.5,
        },
    ))
    orch._run_heartbeat_sweep()  # type: ignore[attr-defined]

    events = (state_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "worker.stuck" not in events
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    _, payload = registry.get_last_heartbeat("critic")
    assert payload["state"] == "idle"
    assert payload["current_task_id"] == "TASK-1"
    assert payload["source"] == "agent.usage"
