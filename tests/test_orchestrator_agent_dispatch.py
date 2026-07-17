"""Tests for Layer 2 dispatch path — _notify_orchestrator_agent (E1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ConstraintsConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    WorkflowFastPathConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_briefing import build_orchestrator_briefing
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import (
    AttachHandle,
    DispatchContext,
    TmuxTransport,
    TransportAdapter,
)
from zf.core.events.model import ZfEvent as ZE


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    (sd / "logs").mkdir()
    EventLog(sd / "events.jsonl").append(ZE(type="loop.started", actor="zf-cli"))
    from zf.core.state.session import SessionStore
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config_with_orchestrator():
    return ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[
            RoleConfig(
                name="orchestrator",
                backend="claude-code",
                transport="stream-json",
                permission_mode="allowlist",
                allowed_tools=[
                    "Bash(zf feature *)",
                    "Bash(zf kanban *)",
                    "Bash(zf emit *)",
                    "Bash(zf events *)",
                    "Read",
                ],
                stages=["meta"],
            ),
            RoleConfig(name="dev", backend="mock"),
        ],
    )


@pytest.fixture
def config_no_orchestrator():
    return ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )


class _RecordingTransport(TmuxTransport):
    """Transport that records send_task calls without actually sending."""

    def __init__(self):
        super().__init__(TmuxSession(session_name="rec", dry_run=True))
        self.sent: list[tuple] = []
        self.contexts: list[DispatchContext | None] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):
        self.sent.append((role_name, briefing_path, prompt))
        self.contexts.append(context)


class _FailingTransport(_RecordingTransport):
    def send_task(self, role_name, briefing_path, prompt, *, context=None):
        err = RuntimeError("pane is not running an agent process")
        err.backend = context.backend if context else ""
        err.current_command = "node"
        err.dead_reason = "node_without_agent_wrapper"
        err.process_probe = {
            "available": True,
            "pane_pid": "4242",
            "current_command": "node",
            "processes": [
                {"pid": "4243", "ppid": "4242", "command": "node server.js"},
            ],
        }
        raise err


class _PaneDeadTransport(_RecordingTransport):
    def send_task(self, role_name, briefing_path, prompt, *, context=None):
        err = RuntimeError(
            f"refusing to send task to {role_name}: pane is not running "
            "an agent process (current_command=node, reason=pane_dead)"
        )
        err.backend = context.backend if context else ""
        err.current_command = "node"
        err.dead_reason = "pane_dead"
        err.process_probe = {
            "available": True,
            "pane_pid": "4242",
            "current_command": "node",
            "processes": [],
        }
        raise err


class _PollingTransport(_RecordingTransport):
    def __init__(self, events: list[ZfEvent]):
        super().__init__()
        self.events = events

    def poll_events(self) -> list[ZfEvent]:
        pending = self.events
        self.events = []
        return pending


class _RecordingWriter:
    def __init__(self) -> None:
        self.events: list[ZfEvent] = []

    def append(self, event: ZfEvent) -> ZfEvent:
        self.events.append(event)
        return event


class _NoopTransport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        raise AssertionError("noop transport must not receive mutating dispatch")


# -- briefing assembler tests --

def test_orchestrator_briefing_contains_trigger_event(state_dir: Path, config_with_orchestrator):
    trigger = ZE(type="dev.build.done", actor="dev", task_id="T1")
    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config_with_orchestrator,
        trigger_event=trigger,
    )
    assert "dev.build.done" in briefing
    assert "T1" in briefing
    assert "## Trigger" in briefing


def test_orchestrator_briefing_contains_feature_list(state_dir: Path, config_with_orchestrator):
    fs = FeatureStore(state_dir / "feature_list.json")
    fs.add(Feature(title="OAuth login", id="F-001"))
    fs.add(Feature(title="Profile page", id="F-002"))
    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config_with_orchestrator,
        trigger_event=ZE(type="user.message", actor="human"),
    )
    assert "## Features" in briefing
    assert "F-001" in briefing
    assert "OAuth login" in briefing
    assert "F-002" in briefing


def test_orchestrator_briefing_contains_kanban(state_dir: Path, config_with_orchestrator):
    ts = TaskStore(state_dir / "kanban.json")
    ts.add(Task(id="T1", title="design oauth", status="in_progress"))
    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config_with_orchestrator,
        trigger_event=ZE(type="user.message", actor="human"),
    )
    assert "## Kanban" in briefing
    assert "T1" in briefing
    assert "design oauth" in briefing


def test_orchestrator_briefing_contains_recent_events(state_dir: Path, config_with_orchestrator):
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZE(type="task.dispatched", actor="orchestrator", task_id="T1"))
    log.append(ZE(type="dev.build.done", actor="dev", task_id="T1"))
    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config_with_orchestrator,
        trigger_event=ZE(type="dev.build.done", actor="dev", task_id="T1"),
    )
    assert "## Recent Events" in briefing
    assert "task.dispatched" in briefing


def test_orchestrator_briefing_lists_available_tools(state_dir: Path, config_with_orchestrator):
    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config_with_orchestrator,
        trigger_event=ZE(type="user.message", actor="human"),
    )
    assert "## Available Tools" in briefing
    assert "zf feature" in briefing
    assert "zf kanban" in briefing
    assert "zf emit" in briefing


def test_orchestrator_briefing_renders_fast_path_policy(
    state_dir: Path,
    config_with_orchestrator,
):
    config_with_orchestrator.workflow = WorkflowConfig(
        fast_path=WorkflowFastPathConfig(
            enabled=True,
            max_scope_files=2,
            blocked_keywords=["runtime"],
        )
    )
    trigger = ZE(type="user.message", actor="user")

    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config_with_orchestrator,
        trigger_event=trigger,
    )

    assert "Small Task Fast Path" in briefing
    assert "scope 文件数 <= 2" in briefing
    assert "不要拉起 arch/critic/judge" in briefing


def test_orchestrator_briefing_uses_machine_readable_task_creation(
    state_dir: Path, config_with_orchestrator,
):
    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config_with_orchestrator,
        trigger_event=ZE(type="user.message", actor="human"),
    )

    assert "--id-only" in briefing
    assert "do not parse human-readable output" in briefing
    assert 'task_id=$(zf kanban add "$feature_id" "Task title" --id-only)' in briefing


def test_orchestrator_briefing_uses_portable_payload_files(
    state_dir: Path,
    config_with_orchestrator,
):
    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config_with_orchestrator,
        trigger_event=ZE(type="user.message", actor="human"),
    )

    assert "--payload-file" in briefing
    assert "python3 - <<'PY'" in briefing
    assert "不要依赖 `jq`" in briefing
    assert '${ZF_STATE_DIR:-.zf}/tmp' in briefing
    assert 'STATE_TMP="$state_tmp" python3' in briefing
    assert ".zf/tmp" not in briefing


def test_transport_provider_events_append_through_event_writer(
    state_dir: Path,
    config_with_orchestrator,
):
    event = ZfEvent(type="agent.text", actor="orchestrator",
                    payload={"text": "hi"})
    transport = _PollingTransport([event])
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)
    writer = _RecordingWriter()
    orch.event_writer = writer

    orch._drain_transport_events()

    assert writer.events == [event]


def test_orchestrator_briefing_handles_empty_state(state_dir: Path, config_with_orchestrator):
    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=config_with_orchestrator,
        trigger_event=ZE(type="user.message", actor="human"),
    )
    # No features, no tasks — must still produce valid briefing
    assert "## Features" in briefing
    assert "## Kanban" in briefing


# -- dispatch path tests --

def test_notify_orchestrator_agent_dispatches_via_transport(
    state_dir: Path, config_with_orchestrator
):
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)
    event = ZE(type="dev.build.done", actor="dev", task_id="T1")
    orch._notify_orchestrator_agent(event)
    assert len(transport.sent) == 1
    role_name, briefing_path, prompt = transport.sent[0]
    assert role_name == "orchestrator"
    assert briefing_path.exists()
    assert "trigger" in prompt.lower() or "decide" in prompt.lower()


def test_send_transport_task_rejects_noop_transport_mutating_dispatch(
    state_dir: Path,
    config_with_orchestrator,
    tmp_path: Path,
) -> None:
    orch = Orchestrator(state_dir, config_with_orchestrator, _NoopTransport())

    with pytest.raises(RuntimeError, match="transport dispatch unavailable"):
        orch._send_transport_task(
            "orchestrator",
            tmp_path / "brief.md",
            "prompt",
            DispatchContext(task_id="T1", trace_id="trace-1"),
        )

    failed = [
        event for event in orch.event_log.read_all()
        if event.type == "orchestrator.dispatch_failed"
    ][-1]
    assert failed.task_id == "T1"
    assert failed.correlation_id == "trace-1"
    assert failed.payload["diagnostic_mode"] is True
    assert failed.payload["transport"] == "_NoopTransport"


def test_reader_write_policy_identifies_validation_artifact_boundary(
    state_dir: Path,
    config_with_orchestrator,
) -> None:
    orch = Orchestrator(state_dir, config_with_orchestrator, _RecordingTransport())

    payload = orch._reader_write_policy_payload(
        "?? docs/validation/review.md\n M docs/validation/summary.json\n"
    )

    assert payload["policy"] == "reader_artifact_policy_missing"
    assert payload["dirty_paths"] == [
        "docs/validation/review.md",
        "docs/validation/summary.json",
    ]
    assert "artifact" in payload["recommended_fix"]


def test_notify_orchestrator_agent_sends_dispatch_context(
    state_dir: Path, config_with_orchestrator
):
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)
    event = ZE(
        type="dev.build.done",
        actor="dev",
        task_id="T1",
        correlation_id="trace-1",
    )

    orch._notify_orchestrator_agent(event)

    context = transport.contexts[0]
    assert context is not None
    assert context.trace_id == "trace-1"
    assert context.task_id == "T1"
    assert context.role_name == "orchestrator"
    assert context.instance_id == "orchestrator"
    assert context.run_id is not None


def test_notify_orchestrator_agent_dispatch_failed_includes_process_probe(
    state_dir: Path, config_with_orchestrator
):
    transport = _FailingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)

    orch._notify_orchestrator_agent(
        ZE(type="dev.build.done", actor="dev", task_id="T1")
    )

    failed = [
        event for event in orch.event_log.read_all()
        if event.type == "orchestrator.dispatch_failed"
    ][-1]
    assert failed.payload["backend"] == "claude-code"
    assert failed.payload["current_command"] == "node"
    assert failed.payload["dead_reason"] == "node_without_agent_wrapper"
    assert failed.payload["process_probe"]["processes"][0]["command"] == "node server.js"


def test_notify_orchestrator_agent_pane_dead_requests_respawn_retry(
    state_dir: Path,
    config_with_orchestrator,
):
    transport = _PaneDeadTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)

    orch._notify_orchestrator_agent(
        ZE(
            type="dev.build.done",
            id="evt-trigger-1",
            actor="dev",
            task_id="T1",
            correlation_id="trace-1",
        )
    )

    events = orch.event_log.read_all()
    failed = [event for event in events if event.type == "orchestrator.dispatch_failed"][-1]
    respawn = [event for event in events if event.type == "worker.respawn.requested"]
    retry = [
        event for event in events
        if event.type == "orchestrator.dispatch.retry_requested"
    ]
    assert failed.payload["dead_reason"] == "pane_dead"
    assert respawn[-1].payload["instance_id"] == "orchestrator"
    assert retry[-1].payload["trigger_event_id"] == "evt-trigger-1"
    assert retry[-1].payload["max_attempts"] == 1


def test_notify_no_op_when_orchestrator_role_missing(
    state_dir: Path, config_no_orchestrator
):
    """If config has no orchestrator role, notify is a no-op (not an error)."""
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_no_orchestrator, transport)
    event = ZE(type="dev.build.done", actor="dev", task_id="T1")
    orch._notify_orchestrator_agent(event)
    assert transport.sent == []


def test_writer_dependency_task_ids_include_archived_terminal_blockers(
    state_dir: Path,
    config_no_orchestrator,
):
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-A", title="upstream", status="in_progress"))
    store.update("TASK-A", status="done")
    task = Task(
        id="TASK-B",
        title="downstream",
        status="backlog",
        blocked_by=["TASK-A", "MISSING"],
    )
    orch = Orchestrator(state_dir, config_no_orchestrator, _RecordingTransport())

    assert orch._writer_dependency_task_ids(task) == ["TASK-A"]  # type: ignore[attr-defined]


def test_briefing_file_written_to_briefings_dir(
    state_dir: Path, config_with_orchestrator
):
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)
    orch._notify_orchestrator_agent(ZE(type="user.message", actor="human"))
    briefings = list((state_dir / "briefings").glob("orchestrator-*.md"))
    assert len(briefings) >= 1
    content = briefings[0].read_text()
    assert "user.message" in content


# -- E4: conditional dispatch tests --

def test_react_with_orchestrator_role_routes_to_layer2(
    state_dir: Path, config_with_orchestrator
):
    """When orchestrator role is configured, _react_to_events should NOT
    fire the deterministic _on_* handlers; instead it dispatches every event
    to Layer 2 via _notify_orchestrator_agent."""
    from zf.core.task.store import TaskStore
    from zf.core.task.schema import Task
    transport = _RecordingTransport()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))

    orch = Orchestrator(state_dir, config_with_orchestrator, transport)
    event = ZE(type="dev.build.done", actor="dev", task_id="T1")
    orch.run_once(events=[event])

    # Layer 2 was notified
    assert len(transport.sent) == 1
    role, _, _ = transport.sent[0]
    assert role == "orchestrator"
    # Task status is UNCHANGED — Layer 1 did not move it (Layer 2 will)
    assert store.get("T1").status == "in_progress"


def test_react_without_orchestrator_role_uses_legacy_handlers(
    state_dir: Path, config_no_orchestrator
):
    """When no orchestrator role is configured (legacy mode), the deterministic
    _on_* handlers fire and move the task as before."""
    from zf.core.task.store import TaskStore
    from zf.core.task.schema import Task
    transport = _RecordingTransport()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))

    orch = Orchestrator(state_dir, config_no_orchestrator, transport)
    event = ZE(type="dev.build.done", actor="dev", task_id="T1")
    orch.run_once(events=[event])

    # No Layer 2 transport call
    assert transport.sent == []
    # Task moved by deterministic handler
    assert store.get("T1").status == "review"


def test_react_with_orchestrator_role_dispatches_user_message_event(
    state_dir: Path, config_with_orchestrator
):
    """A user.message event has no Python handler in legacy mode, but in
    Layer 2 mode it should still dispatch to the orchestrator agent."""
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)
    event = ZE(type="user.message", actor="human", payload={"message": "hi"})
    orch.run_once(events=[event])
    assert len(transport.sent) == 1


# -- wake coalescing (2026-05-28: avoid N back-to-back wakes for a burst) --

def _dispatch_skipped(state_dir: Path) -> list[ZE]:
    return [
        e for e in EventLog(state_dir / "events.jsonl").read_all()
        if e.type == "orchestrator.dispatch_skipped"
    ]


def test_wake_coalescing_first_wake_not_delayed(
    state_dir: Path, config_with_orchestrator
):
    """Leading edge: the first wake fires immediately even with interval > 0
    (_layer2_last_wake_at starts at 0.0)."""
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)
    assert orch._layer2_wake_min_interval_s > 0
    orch._notify_orchestrator_agent(ZE(type="dev.build.done", actor="dev", task_id="T1"))
    assert len(transport.sent) == 1


def test_wake_coalescing_suppresses_burst_after_leading_wake(
    state_dir: Path, config_with_orchestrator
):
    """A second trigger within the interval is suppressed (not sent) and
    remembered as pending; a dispatch_skipped(wake_coalesced) is emitted."""
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)

    orch._notify_orchestrator_agent(ZE(type="dev.build.done", actor="dev", task_id="A"))
    orch._notify_orchestrator_agent(ZE(type="test.failed", actor="test", task_id="B"))

    # Only the leading wake was sent.
    assert len(transport.sent) == 1
    # The suppressed event is remembered (in the pending list), not dropped.
    assert len(orch._layer2_pending) == 1
    assert orch._layer2_pending[-1].task_id == "B"
    skipped = _dispatch_skipped(state_dir)
    assert len(skipped) == 1
    assert skipped[0].payload["reason"] == "wake_coalesced"


def test_wake_coalescing_trailing_flush_sends_pending(
    state_dir: Path, config_with_orchestrator
):
    """Once the interval elapses, run_once flushes the pending wake — the
    suppressed event is delayed, never dropped."""
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)

    orch._notify_orchestrator_agent(ZE(type="dev.build.done", actor="dev", task_id="A"))
    orch._notify_orchestrator_agent(ZE(type="test.failed", actor="test", task_id="B"))
    assert len(transport.sent) == 1

    # Simulate the interval elapsing, then let an idle tick drive the flush.
    orch._layer2_last_wake_at -= 100
    orch.run_once(events=[])

    assert len(transport.sent) == 2
    assert orch._layer2_pending == []


# -- batch coalescing (doc 66 §14.0: one turn per run_once, blocking transport) --

def test_batch_coalesces_burst_into_one_multi_trigger_turn(
    state_dir: Path, config_with_orchestrator
):
    """A burst of triggers in ONE run_once batch fires exactly ONE Layer 2 turn
    (not one per event), and the prompt flags the coalesced count."""
    from zf.core.task.store import TaskStore
    from zf.core.task.schema import Task
    transport = _RecordingTransport()
    store = TaskStore(state_dir / "kanban.json")
    for tid in ("A", "B", "C"):
        store.add(Task(id=tid, title="x", status="in_progress", assigned_to="dev"))
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)

    events = [
        ZE(type="dev.build.done", actor="dev", task_id="A"),
        ZE(type="dev.build.done", actor="dev", task_id="B"),
        ZE(type="dev.build.done", actor="dev", task_id="C"),
    ]
    orch.run_once(events=events)

    assert len(transport.sent) == 1
    assert orch._layer2_pending == []
    _role, _briefing_path, prompt = transport.sent[0]
    assert "coalesced" in prompt


def test_batch_dedups_same_task_repeat_triggers(
    state_dir: Path, config_with_orchestrator
):
    """Repeat (type, task_id) within a batch dedups to the latest (§14.3);
    a distinct (type, task_id) stays separate."""
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)

    orch._layer2_in_batch = True
    orch._notify_orchestrator_agent(ZE(type="dev.build.done", actor="dev", task_id="A"))
    orch._notify_orchestrator_agent(ZE(type="dev.build.done", actor="dev", task_id="A"))
    orch._notify_orchestrator_agent(ZE(type="test.failed", actor="test", task_id="A"))
    assert len(orch._layer2_pending) == 2


def test_wake_coalescing_disabled_when_interval_zero(
    state_dir: Path, config_with_orchestrator
):
    """interval == 0 restores the legacy per-event wake behavior."""
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)
    orch._layer2_wake_min_interval_s = 0.0

    orch._notify_orchestrator_agent(ZE(type="dev.build.done", actor="dev", task_id="A"))
    orch._notify_orchestrator_agent(ZE(type="test.failed", actor="test", task_id="B"))

    assert len(transport.sent) == 2
    assert orch._layer2_pending == []
    assert _dispatch_skipped(state_dir) == []


def test_same_trigger_streak_enters_exponential_backoff(
    state_dir: Path, config_with_orchestrator, monkeypatch
):
    """FIX-5②(bizsim r4 $697 空转账单):同型触发连续 commit 超过免额后,
    Layer-2 唤醒进入指数退避;异型触发一到即复位,新鲜信号不受阻。"""
    clock = [1000.0]
    monkeypatch.setattr("zf.runtime.orchestrator.time.time", lambda: clock[0])

    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config_with_orchestrator, transport)

    # 同型事件每 10s 一发(> 基础窗 5s):免额 3 次 + 退避窗内前两次仍放行
    # (2^1=10s 边界不小于间隔),直至窗口翻倍超过到达间隔后被吸收。
    for _ in range(6):
        orch._notify_orchestrator_agent(
            ZE(type="run.manager.tick.completed", actor="run-manager")
        )
        clock[0] += 10.0
    same_type_sends = len(transport.sent)
    assert same_type_sends < 6, "同型连发未被退避吸收"

    absorbed = [
        e for e in EventLog(state_dir / "events.jsonl").read_all()
        if e.type == "orchestrator.dispatch_skipped"
        and e.payload.get("reason") == "same_trigger_backoff"
    ]
    assert absorbed, "被吸收的唤醒必须留下 same_trigger_backoff 痕迹"

    # 异型触发复位 streak:立即放行(距上次 commit 已超基础窗)。
    orch._notify_orchestrator_agent(ZE(type="dev.build.done", actor="dev", task_id="T9"))
    assert len(transport.sent) == same_type_sends + 1


# -- budget-freeze silence (ZF-E2E-MINI-P2, 2026-07-11) ----------------------
# A frozen global budget blocks Layer 2's own paid dispatch at the charging
# primitive, so stall/attention wakes during a freeze only burn a briefing +
# dispatch_failed per sweep re-emit (mini e2e: ~5min cycle for ~40min). One
# wake per freeze episode passes as the observability anchor; repeats are
# silenced; the flag resets when the freeze lifts.


def _frozen_config():
    cfg = ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        global_budget_usd=1.0,
        roles=[
            RoleConfig(
                name="orchestrator",
                backend="claude-code",
                transport="stream-json",
                permission_mode="allowlist",
                allowed_tools=["Read"],
                stages=["meta"],
                triggers=["dispatch.silent_stall", "dev.build.done"],
            ),
            RoleConfig(name="dev", backend="mock"),
        ],
    )
    return cfg


def test_freeze_silence_first_stall_wake_passes_then_silenced(state_dir: Path):
    from zf.core.cost.tracker import CostTracker

    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, _frozen_config(), transport)
    CostTracker(state_dir / "cost.jsonl").record_usage("dev", 10_000_000, 5_000_000)
    assert orch._global_budget_frozen() is True

    stall = ZE(type="dispatch.silent_stall", actor="zf-cli", task_id="T1")
    orch._notify_orchestrator_agent(stall)
    first_sent = len(transport.sent)

    orch._layer2_last_wake_at = 0.0  # rule out wake-coalescing interference
    orch._notify_orchestrator_agent(
        ZE(type="dispatch.silent_stall", actor="zf-cli", task_id="T1")
    )

    assert len(transport.sent) == first_sent  # second wake silenced
    skipped = [
        e for e in EventLog(state_dir / "events.jsonl").read_all()
        if e.type == "orchestrator.dispatch_skipped"
        and e.payload.get("reason") == "budget_freeze_silence"
    ]
    assert len(skipped) == 1


def test_freeze_silence_resets_when_freeze_lifts(state_dir: Path):
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, _frozen_config(), transport)
    orch._layer2_freeze_wake_fired = True  # as if a freeze episode fired

    # No cost recorded → not frozen → flag resets and wake proceeds.
    assert orch._global_budget_frozen() is False
    orch._notify_orchestrator_agent(
        ZE(type="dispatch.silent_stall", actor="zf-cli", task_id="T1")
    )
    assert orch._layer2_freeze_wake_fired is False
    assert len(transport.sent) == 1


def test_freeze_silence_does_not_touch_progress_wakes(state_dir: Path):
    from zf.core.cost.tracker import CostTracker

    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, _frozen_config(), transport)
    CostTracker(state_dir / "cost.jsonl").record_usage("dev", 10_000_000, 5_000_000)
    orch._layer2_freeze_wake_fired = True

    orch._notify_orchestrator_agent(
        ZE(type="dev.build.done", actor="dev", task_id="T1")
    )

    # Progress events bypass the freeze-silence gate: no
    # budget_freeze_silence skip is emitted for them. (During a freeze the
    # charging primitive still gates the actual send — that is P0-1's job,
    # not this gate's.)
    silenced = [
        e for e in EventLog(state_dir / "events.jsonl").read_all()
        if e.type == "orchestrator.dispatch_skipped"
        and e.payload.get("reason") == "budget_freeze_silence"
    ]
    assert silenced == []
