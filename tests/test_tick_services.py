from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import zf.runtime.tick_services as tick_services
from zf.core.config.schema import (
    AutoresearchConfig,
    AutoresearchTriggerPolicyConfig,
    GoalConfig,
    ObservabilityAlertConfig,
    ObservabilityConfig,
    ProjectConfig,
    RuntimeConfig,
    RuntimeRunManagerConfig,
    RuntimeRunManagerResidentAgentConfig,
    RuntimeRunManagerSourceRepairConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.integrations.feishu.transport import MockFeishuTransport
from zf.runtime.tick_services import (
    TickServiceIntervals,
    TickServiceState,
    _configured_run_manager_backend,
    _emit_cost_blackout_if_needed,
    _emit_runtime_liveness_stale_if_needed,
    emit_stale_supervisor_projection_if_needed,
    run_standard_tick_services,
)


class _FakeOrchestrator:
    def __init__(self, state_dir: Path, config: ZfConfig) -> None:
        self.state_dir = state_dir
        self.project_root = state_dir.parent
        self.config = config
        self.event_log = EventLog(state_dir / "events.jsonl")
        self.event_writer = EventWriter(self.event_log)
        self.housekeeping: list[str] = []
        self.bug_scan = 0

    def _safe_housekeeping(self, label, fn):
        self.housekeeping.append(label)
        fn()

    def _run_heartbeat_sweep(self):
        return None

    def _run_dispatch_sweep(self):
        return None

    def _run_candidate_rework_sweep(self):
        return None

    def _run_zaofu_bug_scan(self):
        self.bug_scan += 1

    def _operator_channel_live(self):
        return False


class _TickRestartCoordinator:
    def __init__(self) -> None:
        self.spawned = []

    def spawn(self, role, *, cwd=None) -> None:
        self.spawned.append((role, cwd))


class _TickRestartTransport:
    def __init__(self) -> None:
        self.registered = []
        self.terminated = []
        self.sent = []
        self.initialized = 0

    def is_alive(self, role_name: str) -> bool:
        assert role_name == "run-manager"
        return False

    def register_role(self, role) -> None:
        self.registered.append(role)

    def for_role(self, _role_name: str):
        return self

    def is_session_running(self) -> bool:
        return True

    def init(self) -> None:
        self.initialized += 1

    def terminate(self, role_name: str) -> None:
        self.terminated.append(role_name)

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        return bool(role_name and pattern and timeout)

    def send_task(self, role_name: str, briefing_path: Path, prompt: str, *, context=None) -> None:
        self.sent.append((role_name, briefing_path, prompt, context))


def test_runtime_liveness_reconcile_emits_when_active_watcher_pid_is_dead(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    (state_dir / "processes").mkdir(parents=True)
    (state_dir / "session.yaml").write_text(
        "runtime_state: active\nproject_root: /tmp/project\n",
        encoding="utf-8",
    )
    (state_dir / "processes" / "watcher.pid.json").write_text(
        json.dumps({"owner_pid": 99999999}),
        encoding="utf-8",
    )
    event_log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(event_log)

    assert _emit_runtime_liveness_stale_if_needed(
        state_dir=state_dir,
        event_log=event_log,
        writer=writer,
    ) is True
    assert _emit_runtime_liveness_stale_if_needed(
        state_dir=state_dir,
        event_log=event_log,
        writer=writer,
    ) is False

    events = event_log.read_all()
    assert events[-1].type == "runtime.liveness.stale"
    assert events[-1].payload["recommended_route"] == "run_manager"


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir


def test_cost_blackout_has_startup_grace_for_missing_initial_usage(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    dispatch_ts = datetime.fromtimestamp(
        time.time() - 5,
        tz=timezone.utc,
    ).isoformat()
    log.append(ZfEvent(
        type="fanout.child.dispatched",
        ts=dispatch_ts,
        payload={"role_instance": "dev-1"},
    ))
    state = TickServiceState()

    emitted = _emit_cost_blackout_if_needed(
        event_log=log,
        event_writer=writer,
        state_dir=state_dir,
        state=state,
        intervals=TickServiceIntervals(
            cost_blackout_stale_s=900,
            cost_blackout_startup_grace_s=60,
            cost_blackout_cooldown_s=0,
        ),
    )

    assert emitted is False
    assert not [event for event in log.read_all() if event.type == "cost.usage.blackout"]


def test_cost_blackout_builds_run_aliases_once_for_usage_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _state(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    now = datetime.now(timezone.utc).isoformat()
    run_id = "run-blackout-alias"
    log.append(ZfEvent(
        type="run.goal.started",
        ts=now,
        correlation_id=run_id,
        payload={"run_id": run_id, "workflow_run_id": run_id},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        ts=now,
        correlation_id=run_id,
        payload={"run_id": run_id, "workflow_run_id": run_id},
    ))
    for index in range(25):
        log.append(ZfEvent(
            type="agent.usage",
            ts=now,
            correlation_id=run_id,
            payload={
                "run_id": run_id,
                "workflow_run_id": run_id,
                "usage": {"input_tokens": index + 1},
            },
        ))
    calls = 0
    original = tick_services.run_aliases

    def counting_run_aliases(events):
        nonlocal calls
        calls += 1
        return original(events)

    monkeypatch.setattr(tick_services, "run_aliases", counting_run_aliases)

    assert _emit_cost_blackout_if_needed(
        event_log=log,
        event_writer=writer,
        state_dir=state_dir,
        state=TickServiceState(),
        intervals=TickServiceIntervals(
            cost_blackout_stale_s=900,
            cost_blackout_startup_grace_s=0,
            cost_blackout_cooldown_s=0,
        ),
    ) is False
    assert calls == 1


def test_standard_tick_services_runs_supervisor_and_autoresearch(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    config = ZfConfig(autoresearch=AutoresearchConfig(
        trigger_policy=AutoresearchTriggerPolicyConfig(
            mode="continuous",
            max_triggers_per_hour=5000,
            max_daily_runs=5000,
        )
    ))
    orch = _FakeOrchestrator(state_dir, config)
    orch.event_log.append(ZfEvent(
        type="human.escalate",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "pdd_id": "R37",
            "reason": "candidate rework exhausted",
        },
    ))

    result = run_standard_tick_services(
        orch,
        state=TickServiceState(),
        now=100.0,
        intervals=TickServiceIntervals(
            heartbeat_sweep_s=0,
            bug_scan_s=0,
            supervisor_inspection_s=0,
        ),
    )

    types = [event.type for event in orch.event_log.read_all()]
    assert result.heartbeat_sweep is True
    assert result.supervisor_inspection is True
    assert "runtime.attention.needed" in types
    assert "supervisor.decision.recorded" in types
    assert "owner.visible_message.requested" in types
    assert "run.manager.autoresearch.requested" in types
    assert "autoresearch.invocation.requested" not in types
    assert {"heartbeat_sweep", "dispatch_sweep"} <= set(orch.housekeeping)
    assert "candidate_rework" not in orch.housekeeping
    assert result.control_plane_health is True
    assert (state_dir / "projections" / "control_plane_health.json").exists()


def test_standard_tick_projects_observability_attention_without_task_mutation(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    config = ZfConfig(
        project=ProjectConfig(name="demo"),
        observability=ObservabilityConfig(
            alerts=ObservabilityAlertConfig(enabled=True, cooldown_seconds=60),
        ),
    )
    orch = _FakeOrchestrator(state_dir, config)
    orch.event_log.append(ZfEvent(type="agent.timeout", task_id="TASK-OBS"))

    result = run_standard_tick_services(
        orch,
        state=TickServiceState(),
        now=100.0,
        intervals=TickServiceIntervals(
            heartbeat_sweep_s=999,
            bug_scan_s=999,
            supervisor_inspection_s=999,
            spine_projection_s=999,
            cost_blackout_check_s=999,
            stillness_audit_s=999,
        ),
    )

    attention = [
        event for event in orch.event_log.read_all()
        if event.type == "runtime.attention.needed"
        and event.actor == "zf-observability"
    ]
    assert result.observability_alerts_emitted == 1
    assert result.observability_alerts_status == "degraded"
    assert attention[0].payload["actionability"] == "observe"
    assert (state_dir / "kanban.json").read_text(encoding="utf-8") == "[]\n"


def test_standard_tick_surfaces_workflow_synthesis_consumer_failure(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    orch = _FakeOrchestrator(state_dir, ZfConfig())

    with patch(
        "zf.runtime.workflow_synthesis.consume_workflow_synthesis_operations",
        side_effect=RuntimeError("consumer unavailable"),
    ):
        run_standard_tick_services(
            orch,
            state=TickServiceState(),
            now=1.0,
            intervals=TickServiceIntervals(
                heartbeat_sweep_s=999,
                bug_scan_s=999,
                supervisor_inspection_s=999,
                spine_projection_s=999,
                cost_blackout_check_s=999,
                stillness_audit_s=999,
            ),
        )

    failure = next(
        event
        for event in orch.event_log.read_all()
        if event.type == "orchestrator.tick.failed"
        and event.payload.get("component") == "workflow_synthesis_consumer"
    )
    assert failure.payload["error_type"] == "RuntimeError"
    assert failure.payload["error"] == "consumer unavailable"


def test_standard_tick_materializes_blocked_goal_before_quiescent_return(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    config = ZfConfig(project=ProjectConfig(name="demo"))
    orch = _FakeOrchestrator(state_dir, config)
    orch.event_log.append(ZfEvent(
        id="evt-start",
        type="run.goal.started",
        correlation_id="run-terminal",
        payload={
            "run_id": "run-terminal",
            "goal_id": "GOAL-TERMINAL",
            "objective": "close owner delivery",
        },
    ))
    orch.event_log.append(ZfEvent(
        id="evt-blocked",
        type="run.goal.blocked",
        correlation_id="run-terminal",
        payload={
            "run_id": "run-terminal",
            "goal_id": "GOAL-TERMINAL",
            "reason": "owner input required",
        },
    ))

    result = run_standard_tick_services(
        orch,
        state=TickServiceState(),
        now=1.0,
        intervals=TickServiceIntervals(
            heartbeat_sweep_s=999,
            bug_scan_s=999,
            supervisor_inspection_s=999,
            spine_projection_s=999,
            cost_blackout_check_s=999,
            stillness_audit_s=999,
        ),
    )

    assert result.goal_dossier_delivery is True
    assert (
        state_dir
        / "projections"
        / "goals"
        / "run-terminal"
        / "goal-dossier.v1.json"
    ).is_file()
    requests = [
        event for event in orch.event_log.read_all()
        if event.type == "owner.visible_message.requested"
        and event.payload.get("message_kind") == "run_terminal_delivery"
    ]
    assert len(requests) == 1


def test_standard_tick_terminalizes_unrecoverable_escalation_before_quiescence(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    config = ZfConfig(
        project=ProjectConfig(name="demo"),
        goal=GoalConfig(enabled=True),
    )
    orch = _FakeOrchestrator(state_dir, config)
    orch.event_log.append(ZfEvent(
        id="evt-start",
        type="run.goal.started",
        correlation_id="run-stage-cap",
        payload={
            "run_id": "run-stage-cap",
            "workflow_run_id": "run-stage-cap",
            "goal_id": "GOAL-STAGE-CAP",
        },
    ))
    orch.event_log.append(ZfEvent(
        id="evt-stage-cap",
        type="human.escalate",
        actor="zf-cli",
        task_id="TASK-STAGE-CAP",
        ts="2026-08-01T00:00:00+00:00",
        correlation_id="run-stage-cap",
        payload={
            "reason": "stage replan cap exhausted",
            "failure_class": "stage_replan_cap_exhausted",
            "failure_scope": "plan_admission",
            "operator_required": True,
            "recoverable": False,
            "terminalization_condition": "auto_recovery_exhausted",
        },
    ))

    run_standard_tick_services(
        orch,
        state=TickServiceState(),
        now=1.0,
        intervals=TickServiceIntervals(
            heartbeat_sweep_s=999,
            bug_scan_s=999,
            supervisor_inspection_s=999,
            spine_projection_s=999,
            cost_blackout_check_s=999,
            stillness_audit_s=999,
        ),
    )

    events = orch.event_log.read_all()
    terminal = next(
        event for event in events
        if event.type == "run.goal.blocked"
        and event.causation_id == "evt-stage-cap"
    )
    request = next(
        event for event in events
        if event.type == "run.manager.autoresearch.requested"
        and event.causation_id == terminal.id
    )
    assert terminal.correlation_id == "run-stage-cap"
    assert request.payload["failure_class"] == "stage_replan_cap_exhausted"


def test_standard_tick_services_coalesces_healthy_run_manager_ticks(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    orch = _FakeOrchestrator(state_dir, ZfConfig())
    state = TickServiceState()
    intervals = TickServiceIntervals(
        heartbeat_sweep_s=999,
        bug_scan_s=999,
        supervisor_inspection_s=0,
        run_manager_idle_refresh_s=900,
    )

    with patch("zf.runtime.tick_services._run_supervisor", return_value=False), \
            patch("zf.runtime.tick_services.run_autoresearch_trigger_scan", return_value=0), \
            patch("zf.runtime.tick_services._emit_stall_recoveries", return_value=False), \
            patch("zf.runtime.tick_services._run_remediation_shadow", return_value=False), \
            patch("zf.runtime.tick_services._redrive_replan_adoptions", return_value=False), \
            patch("zf.runtime.tick_services._run_run_manager_watchdog", return_value=MagicMock(changed=False)), \
            patch("zf.runtime.tick_services._apply_run_manager_resident_restarts", return_value=False), \
            patch("zf.runtime.tick_services._deliver_run_manager_cards", return_value=False), \
            patch("zf.runtime.tick_services._materialize_failure_candidates", return_value=0), \
            patch("zf.runtime.tick_services._replay_unconsumed_invokes", return_value=0), \
            patch("zf.runtime.tick_services._sweep_channel_discussions", return_value=0), \
            patch("zf.runtime.tick_services._consume_run_manager_source_repairs", return_value=0):
        run_standard_tick_services(orch, state=state, now=100.0, intervals=intervals)
        first_types = [event.type for event in orch.event_log.read_all()]
        run_standard_tick_services(orch, state=state, now=101.0, intervals=intervals)
        second_types = [event.type for event in orch.event_log.read_all()]
        run_standard_tick_services(orch, state=state, now=1001.0, intervals=intervals)
        third_types = [event.type for event in orch.event_log.read_all()]

    assert first_types.count("run.manager.tick.started") == 1
    assert second_types.count("run.manager.tick.started") == 1
    assert third_types.count("run.manager.tick.started") == 2
    assert (state_dir / "projections" / "control_plane_health.json").exists()


@pytest.mark.parametrize(("idle", "inspection", "age", "stale"), [
    (900, 300, 727, False), (900, 300, 1030, False), (900, 300, 1250, True),
    (1400, 60, 1430, False), (1400, 60, 1550, True), (50, 10, 500, False),
])
def test_scheduled_watchdog_observes_run_manager_refresh_window(
    tmp_path: Path, idle: int, inspection: int, age: int, stale: bool,
) -> None:
    state_dir = _state(tmp_path)
    config = ZfConfig(runtime=RuntimeConfig(run_manager=RuntimeRunManagerConfig(
        source_repair=RuntimeRunManagerSourceRepairConfig(enabled=True),
    )))
    orch = _FakeOrchestrator(state_dir, config)
    projection = state_dir / "projections" / "run_manager.json"
    projection.parent.mkdir(exist_ok=True)
    projection.write_text("{}")
    timestamp = time.time() - age
    os.utime(projection, (timestamp, timestamp))
    intervals = TickServiceIntervals(run_manager_idle_refresh_s=idle, supervisor_inspection_s=inspection)

    with patch.object(tick_services, "_consume_run_manager_source_repairs", return_value=0):
        run_standard_tick_services(orch, state=TickServiceState(), now=10000, intervals=intervals)

    events = orch.event_log.read_all()
    assert any(e.type == "run.manager.unhealthy" and e.payload["reason"] == "projection_stale"
               for e in events) == stale
    assert any(e.type == "run.manager.source_repair.dispatch_requested" for e in events) == stale


def test_standard_tick_services_keeps_quiescent_delivery_silent(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    orch = _FakeOrchestrator(state_dir, ZfConfig())
    orch.event_log.append(ZfEvent(
        type="run.goal.started",
        correlation_id="run-terminal",
        payload={"workflow_run_id": "run-terminal"},
    ))
    orch.event_log.append(ZfEvent(
        type="ship.completed",
        correlation_id="run-terminal",
        payload={"workflow_run_id": "run-terminal"},
    ))
    state = TickServiceState()
    intervals = TickServiceIntervals(
        heartbeat_sweep_s=999,
        bug_scan_s=999,
        supervisor_inspection_s=0,
        run_manager_idle_refresh_s=1,
    )

    with patch("zf.runtime.tick_services._run_supervisor", return_value=False), \
            patch("zf.runtime.tick_services.run_autoresearch_trigger_scan", return_value=0), \
            patch("zf.runtime.tick_services._emit_stall_recoveries", return_value=False), \
            patch("zf.runtime.tick_services._run_remediation_shadow", return_value=False), \
            patch("zf.runtime.tick_services._redrive_replan_adoptions", return_value=False), \
            patch("zf.runtime.tick_services._apply_run_manager_resident_restarts", return_value=False), \
            patch("zf.runtime.tick_services._deliver_run_manager_cards", return_value=False), \
            patch("zf.runtime.tick_services._materialize_failure_candidates", return_value=0), \
            patch("zf.runtime.tick_services._replay_unconsumed_invokes", return_value=0), \
            patch("zf.runtime.tick_services._sweep_channel_discussions", return_value=0), \
            patch("zf.runtime.tick_services._consume_run_manager_source_repairs", return_value=0):
        first = run_standard_tick_services(orch, state=state, now=100.0, intervals=intervals)
        second = run_standard_tick_services(orch, state=state, now=200.0, intervals=intervals)

    assert first.run_manager is False
    assert second.run_manager is False
    types = [event.type for event in orch.event_log.read_all()]
    assert "run.manager.unhealthy" not in types
    assert "run.manager.tick.started" not in types


def test_standard_tick_services_ignores_control_plane_bookkeeping_inputs(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    orch = _FakeOrchestrator(state_dir, ZfConfig())
    state = TickServiceState()
    intervals = TickServiceIntervals(
        heartbeat_sweep_s=999,
        bug_scan_s=999,
        supervisor_inspection_s=0,
        run_manager_idle_refresh_s=900,
    )

    with patch("zf.runtime.tick_services._run_supervisor", return_value=False), \
            patch("zf.runtime.tick_services.run_autoresearch_trigger_scan", return_value=0), \
            patch("zf.runtime.tick_services._emit_stall_recoveries", return_value=False), \
            patch("zf.runtime.tick_services._run_remediation_shadow", return_value=False), \
            patch("zf.runtime.tick_services._redrive_replan_adoptions", return_value=False), \
            patch("zf.runtime.tick_services._run_run_manager_watchdog", return_value=MagicMock(changed=False)), \
            patch("zf.runtime.tick_services._apply_run_manager_resident_restarts", return_value=False), \
            patch("zf.runtime.tick_services._deliver_run_manager_cards", return_value=False), \
            patch("zf.runtime.tick_services._materialize_failure_candidates", return_value=0), \
            patch("zf.runtime.tick_services._replay_unconsumed_invokes", return_value=0), \
            patch("zf.runtime.tick_services._sweep_channel_discussions", return_value=0), \
            patch("zf.runtime.tick_services._consume_run_manager_source_repairs", return_value=0):
        run_standard_tick_services(orch, state=state, now=100.0, intervals=intervals)
        for event_type in (
            "agent.text",
            "hook.orphan_event",
            "orchestrator.decision.recorded",
            "orchestrator.round.complete",
        ):
            orch.event_log.append(ZfEvent(type=event_type, actor="control-plane"))
        run_standard_tick_services(orch, state=state, now=101.0, intervals=intervals)

    types = [event.type for event in orch.event_log.read_all()]
    assert types.count("run.manager.tick.started") == 1


def test_standard_tick_services_materializes_failure_candidates(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    config = ZfConfig()
    orch = _FakeOrchestrator(state_dir, config)
    # ZF-E2E-PRDCTL-P0-3: RM self events no longer materialize; seed a real
    # failure to exercise the materialization pipeline.
    orch.event_log.append(ZfEvent(
        type="gate.failed",
        actor="zf-cli",
        task_id="TASK-RM",
        payload={"reason": "static gate red"},
    ))

    result = run_standard_tick_services(
        orch,
        state=TickServiceState(),
        now=100.0,
        intervals=TickServiceIntervals(
            heartbeat_sweep_s=999,
            bug_scan_s=999,
            supervisor_inspection_s=0,
        ),
    )

    candidates = sorted((state_dir / "failure-candidates").glob("*.json"))
    assert result.failure_candidates_materialized == 1
    assert result.failure_closeout_materialized == 1
    assert len(candidates) == 1
    data = json.loads(candidates[0].read_text(encoding="utf-8"))
    assert data["event"]["type"] == "gate.failed"
    assert data["event"]["task_id"] == "TASK-RM"
    closeout = state_dir / "failure-closeout" / "failure-closeout-manifest.json"
    assert closeout.exists()
    closeout_data = json.loads(closeout.read_text(encoding="utf-8"))
    assert closeout_data["materialized_count"] == 1
    types = [event.type for event in orch.event_log.read_all()]
    assert "failure.candidates.materialized" in types
    assert "failure.closeout.materialized" in types


def test_run_manager_backend_prefers_runtime_run_manager_config() -> None:
    cfg = ZfConfig(
        autoresearch=AutoresearchConfig(
            trigger_policy=AutoresearchTriggerPolicyConfig(
                self_repair_backend="codex",
            ),
        ),
        runtime=RuntimeConfig(
            run_manager=RuntimeRunManagerConfig(backend="claude-code"),
        ),
    )

    assert _configured_run_manager_backend(cfg) == "claude-code"


def test_stale_supervisor_projection_emits_attention_source(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    supervisor_dir = state_dir / "projections" / "supervisor"
    supervisor_dir.mkdir(parents=True)
    (supervisor_dir / "snapshot.json").write_text(json.dumps({
        "generated_at": "2026-06-21T10:00:00+00:00",
    }), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    log.append(ZfEvent(
        id="evt-late",
        type="integration.failed",
        actor="zf-cli",
        ts="2026-06-21T10:20:00+00:00",
    ))

    emitted = emit_stale_supervisor_projection_if_needed(
        state_dir=state_dir,
        writer=writer,
        event_log=log,
        max_stale_seconds=60,
    )

    events = log.read_all()
    assert emitted is True
    assert events[-1].type == "supervisor.projection.stale"
    assert events[-1].payload["latest_event_id"] == "evt-late"


def test_stale_supervisor_projection_ignores_run_manager_tick_only(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    supervisor_dir = state_dir / "projections" / "supervisor"
    supervisor_dir.mkdir(parents=True)
    (supervisor_dir / "snapshot.json").write_text(json.dumps({
        "generated_at": "2026-06-21T10:00:00+00:00",
    }), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    log.append(ZfEvent(
        id="evt-rm-tick",
        type="run.manager.tick.completed",
        actor="run-manager",
        ts="2026-06-21T10:20:00+00:00",
    ))

    emitted = emit_stale_supervisor_projection_if_needed(
        state_dir=state_dir,
        writer=writer,
        event_log=log,
        max_stale_seconds=60,
    )

    assert emitted is False
    assert not [
        event for event in log.read_all()
        if event.type == "supervisor.projection.stale"
    ]


def test_standard_tick_refreshes_supervisor_before_stale_check(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    supervisor_dir = state_dir / "projections" / "supervisor"
    supervisor_dir.mkdir(parents=True)
    snapshot_path = supervisor_dir / "snapshot.json"
    snapshot_path.write_text(json.dumps({
        "generated_at": "2026-06-21T10:00:00+00:00",
    }), encoding="utf-8")
    orch = _FakeOrchestrator(state_dir, ZfConfig())
    orch.event_log.append(ZfEvent(
        id="evt-progress",
        type="integration.failed",
        actor="zf-cli",
        ts="2026-06-21T10:20:00+00:00",
    ))

    def refresh_supervisor(**_kwargs) -> bool:
        snapshot_path.write_text(json.dumps({
            "generated_at": "2026-06-21T10:20:00+00:00",
        }), encoding="utf-8")
        return True

    with patch(
        "zf.runtime.tick_services._run_supervisor",
        side_effect=refresh_supervisor,
    ):
        result = run_standard_tick_services(
            orch,
            state=TickServiceState(),
            now=100.0,
            intervals=TickServiceIntervals(
                heartbeat_sweep_s=999,
                bug_scan_s=999,
                supervisor_inspection_s=0,
                stale_supervisor_projection_s=60,
                spine_projection_s=999,
                cost_blackout_check_s=999,
            ),
        )

    assert result.supervisor_inspection is True
    assert result.stale_supervisor_projection is False
    assert not [
        event for event in orch.event_log.read_all()
        if event.type == "supervisor.projection.stale"
    ]


def test_standard_tick_services_consumes_self_repair_dispatch(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    config = ZfConfig(autoresearch=AutoresearchConfig(
        trigger_policy=AutoresearchTriggerPolicyConfig(
            repair_mode="bounded_repair",
            self_repair_backend="codex",
        )
    ))
    orch = _FakeOrchestrator(state_dir, config)
    orch.event_log.append(ZfEvent(
        type="autoresearch.repair.dispatch_requested",
        payload={
            "fingerprint": "stall:R37",
            "attempt": 0,
            "candidate_id": "C-1",
            "candidate_path": "/tmp/candidate.md",
            "repair_task_payload": {
                "contract": {
                    "scope": ["src/zf/**"],
                    "verification": "pytest",
                }
            },
        },
    ))

    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
            patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        result = run_standard_tick_services(
            orch,
            state=TickServiceState(),
            now=100.0,
            intervals=TickServiceIntervals(
                heartbeat_sweep_s=999,
                bug_scan_s=999,
                supervisor_inspection_s=0,
            ),
        )

    assert result.self_repair_consumer is True
    assert any(
        event.type == "autoresearch.repair.dispatched"
        for event in orch.event_log.read_all()
    )
    assert mpopen.called


def test_standard_tick_services_runs_run_manager_watchdog_source_repair(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    config = ZfConfig(
        runtime=RuntimeConfig(
            run_manager=RuntimeRunManagerConfig(
                backend="codex",
                source_repair=RuntimeRunManagerSourceRepairConfig(enabled=True),
            ),
        ),
    )
    orch = _FakeOrchestrator(state_dir, config)
    for idx in range(3):
        orch.event_log.append(ZfEvent(
            type="run.manager.tick.failed",
            actor="run-manager",
            payload={"error": f"boom-{idx}"},
        ))

    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
            patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        result = run_standard_tick_services(
            orch,
            state=TickServiceState(),
            now=100.0,
            intervals=TickServiceIntervals(
                heartbeat_sweep_s=999,
                bug_scan_s=999,
                supervisor_inspection_s=0,
            ),
        )

    types = [event.type for event in orch.event_log.read_all()]
    assert result.run_manager_watchdog is True
    assert result.self_repair_consumer is True
    assert "run.manager.unhealthy" in types
    assert "run.manager.source_repair.dispatch_requested" in types
    assert "autoresearch.repair.dispatched" in types
    assert mpopen.called


def test_standard_tick_services_emits_run_manager_tick_failed_on_exception(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    orch = _FakeOrchestrator(
        state_dir,
        ZfConfig(runtime=RuntimeConfig(run_manager=RuntimeRunManagerConfig())),
    )

    with patch("zf.runtime.run_manager.run_manager_tick", side_effect=RuntimeError("boom")):
        result = run_standard_tick_services(
            orch,
            state=TickServiceState(),
            now=100.0,
            intervals=TickServiceIntervals(
                heartbeat_sweep_s=999,
                bug_scan_s=999,
                supervisor_inspection_s=0,
            ),
        )

    failures = [
        event for event in orch.event_log.read_all()
        if event.type == "run.manager.tick.failed"
    ]
    assert result.run_manager is False
    assert failures
    assert failures[-1].payload["reason"] == "run_manager_tick_exception"
    assert "boom" in failures[-1].payload["error"]


def test_standard_tick_services_restarts_dead_resident_run_manager(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    config = ZfConfig(
        project=ProjectConfig(name="tick-resident-restart"),
        session=SessionConfig(tmux_session="zf-tick-resident"),
        runtime=RuntimeConfig(
            run_manager=RuntimeRunManagerConfig(
                backend="claude-code",
                resident_agent=RuntimeRunManagerResidentAgentConfig(
                    enabled=True,
                    transport="tmux",
                    instance_id="run-manager",
                    session_mode="dedicated",
                ),
            ),
        ),
    )
    orch = _FakeOrchestrator(state_dir, config)
    transport = _TickRestartTransport()
    coordinator = _TickRestartCoordinator()
    orch.transport = transport
    orch.spawn_coordinator = coordinator

    result = run_standard_tick_services(
        orch,
        state=TickServiceState(),
        now=100.0,
        intervals=TickServiceIntervals(
            heartbeat_sweep_s=999,
            bug_scan_s=999,
            supervisor_inspection_s=0,
        ),
    )

    types = [event.type for event in orch.event_log.read_all()]
    assert result.run_manager_watchdog is True
    assert result.run_manager_resident_restart is True
    assert "run.manager.resident.restart_requested" in types
    assert "run.manager.resident.restarted" in types
    assert "run.manager.resident.spawned" in types
    assert "run.manager.resident.prompted" in types
    assert transport.terminated == ["run-manager"]
    assert coordinator.spawned and coordinator.spawned[0][0].instance_id == "run-manager"
    assert transport.sent and "observe-only" in transport.sent[0][2]


def test_standard_tick_services_records_owner_visible_no_target(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    orch = _FakeOrchestrator(state_dir, ZfConfig())
    orch.event_log.append(ZfEvent(
        type="owner.visible_message.requested",
        actor="zf-supervisor",
        task_id="TASK-OWNER",
        payload={
            "message_id": "omsg-owner",
            "severity": "high",
            "title": "owner attention",
            "delivery_targets": ["feishu"],
        },
    ))

    result = run_standard_tick_services(
        orch,
        state=TickServiceState(),
        now=100.0,
        intervals=TickServiceIntervals(
            heartbeat_sweep_s=0,
            bug_scan_s=999,
            supervisor_inspection_s=999,
        ),
    )

    types = [event.type for event in orch.event_log.read_all()]
    assert result.owner_visible_delivery is True
    assert "owner.visible_message.suppressed" in types


def test_standard_tick_services_pushes_run_manager_human_decision_card(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = _state(tmp_path)
    transport = MockFeishuTransport()
    config = ZfConfig()
    orch = _FakeOrchestrator(state_dir, config)
    orch.event_log.append(ZfEvent(
        type="human.escalation.sent",
        actor="run-manager",
        payload={
            "decision_token": "hdec-r5",
            "owner_route": "run_manager",
            "failure_class": "candidate_gate",
            "checkpoint_id": "wfres-r5",
            "fingerprint": "candidate:r5",
            "safe_resume_action": "workflow_resume_apply",
            "reason": "operator approval required",
        },
    ))
    monkeypatch.setenv("ZF_OWNER_VISIBLE_CHAT", "oc_rm")
    monkeypatch.setenv("ZF_OWNER_VISIBLE_RECEIVE_ID_TYPE", "chat_id")
    monkeypatch.setenv("FEISHU_RUNM", "cli_arch")
    monkeypatch.setenv("FEISHU_RUNM_SECRET", "secret_arch")

    with patch(
        "zf.integrations.feishu.bot_credentials.FeishuHttpTransport",
        return_value=transport,
    ):
        result = run_standard_tick_services(
            orch,
            state=TickServiceState(),
            now=100.0,
            intervals=TickServiceIntervals(
                heartbeat_sweep_s=999,
                bug_scan_s=999,
                supervisor_inspection_s=0,
            ),
        )

    assert result.run_manager_card_delivery is True
    assert transport.sent_messages
    card = json.loads(transport.sent_messages[0].content)
    assert transport.sent_messages[0].chat_id == "oc_rm"
    assert transport.sent_messages[0].msg_type == "interactive"
    assert "Run Manager 人工决策" in json.dumps(card, ensure_ascii=False)
    assert "human-decision-approve:hdec-r5" in json.dumps(card, ensure_ascii=False)


@pytest.mark.parametrize("event_type", ["runtime.action.attempt.completed", "provider.turn.closed"])
def test_bound_recovery_receipt_wakes_rm_before_supervisor_interval(tmp_path: Path, event_type: str):
    state_dir = _state(tmp_path)
    orch = _FakeOrchestrator(state_dir, ZfConfig())
    state = TickServiceState(last_supervisor_inspection_at=100.0,
                             last_run_manager_tick_at=100.0)
    intervals = TickServiceIntervals(supervisor_inspection_s=300)
    initial = ZfEvent(type="run.manager.tick.completed", actor="run-manager")
    orch.event_log.append(initial)
    state.last_run_manager_input_event_id = ""
    receipt = ZfEvent(type=event_type, origin="kernel",
                      actor="run-manager", payload={"operation_id": "op-1"})
    orch.event_log.append(receipt)
    with patch("zf.runtime.tick_services._run_supervisor") as supervisor, \
            patch("zf.runtime.tick_services._run_run_manager", return_value=MagicMock(changed=True, repairs_dispatched=0)) as manager:
        result = run_standard_tick_services(orch, state=state, now=105.0, intervals=intervals)
        assert manager.call_count == 1
        assert result.run_manager is True
        supervisor.assert_not_called()
        orch.event_log.append(ZfEvent(type="run.manager.tick.completed", actor="run-manager"))
        run_standard_tick_services(orch, state=state, now=110.0, intervals=intervals)
        assert manager.call_count == 1  # bookkeeping must not create a tick feedback loop


def test_side_closeout_approval_keeps_recovery_tick_running(tmp_path: Path):
    from datetime import datetime, timedelta, timezone
    state_dir = _state(tmp_path)
    config = ZfConfig()
    config.goal.enabled = True
    config.goal.quiescent_after_escalate = True
    orch = _FakeOrchestrator(state_dir, config)
    orch.event_log.append(ZfEvent(
        type="human.escalate", actor="run-manager", origin="kernel",
        ts=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        payload={"blocking_scope": "side", "action": "failure-closeout-activate"},
    ))
    orch.event_log.append(ZfEvent(type="workflow.operation.settled", origin="kernel"))
    with patch("zf.runtime.tick_services._run_run_manager", return_value=MagicMock(changed=True, repairs_dispatched=0)) as manager:
        result = run_standard_tick_services(
            orch, state=TickServiceState(last_supervisor_inspection_at=100), now=105,
        )
    assert result.run_manager
    manager.assert_called_once()
    assert not any(e.type == "run.goal.quiescent.entered" for e in orch.event_log.read_all())
