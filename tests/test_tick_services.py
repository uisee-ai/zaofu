from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from zf.core.config.schema import (
    AutoresearchConfig,
    AutoresearchTriggerPolicyConfig,
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

    def pane_display(self, target: str) -> dict[str, object]:
        return {"ok": False, "target": target, "reason": "pane_dead"}

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


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    return state_dir


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
    assert "autoresearch.invocation.requested" in types
    assert {"heartbeat_sweep", "dispatch_sweep"} <= set(orch.housekeeping)
    assert "candidate_rework" not in orch.housekeeping


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
    assert "owner.visible_message.delivery_attempted" in types
    assert "owner.visible_message.failed" in types


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

    with patch(
        "zf.integrations.feishu.transport.FeishuHttpTransport",
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
