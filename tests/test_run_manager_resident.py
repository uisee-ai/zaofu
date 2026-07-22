from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    RuntimeRunManagerConfig,
    RuntimeRunManagerResidentAgentConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.session import SessionStore
from zf.runtime.pane_probe import build_runtime_pane_probe
from zf.runtime.run_manager import (
    RUN_MANAGER_AUTORESEARCH_REQUESTED,
    build_run_manager_projection,
    run_manager_tick,
)
from zf.runtime.run_manager_resident import (
    RUN_MANAGER_RESIDENT_PRESERVED,
    RUN_MANAGER_RESIDENT_REBOUND,
    RUN_MANAGER_RESIDENT_RESTARTED,
    RUN_MANAGER_RESIDENT_RESTART_FAILED,
    RUN_MANAGER_RESIDENT_RESTART_REQUESTED,
    apply_pending_resident_restart_requests,
    build_resident_preserve_payload,
    build_resident_run_manager_briefing,
    build_resident_run_manager_role,
    read_resident_preserve_marker,
    rebind_preserved_resident_run_manager,
    resident_run_manager_tmux_session,
    spawn_resident_run_manager,
    write_resident_preserve_marker,
)
from zf.runtime.shutdown import GracefulShutdown
from zf.runtime.transport import make_transport


_STALE_EVENT_ANCHOR = datetime.now(timezone.utc) - timedelta(hours=1)


def _stale_event_ts(offset_seconds: int = 0) -> str:
    return (_STALE_EVENT_ANCHOR + timedelta(seconds=offset_seconds)).isoformat()


def _resident_config(
    *,
    session_mode: str = "shared",
    tmux_session: str = "",
) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="resident-test"),
        session=SessionConfig(tmux_session="zf-resident-test"),
        roles=[RoleConfig(name="dev", backend="claude-code")],
        runtime=RuntimeConfig(
            run_manager=RuntimeRunManagerConfig(
                backend="claude-code",
                resident_agent=RuntimeRunManagerResidentAgentConfig(
                    enabled=True,
                    transport="tmux",
                    instance_id="run-manager",
                    session_mode=session_mode,
                    tmux_session=tmux_session,
                ),
            ),
        ),
    )


def _write_supervisor_attention(state_dir: Path) -> None:
    supervisor_dir = state_dir / "projections" / "supervisor"
    supervisor_dir.mkdir(parents=True)
    (supervisor_dir / "snapshot.json").write_text(
        json.dumps({
            "schema_version": "supervisor.snapshot.v1",
            "attention_items": [{
                "attention_id": "attn-stall-1",
                "status": "open",
                "fingerprint": "runtime:dispatch.silent_stall:T1",
                "severity": "high",
                "title": "Dispatch silent stall",
                "summary": "task.assigned had no matching terminal event",
                "task_id": "T1",
                "fanout_id": "fanout-impl-1",
                "stage_id": "cj-min-slice-implementation",
                "lane": "dev-lane-1",
                "source_event_ids": ["evt-stall-1"],
                "source_ref": "events.jsonl#evt-stall-1",
                "suggested_route": "autoresearch_trigger",
                "suggested_action": {"kind": "diagnose_worker_noop"},
            }],
        }) + "\n",
        encoding="utf-8",
    )


def test_build_resident_run_manager_role_uses_claude_code_tmux() -> None:
    role = build_resident_run_manager_role(_resident_config())

    assert role is not None
    assert role.name == "run-manager"
    assert role.instance_id == "run-manager"
    assert role.backend == "claude-code"
    assert role.transport == "tmux"
    assert role.role_kind == "reader"
    assert "run.manager.transition" in role.triggers
    assert "run.manager.agent.observation" in role.publishes


def test_resident_run_manager_briefing_preserves_kernel_boundary(
    tmp_path: Path,
) -> None:
    role = build_resident_run_manager_role(_resident_config())
    assert role is not None

    briefing = build_resident_run_manager_briefing(
        project_root=tmp_path,
        state_dir=tmp_path / ".zf",
        role=role,
    )

    assert "不直接编辑 `events.jsonl`" in briefing
    assert "你不是 monitor-only" in briefing
    assert "recommended_route=repair" in briefing
    assert "bounded repair worker" in briefing
    assert "zf emit run.manager.agent.observation" in briefing
    assert "停止常规 proof-of-life 写入" in briefing
    assert f"--state-dir {tmp_path / '.zf'}" in briefing
    assert "human.escalate" in briefing


def test_resident_run_manager_events_are_known() -> None:
    for event_type in {
        "run.manager.resident.spawned",
        "run.manager.resident.prompted",
        "run.manager.resident.preserved",
        "run.manager.resident.rebound",
        "run.manager.resident.restart_requested",
        "run.manager.resident.restarted",
        "run.manager.resident.restart_failed",
        "run.manager.agent.observation",
        "run.manager.agent.recommendation",
    }:
        assert event_type in KNOWN_EVENT_TYPES


def test_make_transport_registers_shared_resident_run_manager(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    transport = make_transport(_resident_config(), dry_run=True)

    assert transport.for_role("run-manager") is not None
    assert transport.for_role("run-manager").session_name == "zf-resident-test"
    assert transport.for_role("dev").session_name == "zf-resident-test"


def test_make_transport_registers_dedicated_resident_tmux_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _resident_config(session_mode="dedicated")

    transport = make_transport(cfg, dry_run=True)

    assert resident_run_manager_tmux_session(cfg) == "zf-resident-test-run-manager"
    assert transport.for_role("dev").session_name == "zf-resident-test"
    assert transport.for_role("run-manager").session_name == (
        "zf-resident-test-run-manager"
    )


def test_make_transport_uses_explicit_dedicated_resident_tmux_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _resident_config(
        session_mode="dedicated",
        tmux_session="zf-custom-monitor",
    )

    transport = make_transport(cfg, dry_run=True)

    assert resident_run_manager_tmux_session(cfg) == "zf-custom-monitor"
    assert transport.for_role("run-manager").session_name == "zf-custom-monitor"


class _FakeCoordinator:
    def __init__(self) -> None:
        self.spawned = []

    def spawn(self, role, *, cwd=None) -> None:
        self.spawned.append((role, cwd))


class _RestartTransport:
    def __init__(self, *, running: bool = True) -> None:
        self.running = running
        self.registered = []
        self.terminated = []
        self.initialized = 0
        self.sent = []

    def register_role(self, role) -> None:
        self.registered.append(role)

    def for_role(self, _role_name: str):
        return self

    def is_session_running(self) -> bool:
        return self.running

    def init(self) -> None:
        self.initialized += 1
        self.running = True

    def terminate(self, role_name: str) -> None:
        self.terminated.append(role_name)

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        return bool(role_name and pattern and timeout)

    def send_task(self, role_name: str, briefing_path: Path, prompt: str, *, context=None) -> None:
        self.sent.append((role_name, briefing_path, prompt, context))


class _ShutdownTransport:
    def __init__(self) -> None:
        self.shutdown_exclude_roles: set[str] | None = None

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def shutdown(self, *, exclude_roles: set[str] | None = None) -> None:
        self.shutdown_exclude_roles = set(exclude_roles or set())


def test_spawn_resident_records_session_mode_and_tmux_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    cfg = _resident_config(
        session_mode="dedicated",
        tmux_session="zf-custom-monitor",
    )
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    coordinator = _FakeCoordinator()

    role = spawn_resident_run_manager(
        config=cfg,
        state_dir=state_dir,
        project_root=tmp_path,
        coordinator=coordinator,
        transport=make_transport(cfg, dry_run=True),
        event_log=log,
        instructions_dir=state_dir / "instructions",
        dry_run=True,
    )

    assert role is not None
    assert coordinator.spawned
    events = log.read_all()
    spawned = [event for event in events if event.type == "run.manager.resident.spawned"]
    assert spawned
    assert spawned[0].payload["session_mode"] == "dedicated"
    assert spawned[0].payload["tmux_session"] == "zf-custom-monitor"


def test_graceful_shutdown_preserves_dedicated_resident_run_manager(
    tmp_path: Path,
) -> None:
    cfg = _resident_config(session_mode="dedicated", tmux_session="zf-rm-only")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    transport = _ShutdownTransport()

    steps = GracefulShutdown(
        state_dir,
        transport,  # type: ignore[arg-type]
        config=cfg,
        preserve_run_manager=True,
    ).execute_fast()

    events = EventLog(state_dir / "events.jsonl").read_all()
    preserved = [
        event for event in events
        if event.type == RUN_MANAGER_RESIDENT_PRESERVED
    ]
    assert "preserve_run_manager" in steps
    assert preserved
    assert preserved[-1].payload["tmux_session"] == "zf-rm-only"
    assert preserved[-1].payload["shutdown_scope"] == "workflow_only"
    assert transport.shutdown_exclude_roles == {"run-manager"}
    assert read_resident_preserve_marker(state_dir)["tmux_session"] == "zf-rm-only"


def test_rebind_preserved_resident_run_manager_reprompts_existing_session(
    tmp_path: Path,
) -> None:
    cfg = _resident_config(session_mode="dedicated", tmux_session="zf-rm-only")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    payload = build_resident_preserve_payload(
        config=cfg,
        state_dir=state_dir,
        reason="test",
    )
    assert payload is not None
    write_resident_preserve_marker(state_dir=state_dir, payload=payload)
    transport = _RestartTransport(running=True)

    role = rebind_preserved_resident_run_manager(
        config=cfg,
        state_dir=state_dir,
        project_root=tmp_path,
        transport=transport,
        event_log=log,
        instructions_dir=state_dir / "instructions",
        marker_payload=payload,
    )

    events = log.read_all()
    assert role is not None
    assert role.instance_id == "run-manager"
    assert any(event.type == RUN_MANAGER_RESIDENT_REBOUND for event in events)
    prompted = [
        event for event in events
        if event.type == "run.manager.resident.prompted"
    ]
    assert prompted and prompted[-1].payload["rebound"] is True
    assert transport.sent and "Control-Plane Rebind" in transport.sent[-1][2]


def test_apply_resident_restart_request_rebuilds_only_resident_agent(
    tmp_path: Path,
) -> None:
    cfg = _resident_config(session_mode="dedicated", tmux_session="zf-rm-only")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    request = ZfEvent(
        id="evt-restart-request",
        type=RUN_MANAGER_RESIDENT_RESTART_REQUESTED,
        actor="run-manager-watchdog",
        payload={
            "instance_id": "run-manager",
            "tmux_session": "zf-rm-only",
            "restart_scope": "resident_only",
            "first_tick_mode": "observe_only",
        },
    )
    log.append(request)
    coordinator = _FakeCoordinator()
    transport = _RestartTransport(running=True)

    first = apply_pending_resident_restart_requests(
        config=cfg,
        state_dir=state_dir,
        project_root=tmp_path,
        writer=writer,
        event_log=log,
        coordinator=coordinator,
        transport=transport,
        instructions_dir=state_dir / "instructions",
    )
    second = apply_pending_resident_restart_requests(
        config=cfg,
        state_dir=state_dir,
        project_root=tmp_path,
        writer=writer,
        event_log=log,
        coordinator=coordinator,
        transport=transport,
        instructions_dir=state_dir / "instructions",
    )

    events = log.read_all()
    assert first == 1
    assert second == 0
    assert transport.terminated == ["run-manager"]
    assert len(coordinator.spawned) == 1
    assert coordinator.spawned[0][0].instance_id == "run-manager"
    assert not any(event.type == RUN_MANAGER_RESIDENT_RESTART_FAILED for event in events)
    restarted = [
        event for event in events
        if event.type == RUN_MANAGER_RESIDENT_RESTARTED
    ]
    assert len(restarted) == 1
    assert restarted[0].causation_id == request.id
    assert restarted[0].payload["restart_scope"] == "resident_only"
    assert restarted[0].payload["workflow_restart"] is False
    assert restarted[0].payload["first_tick_mode"] == "observe_only"
    briefing = state_dir / "briefings" / "run-manager-resident.md"
    assert "First tick mode is observe-only" in briefing.read_text(encoding="utf-8")
    assert transport.sent and "observe-only" in transport.sent[0][2]


def test_pane_probe_observes_dedicated_resident_run_manager(
    tmp_path: Path,
) -> None:
    cfg = _resident_config(session_mode="dedicated")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    calls: list[list[str]] = []

    def fake_tmux(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["tmux", "display-message", "-p"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="%42\tclaude\t/tmp/project\t0\n",
                stderr="",
            )
        if args[:2] == ["tmux", "capture-pane"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="Resident run manager watching the run\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="unexpected")

    probe = build_runtime_pane_probe(
        state_dir,
        config=cfg,
        project_root=tmp_path,
        runner=fake_tmux,
    )

    resident = next(
        item for item in probe["panes"]
        if item["instance_id"] == "run-manager"
    )
    assert probe["summary"]["expected"] == 2
    assert resident["target"] == "zf-resident-test-run-manager:run-manager"
    assert resident["target_source"] == "run_manager_resident"
    assert resident["alive"] is True
    assert any(
        "zf-resident-test-run-manager:run-manager" in call
        for call in calls
    )


def test_run_manager_projection_detects_prompted_resident_without_observation(
    tmp_path: Path,
) -> None:
    cfg = _resident_config(session_mode="dedicated")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    events = [
        ZfEvent(
            type="run.manager.resident.spawned",
            id="evt-spawned",
            ts=_stale_event_ts(),
            actor="zf-cli",
            payload={"ready": True, "session_mode": "dedicated"},
        ),
        ZfEvent(
            type="run.manager.resident.prompted",
            id="evt-prompted",
            ts=_stale_event_ts(1),
            actor="zf-cli",
            payload={"prompted": True},
        ),
    ]

    projection = build_run_manager_projection(
        state_dir,
        events=events,
        config=cfg,
        project_root=tmp_path,
    )

    assert projection["resident_agent"]["status"] == "stalled"
    actions = [
        item for item in projection["pending_actions"]
        if item.get("failure_class") == "run_manager_resident_agent_stalled"
    ]
    assert len(actions) == 1
    assert actions[0]["preflight"]["status"] == "passed"
    assert actions[0]["policy_decision"]["decision"] == "needs_diagnosis"
    assert actions[0]["source_event_ids"] == ["evt-spawned", "evt-prompted"]


def test_run_manager_tick_requests_diagnosis_for_resident_stall(
    tmp_path: Path,
) -> None:
    cfg = _resident_config(session_mode="dedicated")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="run.manager.resident.spawned",
        id="evt-spawned",
        ts=_stale_event_ts(),
        actor="zf-cli",
        payload={"ready": True, "session_mode": "dedicated"},
    ))
    log.append(ZfEvent(
        type="run.manager.resident.prompted",
        id="evt-prompted",
        ts=_stale_event_ts(1),
        actor="zf-cli",
        payload={"prompted": True},
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=EventWriter(log),
        config=cfg,
        project_root=tmp_path,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )

    assert result.autoresearch_requested == 1
    requests = [
        event for event in log.read_all()
        if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
    ]
    assert len(requests) == 1
    assert requests[0].payload["failure_class"] == "run_manager_resident_agent_stalled"


def test_run_manager_tick_reprompts_live_resident_pane(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _resident_config(session_mode="dedicated")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    briefing = state_dir / "briefings" / "run-manager-resident.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text("observe this run", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="run.manager.resident.spawned",
        id="evt-spawned",
        ts=_stale_event_ts(),
        actor="zf-cli",
        payload={
            "ready": True,
            "session_mode": "dedicated",
            "tmux_session": "zf-rm",
        },
    ))
    log.append(ZfEvent(
        type="run.manager.resident.prompted",
        id="evt-prompted",
        ts=_stale_event_ts(1),
        actor="zf-cli",
        payload={"prompted": True, "briefing_path": str(briefing)},
    ))
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["tmux", "display-message", "-p"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=f"%9\tclaude\t{tmp_path}\t0\n",
                stderr="",
            )
        if args[:2] == ["tmux", "send-keys"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="unexpected")

    monkeypatch.setattr("zf.runtime.run_manager.subprocess.run", fake_run)
    monkeypatch.setattr("zf.runtime.run_manager.time.sleep", lambda _seconds: None)

    result = run_manager_tick(
        state_dir=state_dir,
        writer=EventWriter(log),
        config=cfg,
        project_root=tmp_path,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )

    assert result.actions_applied == 1
    assert result.autoresearch_requested == 0
    reprompts = [
        event for event in log.read_all()
        if event.type == "run.manager.resident.prompted"
        and event.payload.get("reprompt") is True
    ]
    assert len(reprompts) == 1
    assert reprompts[0].payload["target"] == "zf-rm:run-manager"
    assert reprompts[0].payload["briefing_refreshed"] is True
    assert "你不是 monitor-only" in briefing.read_text(encoding="utf-8")
    assert any(call[:2] == ["tmux", "send-keys"] for call in calls)
    assert any(event.type == "run.manager.action.verify.passed" for event in log.read_all())


def test_supervisor_attention_requests_diagnosis_without_resident_reprompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _resident_config(session_mode="dedicated")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    briefing = state_dir / "briefings" / "run-manager-resident.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text("observe this run", encoding="utf-8")
    _write_supervisor_attention(state_dir)
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="run.manager.resident.spawned",
        id="evt-spawned",
        ts=_stale_event_ts(),
        actor="zf-cli",
        payload={
            "ready": True,
            "session_mode": "dedicated",
            "tmux_session": "zf-rm",
        },
    ))
    log.append(ZfEvent(
        type="run.manager.resident.prompted",
        id="evt-prompted",
        ts=_stale_event_ts(1),
        actor="zf-cli",
        payload={"prompted": True, "briefing_path": str(briefing)},
    ))
    log.append(ZfEvent(
        type="run.manager.agent.observation",
        id="evt-observed",
        ts=_stale_event_ts(2),
        actor="run-manager",
        payload={"status": "watching"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=cfg,
        project_root=tmp_path,
    )
    pending = projection["pending_actions"][0]
    assert pending["action"] == "diagnose-attention"
    assert pending["safe_resume_action"] == "diagnose_attention"
    assert pending["owner_route"] == "run_manager"
    assert pending["policy_decision"]["decision"] == "needs_diagnosis"
    assert projection["status_explain"]["next_auto_action"] == "run_manager_diagnosis"

    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="unexpected")

    monkeypatch.setattr("zf.runtime.run_manager.subprocess.run", fake_run)
    monkeypatch.setattr("zf.runtime.run_manager.time.sleep", lambda _seconds: None)

    result = run_manager_tick(
        state_dir=state_dir,
        writer=EventWriter(log),
        config=cfg,
        project_root=tmp_path,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied == 0
    assert result.autoresearch_requested == 1
    assert [event for event in events if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED]
    reprompts = [
        event for event in events
        if event.type == "run.manager.resident.prompted"
        and event.payload.get("reprompt") is True
    ]
    assert not reprompts
    assert "recommended_route=repair" not in briefing.read_text(encoding="utf-8")
    assert not any(call[:2] == ["tmux", "send-keys"] for call in calls)


def test_run_manager_tick_blocks_reprompt_into_shell_only_pane(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _resident_config(session_mode="dedicated")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    briefing = state_dir / "briefings" / "run-manager-resident.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text("observe this run", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="run.manager.resident.spawned",
        id="evt-spawned",
        ts=_stale_event_ts(),
        actor="zf-cli",
        payload={
            "ready": True,
            "session_mode": "dedicated",
            "tmux_session": "zf-rm",
        },
    ))
    log.append(ZfEvent(
        type="run.manager.resident.prompted",
        id="evt-prompted",
        ts=_stale_event_ts(1),
        actor="zf-cli",
        payload={"prompted": True, "briefing_path": str(briefing)},
    ))
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["tmux", "display-message", "-p"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=f"%9\tbash\t{tmp_path}\t0\n",
                stderr="",
            )
        if args[:2] == ["tmux", "send-keys"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="unexpected")

    monkeypatch.setattr("zf.runtime.run_manager.subprocess.run", fake_run)

    result = run_manager_tick(
        state_dir=state_dir,
        writer=EventWriter(log),
        config=cfg,
        project_root=tmp_path,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )

    assert result.actions_blocked == 1
    assert not any(call[:2] == ["tmux", "send-keys"] for call in calls)
    blocked = [
        event for event in log.read_all()
        if event.type == "run.manager.action.blocked"
    ]
    assert blocked
    assert "not an agent" in blocked[0].payload["reason"]


def test_run_manager_projection_clears_resident_stall_after_observation(
    tmp_path: Path,
) -> None:
    cfg = _resident_config(session_mode="dedicated")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    events = [
        ZfEvent(
            type="run.manager.resident.spawned",
            id="evt-spawned",
            ts=_stale_event_ts(),
            actor="zf-cli",
            payload={"ready": True, "session_mode": "dedicated"},
        ),
        ZfEvent(
            type="run.manager.resident.prompted",
            id="evt-prompted",
            ts=_stale_event_ts(1),
            actor="zf-cli",
            payload={"prompted": True},
        ),
        ZfEvent(
            type="run.manager.agent.observation",
            id="evt-observation",
            ts=_stale_event_ts(60),
            actor="run-manager",
            payload={"status": "watching"},
        ),
    ]

    projection = build_run_manager_projection(
        state_dir,
        events=events,
        config=cfg,
        project_root=tmp_path,
    )

    assert projection["resident_agent"]["status"] == "observing"
    assert not [
        item for item in projection["pending_actions"]
        if item.get("failure_class") == "run_manager_resident_agent_stalled"
    ]


def test_run_manager_projection_uses_archived_observation_when_prompt_is_newer(
    tmp_path: Path,
) -> None:
    cfg = _resident_config(session_mode="dedicated")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    events = [
        ZfEvent(
            type="run.manager.resident.spawned",
            id="evt-spawned",
            ts=_stale_event_ts(),
            actor="zf-cli",
            payload={"ready": True, "session_mode": "dedicated"},
        ),
        ZfEvent(
            type="run.manager.agent.observation",
            id="evt-observation",
            ts=_stale_event_ts(60),
            actor="run-manager",
            payload={"status": "watching archived event window"},
        ),
        ZfEvent(
            type="run.manager.resident.prompted",
            id="evt-prompted",
            ts=_stale_event_ts(120),
            actor="zf-cli",
            payload={"prompted": True},
        ),
    ]

    projection = build_run_manager_projection(
        state_dir,
        events=events,
        config=cfg,
        project_root=tmp_path,
    )

    assert projection["resident_agent"]["status"] == "observing"
    assert projection["resident_agent"]["latest_agent_event_id"] == "evt-observation"
    assert not [
        item for item in projection["pending_actions"]
        if item.get("failure_class") == "run_manager_resident_agent_stalled"
    ]


def test_resident_briefing_contains_wait_semantics_rules(tmp_path) -> None:
    """ZF-E2E-PRDCTL-P1-5: resume 前置三点核对(完成证据/兄弟在飞/unroutable
    前科)必须在 briefing 里——csvstats 轮 resident 4 次把等待判成停滞。"""
    role = build_resident_run_manager_role(_resident_config())
    assert role is not None
    briefing = build_resident_run_manager_briefing(
        project_root=tmp_path,
        state_dir=tmp_path / ".zf",
        role=role,
    )
    assert "完成证据早于 stall 信号仍然算完成" in briefing
    assert "等待中的流不是停滞" in briefing
    assert "workflow.resume.gate_unroutable" in briefing
