from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.start import _record_dormant_worker_state
from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RoleLifecycleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.events.writer import EventWriter
from zf.core.state.git_state import GitState
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.session_tailer import claude_session_path
from zf.runtime.skill_dispatch_treatment import SkillDispatchTreatmentError
from zf.runtime.spawn_coordinator import SpawnCoordinator
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
)
from zf.web.projections.common import _derive_lifecycle_state


class RecordingTransport:
    def __init__(self) -> None:
        self.alive: set[str] = set()
        self.spawned: list[tuple[str, list[str], Path | None]] = []
        self.sent: list[str] = []
        self.terminated: list[str] = []
        self.ready = True

    def spawn(self, role, argv, *, cwd=None) -> None:  # noqa: ANN001
        self.alive.add(role.instance_id)
        self.spawned.append((role.instance_id, list(argv), cwd))

    def is_alive(self, role_name: str) -> bool:
        return role_name in self.alive

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        return self.ready and role_name in self.alive

    def readiness_diagnostics(self, role_name: str) -> dict:
        return {
            "failure_class": "provider_launch_not_submitted",
            "pane_alive": role_name in self.alive,
            "current_command": "bash",
            "process_probe": {"available": True, "processes": []},
            "last_screen_excerpt": "codex --enable hooks",
            "launch_attempts": 2,
            "requested_timeout_seconds": 240.0,
            "effective_timeout_seconds": 20.0,
        }

    def send_task(self, role_name, briefing_path, prompt, *, context=None) -> None:  # noqa: ANN001
        assert role_name in self.alive
        self.sent.append(role_name)

    def terminate(self, role_name: str) -> None:
        self.alive.discard(role_name)
        self.terminated.append(role_name)

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""

    def poll_events(self) -> list[ZfEvent]:
        return []


def _runtime(tmp_path: Path) -> tuple[Orchestrator, RoleConfig, RecordingTransport]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    role = RoleConfig(
        name="impl",
        backend="claude-code",
        instance_id="impl-1",
        lifecycle=RoleLifecycleConfig(
            mode="on_demand",
            idle_seconds=0,
            cooldown_seconds=0,
        ),
    )
    config = ZfConfig(
        project=ProjectConfig(name="lifecycle-test"),
        session=SessionConfig(tmux_session="lifecycle-test"),
        roles=[role],
    )
    transport = RecordingTransport()
    return (
        Orchestrator(
            state_dir,
            config,
            transport,
            project_root=tmp_path,
        ),
        role,
        transport,
    )


def test_start_registration_keeps_on_demand_role_dormant(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    coordinator = SpawnCoordinator(
        state_dir=orchestrator.state_dir,
        registry=registry,
        transport=transport,
        project_root=str(tmp_path),
        event_log=orchestrator.event_log,
        config=orchestrator.config,
    )

    coordinator.prepare_provider_session(role)
    _record_dormant_worker_state(
        event_log=orchestrator.event_log,
        registry=registry,
        role=role,
    )

    meta = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).instance_meta()[role.instance_id]
    assert transport.spawned == []
    assert meta["lifecycle_state"] == "dormant"
    assert meta["provider_session_config_digest"]
    assert [
        event.type for event in orchestrator.event_log.read_all()
    ][-2:] == ["role.lifecycle.dormant", "worker.state.changed"]
    assert _derive_lifecycle_state(
        "dormant",
        active_task="",
        signal={},
    ) == "dormant"


def test_taskless_skill_materialization_cannot_overwrite_active_treatment(
    tmp_path: Path,
) -> None:
    orchestrator, role, _transport = _runtime(tmp_path)
    orchestrator._active_skill_treatment_tasks = {  # type: ignore[attr-defined]
        role.instance_id: "TASK-CANDIDATE",
    }

    with pytest.raises(SkillDispatchTreatmentError, match="TASK-CANDIDATE"):
        orchestrator._record_skill_provenance(role=role)  # type: ignore[attr-defined]


def test_dispatch_primitive_activates_on_demand_role_before_send(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    briefing = orchestrator.state_dir / "briefings" / "impl.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text("work\n", encoding="utf-8")

    orchestrator._send_transport_task(
        role.instance_id,
        briefing,
        "work",
        None,
    )

    assert len(transport.spawned) == 1
    assert transport.sent == [role.instance_id]
    meta = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).instance_meta()[role.instance_id]
    assert meta["lifecycle_state"] == "active"


def test_fanout_liveness_activates_on_demand_role_before_watchdog(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)

    assert orchestrator._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id="fanout-on-demand",
        stage_id="impl",
        child_id="child-1",
        run_id="run-child-1",
        trace_id="trace-1",
        task_id="TASK-1",
    )

    assert [item[0] for item in transport.spawned] == [role.instance_id]
    events = orchestrator.event_log.read_all()
    assert [
        event.payload["instance_id"]
        for event in events
        if event.type == "role.lifecycle.ready"
    ] == [role.instance_id]
    assert not [event for event in events if event.type == "worker.respawned"]


def _seed_reader_fanout_context(
    orchestrator: Orchestrator,
    role: RoleConfig,
    transport: RecordingTransport,
    *,
    task_id: str,
    trace_id: str,
    terminal: bool = True,
) -> None:
    role.backend = "codex"
    role.role_kind = "reader"
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(orchestrator.project_root),
    )
    registry.mark_backend(role.instance_id, role.backend)
    registry.bind_codex_session(
        role.instance_id,
        "00000000-0000-4000-8000-000000000001",
    )
    registry.mark_spawned(role.instance_id)
    registry.update_instance_meta(
        role.instance_id,
        lifecycle_state="active",
    )
    transport.alive.add(role.instance_id)
    orchestrator._set_worker_state(role.instance_id, "idle", force=True)
    payload = {
        "fanout_id": f"fanout-{task_id.lower()}",
        "stage_id": "plan",
        "child_id": role.instance_id,
        "run_id": f"run-{task_id.lower()}",
        "role_instance": role.instance_id,
        "task_id": task_id,
        "trace_id": trace_id,
        "workflow_run_id": trace_id,
    }
    orchestrator.event_writer.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id=task_id,
        payload=payload,
        correlation_id=trace_id,
    ))
    if terminal:
        orchestrator.event_writer.append(ZfEvent(
            type="fanout.child.completed",
            actor=role.instance_id,
            task_id=task_id,
            payload=payload,
            correlation_id=trace_id,
        ))


@pytest.mark.parametrize(
    ("task_id", "trace_id"),
    [
        ("TASK-NEW", "workflow-new-task"),
        ("TASK-OLD", "workflow-new-run"),
    ],
)
def test_reader_fanout_new_root_scope_uses_fresh_provider_context(
    tmp_path: Path,
    task_id: str,
    trace_id: str,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(
        orchestrator,
        role,
        transport,
        task_id="TASK-OLD",
        trace_id="workflow-old",
    )

    assert orchestrator._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id="fanout-new",
        stage_id="plan",
        child_id=role.instance_id,
        run_id="run-new",
        trace_id=trace_id,
        task_id=task_id,
    )

    assert transport.terminated == [role.instance_id]
    assert [item[0] for item in transport.spawned] == [role.instance_id]
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    meta = registry.instance_meta()[role.instance_id]
    assert meta["fanout_context_task_id"] == task_id
    assert meta["fanout_context_trace_id"] == trace_id
    recycled = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "worker.recycled"
    ]
    assert recycled[-1].payload["reason"] == "reader_root_context_changed"
    assert recycled[-1].payload["previous_task_id"] == "TASK-OLD"
    assert recycled[-1].payload["session_strategy"] == (
        "reader_task_boundary_clear_codex"
    )


def test_reader_fanout_same_root_reuses_provider_context(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(
        orchestrator,
        role,
        transport,
        task_id="TASK-SAME",
        trace_id="workflow-same",
    )

    assert orchestrator._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id="fanout-rework",
        stage_id="plan",
        child_id=role.instance_id,
        run_id="run-rework",
        trace_id="workflow-same",
        task_id="TASK-SAME",
    )

    assert transport.terminated == []
    assert transport.spawned == []
    assert not [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "worker.recycled"
    ]


def test_reader_fanout_context_switch_waits_for_active_child(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(
        orchestrator,
        role,
        transport,
        task_id="TASK-ACTIVE",
        trace_id="workflow-active",
        terminal=False,
    )

    assert not orchestrator._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id="fanout-next",
        stage_id="plan",
        child_id=role.instance_id,
        run_id="run-next",
        trace_id="workflow-next",
        task_id="TASK-NEXT",
    )

    assert transport.terminated == []
    assert transport.spawned == []
    deferred = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "fanout.child.dispatch_deferred"
    ]
    assert "fanout_child_active" in deferred[-1].payload["reason"]


def test_writer_fanout_does_not_rotate_at_reader_context_boundary(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    _seed_reader_fanout_context(
        orchestrator,
        role,
        transport,
        task_id="TASK-OLD",
        trace_id="workflow-old",
    )
    role.role_kind = "writer"

    assert orchestrator._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id="fanout-writer",
        stage_id="impl",
        child_id=role.instance_id,
        run_id="run-writer",
        trace_id="workflow-writer",
        task_id="TASK-WRITER",
    )

    assert transport.terminated == []
    assert transport.spawned == []


def test_fanout_activation_failure_does_not_bypass_lifecycle_with_respawn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    orchestrator, role, _transport = _runtime(tmp_path)
    respawned: list[str] = []

    def fail_activation(*_args, **_kwargs) -> bool:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(orchestrator, "_ensure_role_active", fail_activation)
    monkeypatch.setattr(
        orchestrator,
        "_respawn_instance",
        lambda target, **_kwargs: respawned.append(target.instance_id),
    )

    assert not orchestrator._ensure_fanout_role_dispatchable(
        role=role,
        fanout_id="fanout-on-demand",
        stage_id="impl",
        child_id="child-1",
        run_id="run-child-1",
        trace_id="trace-1",
        task_id="TASK-1",
    )
    assert respawned == []
    deferred = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "fanout.child.dispatch_deferred"
    ]
    assert "role_activation_failed:provider unavailable" in (
        deferred[-1].payload["reason"]
    )


def test_activation_failure_does_not_claim_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    task = Task(
        id="TASK-ACTIVATION-FAIL",
        title="activation must precede ownership",
        status="backlog",
    )
    orchestrator.task_store.add(task)

    def fail_activation(*_args, **_kwargs) -> bool:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        orchestrator,
        "_ensure_role_active",
        fail_activation,
    )

    assert orchestrator._dispatch_task(task, role) is False
    stored = orchestrator.task_store.get(task.id)
    assert stored is not None
    assert stored.status == "backlog"
    assert not stored.assigned_to
    assert transport.sent == []
    failed = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "orchestrator.dispatch_failed"
    ]
    assert failed[-1].payload["stage"] == "role_activation"


def test_on_demand_ready_failure_emits_structured_transport_diagnostics(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    transport.ready = False

    with pytest.raises(Exception, match="provider readiness failed"):
        orchestrator._ensure_role_active(role, task_id="TASK-READY-FAIL")

    failed = next(
        event
        for event in reversed(orchestrator.event_log.read_all())
        if event.type == "role.lifecycle.activation.failed"
    )
    assert failed.payload["failure_class"] == "provider_launch_not_submitted"
    assert failed.payload["pane_alive"] is True
    assert failed.payload["current_command"] == "bash"
    assert failed.payload["launch_attempts"] == 2
    assert failed.payload["effective_timeout_seconds"] == 20.0
    assert failed.payload["last_screen_excerpt"] == "codex --enable hooks"


def test_codex_activation_without_session_reports_fresh_provider_launch(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    role.backend = "codex"
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml", project_root=str(tmp_path),
    )
    registry.mark_spawned(role.instance_id)
    registry.update_instance_meta(role.instance_id, lifecycle_state="suspended")

    assert orchestrator._ensure_role_active(role, task_id="TASK-FRESH") is True

    ready = next(e for e in orchestrator.event_log.read_all()
                 if e.type == "role.lifecycle.ready")
    assert ready.payload["from"] == "resuming"
    assert ready.payload["is_resume"] is False
    assert "resume" not in transport.spawned[-1][1]


def test_codex_resume_readiness_failure_falls_back_to_fresh_session(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    role.backend = "codex"
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    session_id = "58585858-5858-5858-5858-585858585858"
    rollout = (
        orchestrator.state_dir
        / "workdirs"
        / role.instance_id
        / "codex-home"
        / "sessions"
        / "2026"
        / "08"
        / "09"
        / f"rollout-2026-08-09T00-00-00-{session_id}.jsonl"
    )
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        json.dumps({
            "type": "session_meta",
            "payload": {"cwd": str(tmp_path)},
        }) + "\n",
        encoding="utf-8",
    )
    registry.bind_codex_session(
        role.instance_id,
        session_id,
        session_path=rollout,
    )
    registry.mark_spawned(role.instance_id)
    registry.update_instance_meta(
        role.instance_id,
        lifecycle_state="suspended",
    )
    transport.ready = False

    with pytest.raises(Exception, match="provider readiness failed"):
        orchestrator._ensure_role_active(role, task_id="TASK-CODEX-RESUME")

    reloaded = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    assert reloaded.get(role.instance_id) is None
    assert "resume" in transport.spawned[-1][1]
    downgraded = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "role.lifecycle.continuity.downgraded"
    ]
    assert downgraded[-1].payload["fallback"] == "fresh_provider_session"


def test_active_workflow_operation_prevents_hibernation(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-1")
    orchestrator.event_writer.append(ZfEvent(
        type="workflow.operation.requested",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "workflow_run_id": orchestrator._current_run_id(),
            "operation_id": "wop-active",
            "operation_type": "agent",
            "request_hash": "a" * 64,
            "role_instance": role.instance_id,
        },
    ))
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    registry.record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id in transport.alive
    rejected = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "role.lifecycle.suspend.rejected"
    ]
    assert rejected == []


def test_terminal_run_operation_does_not_prevent_hibernation(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-OLD")
    run_id = "run-terminal-operation"
    operation = {
        "workflow_run_id": run_id,
        "operation_id": "wop-terminal-operation",
        "operation_type": "agent",
        "request_hash": "a" * 64,
        "role_instance": role.instance_id,
        "task_id": "TASK-OLD",
    }
    orchestrator.event_writer.append(ZfEvent(
        type="workflow.operation.requested",
        actor="zf-cli",
        task_id="TASK-OLD",
        payload=operation,
        correlation_id=run_id,
    ))
    orchestrator.event_writer.append(ZfEvent(
        type="workflow.operation.started",
        actor="zf-cli",
        task_id="TASK-OLD",
        payload=operation,
        correlation_id=run_id,
    ))
    orchestrator.event_writer.append(ZfEvent(
        type="run.goal.blocked",
        actor="kernel",
        task_id="TASK-OLD",
        payload={"run_id": run_id},
        correlation_id=run_id,
    ))
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id not in transport.alive
    assert role.instance_id in transport.terminated


def test_reactivated_blocked_run_operation_prevents_hibernation(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-RESUMED")
    run_id = "run-reactivated-operation"
    operation = {
        "workflow_run_id": run_id,
        "operation_id": "wop-reactivated-operation",
        "operation_type": "agent",
        "request_hash": "a" * 64,
        "role_instance": role.instance_id,
        "task_id": "TASK-RESUMED",
    }
    for event_type in (
        "workflow.operation.requested",
        "workflow.operation.started",
    ):
        orchestrator.event_writer.append(ZfEvent(
            type=event_type,
            actor="zf-cli",
            task_id="TASK-RESUMED",
            payload=operation,
            correlation_id=run_id,
        ))
    orchestrator.event_writer.append(ZfEvent(
        type="run.goal.blocked",
        actor="kernel",
        task_id="TASK-RESUMED",
        payload={"run_id": run_id},
        correlation_id=run_id,
    ))
    orchestrator.event_writer.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        task_id="TASK-RESUMED",
        payload={"run_id": run_id, "status": "active"},
        correlation_id=run_id,
    ))
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id in transport.alive
    assert role.instance_id not in transport.terminated


def test_uncheckpointed_source_change_prevents_hibernation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-DIRTY")
    workdir = (
        orchestrator.state_dir / "workdirs" / role.instance_id / "project"
    )
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "zf.runtime.role_lifecycle_runtime.capture_git_state",
        lambda _path: GitState(dirty_files=["src/app.py"]),
    )
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id in transport.alive
    rejected = [
        event
        for event in orchestrator.event_log.read_all()
        if event.type == "role.lifecycle.suspend.rejected"
    ]
    assert rejected[-1].payload["reason"] == "workdir_dirty_without_checkpoint"
    assert rejected[-1].payload["dirty_files"] == ["src/app.py"]


def test_materialized_skill_projection_does_not_prevent_hibernation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-SKILL")
    workdir = (
        orchestrator.state_dir / "workdirs" / role.instance_id / "project"
    )
    workdir.mkdir(parents=True, exist_ok=True)
    manifest = (
        orchestrator.state_dir
        / "workdirs"
        / role.instance_id
        / "runtime"
        / "skills-manifest.json"
    )
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({
            "instance_id": role.instance_id,
            "skills": [{
                "materialized_to": str(
                    workdir / ".claude" / "skills" / "verify-review"
                ),
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "zf.runtime.role_lifecycle_runtime.capture_git_state",
        lambda _path: GitState(
            dirty_files=[".claude/skills/verify-review/SKILL.md"]
        ),
    )
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id not in transport.alive
    assert role.instance_id in transport.terminated


def test_admitted_terminal_operation_checkpoints_preserved_dirty_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-PLAN")
    workdir = (
        orchestrator.state_dir / "workdirs" / role.instance_id / "project"
    )
    workdir.mkdir(parents=True, exist_ok=True)
    operation_id = "wop-plan-admitted"
    common_payload = {
        "workflow_run_id": orchestrator._current_run_id(),
        "operation_id": operation_id,
        "task_id": "TASK-PLAN",
        "role_instance": role.instance_id,
    }
    orchestrator.event_writer.append(ZfEvent(
        type="workflow.operation.requested",
        actor="zf-cli",
        task_id="TASK-PLAN",
        payload={
            **common_payload,
            "operation_type": "agent",
            "request_hash": "b" * 64,
        },
    ))
    orchestrator.event_writer.append(ZfEvent(
        type="workflow.operation.started",
        actor="zf-cli",
        task_id="TASK-PLAN",
        payload=common_payload,
    ))
    orchestrator.event_writer.append(ZfEvent(
        type="workflow.operation.settled",
        actor="zf-cli",
        task_id="TASK-PLAN",
        payload={
            **common_payload,
            "admitted_call_result_ref": {
                "ref": "artifacts/call-results/plan.json",
                "sha256": "c" * 64,
            },
        },
    ))
    monkeypatch.setattr(
        "zf.runtime.role_lifecycle_runtime.capture_git_state",
        lambda _path: GitState(
            dirty_files=["docs/plans/issue-plan.md", "artifacts/plan/task_map.json"]
        ),
    )
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
        "operation_id": operation_id,
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id not in transport.alive
    assert role.instance_id in transport.terminated


def test_workflow_fanout_anchor_is_not_generic_runnable_role_work(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-ROOT")
    orchestrator.task_store.add(Task(
        id="TASK-ROOT",
        title="workflow root anchor",
        status="backlog",
        contract=TaskContract(evidence_contract={
            "workflow_fanout_anchor": True,
        }),
    ))
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id not in transport.alive
    assert role.instance_id in transport.terminated
    assert not any(
        event.type == "role.lifecycle.suspend.rejected"
        for event in orchestrator.event_log.read_all()
    )


def test_ready_task_for_other_role_does_not_prevent_hibernation(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    verify_role = RoleConfig(
        name="verify",
        backend="claude-code",
        instance_id="verify-1",
    )
    orchestrator.config.roles.append(verify_role)
    orchestrator._ensure_role_active(role, task_id="TASK-IMPL-DONE")
    orchestrator.task_store.add(Task(
        id="TASK-VERIFY-READY",
        title="ready for a different stage role",
        status="backlog",
        assigned_to=verify_role.instance_id,
    ))
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
        "checkpoint_ref": "checkpoint://TASK-IMPL-DONE",
    })

    orchestrator._hibernate_idle_roles()

    assert role.instance_id not in transport.alive
    assert verify_role.instance_id not in transport.terminated
    assert (
        orchestrator.task_store.get("TASK-VERIFY-READY").status
        == "backlog"
    )


def test_hibernate_preserves_session_and_next_activation_resumes(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    orchestrator._ensure_role_active(role, task_id="TASK-1")
    registry = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    session_id = registry.get(role.instance_id)
    assert session_id is not None
    session_path = claude_session_path(str(tmp_path), str(session_id))
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("{}\n", encoding="utf-8")
    registry.record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
        "checkpoint_ref": "checkpoint://TASK-1",
    })

    orchestrator._hibernate_idle_roles()

    after_suspend = RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    assert after_suspend.get(role.instance_id) == session_id
    assert (
        after_suspend.instance_meta()[role.instance_id]["lifecycle_state"]
        == "suspended"
    )
    assert role.instance_id not in transport.alive

    orchestrator._ensure_role_active(role, task_id="TASK-2")

    assert len(transport.spawned) == 2
    second_argv = transport.spawned[-1][1]
    assert "--resume" in second_argv
    assert str(session_id) in second_argv
    assert RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).get(role.instance_id) == session_id


def test_codex_task_stage_spawn_can_reactivate_in_role_workdir(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    role.backend = "codex"
    role.role_kind = "reader"
    orchestrator.config.runtime.workdirs.enabled = True
    orchestrator.config.runtime.workdirs.mode = "dry-run"
    task_workspace = tmp_path / "task-stage-workspace"
    task_workspace.mkdir()

    orchestrator._ensure_role_active(
        role,
        task_id="TASK-VERIFY",
        spawn_cwd=task_workspace,
    )

    role_root = orchestrator.state_dir / "workdirs" / role.instance_id
    marker = role_root / ".zf-workdir-owner.json"
    assert json.loads(marker.read_text(encoding="utf-8"))["instance_id"] == (
        role.instance_id
    )

    transport.terminate(role.instance_id)
    RoleSessionRegistry(
        orchestrator.state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).update_instance_meta(role.instance_id, lifecycle_state="suspended")

    orchestrator._ensure_role_active(role, task_id="ROOT-CANDIDATE-VERIFY")

    assert [item[0] for item in transport.spawned] == [
        role.instance_id,
        role.instance_id,
    ]
    assert (role_root / "meta.json").is_file()


def test_mock_e2e_dormant_operation_settlement_suspend_and_same_lane_resume(
    tmp_path: Path,
) -> None:
    orchestrator, role, transport = _runtime(tmp_path)
    state_dir = orchestrator.state_dir
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    coordinator = SpawnCoordinator(
        state_dir=state_dir,
        registry=registry,
        transport=transport,
        project_root=str(tmp_path),
        event_log=orchestrator.event_log,
        config=orchestrator.config,
    )
    coordinator.prepare_provider_session(role)
    _record_dormant_worker_state(
        event_log=orchestrator.event_log,
        registry=registry,
        role=role,
    )

    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-IMPL-1",
        title="provider root operation",
        status="in_progress",
        assigned_to=role.instance_id,
    ))
    briefing = state_dir / "briefings" / "TASK-IMPL-1.md"
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text("implement\n", encoding="utf-8")
    orchestrator._send_transport_task(
        role.instance_id,
        briefing,
        "implement",
        None,
    )
    orchestrator._set_worker_state(
        role.instance_id,
        "busy",
        reason="mock operation started",
        task_id="TASK-IMPL-1",
    )

    run_id = str(orchestrator._current_run_id() or "")
    session_id = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).get(role.instance_id)
    assert session_id is not None
    session_path = claude_session_path(str(tmp_path), str(session_id))
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text("{}\n", encoding="utf-8")
    writer = EventWriter(orchestrator.event_log)
    operations = WorkflowOperationService(
        state_dir=state_dir,
        event_log=orchestrator.event_log,
        event_writer=writer,
    )
    call_task_id = "CALL-RESULT-1"
    operation = operations.ensure_operation(
        workflow_run_id=run_id,
        operation_id="wop-TASK-IMPL-1",
        operation_type="agent",
        request={"prompt": "implement"},
        task_id=call_task_id,
        role_instance=role.instance_id,
    )
    operations.mark_started(
        operation_id=operation.operation_id,
        request_hash=operation.request_hash,
        workflow_run_id=run_id,
        task_id=call_task_id,
        role_instance=role.instance_id,
        provider_session_id=str(session_id),
    )
    admission = CallResultAdmissionService(
        state_dir=state_dir,
        event_log=orchestrator.event_log,
        event_writer=writer,
        operation_service=operations,
    )
    from tests.test_call_result_admission import _persisted_verification_payload

    result_payload = {
        **_persisted_verification_payload(state_dir, "passed", identity={
            "workflow_run_id": run_id, "task_id": call_task_id,
            "role_instance": role.instance_id,
        }),
        "provider_operation_summary": {
            "schema_version": "provider-operation-summary.v1",
            "workflow_run_id": run_id,
            "operation_id": operation.operation_id,
            "provider_session_id": str(session_id),
            "settlement": "settled",
            "child_count": 2,
            "child_status_counts": {"completed": 2},
            "active_child_count": 0,
            "peak_parallel_agents": 2,
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "cost_usd": 0.2,
        },
    }
    outcome = admission.report_legacy_result(
        ZfEvent(
            type="verify.child.completed",
            actor=role.instance_id,
            task_id=call_task_id,
            payload=result_payload,
        ),
        mode="blocking",
        operation={
            "workflow_run_id": run_id,
            "operation_id": operation.operation_id,
            "request_hash": operation.request_hash,
            "provider_session_max_parallel_agents": 4,
            "budget_snapshot": {"remaining_usd": 1.0},
        },
    )
    assert outcome.admitted is True
    assert load_workflow_operation(
        orchestrator.event_log,
        operation.operation_id,
    )["status"] == "settled"

    store.update("TASK-IMPL-1", status="done")
    orchestrator._set_worker_state(
        role.instance_id,
        "idle",
        reason="mock operation settled",
        task_id="TASK-IMPL-1",
        force=True,
    )
    RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).record_heartbeat(role.instance_id, {
        "state": "idle",
        "last_action_ts": 1,
        "checkpoint_ref": "checkpoint://TASK-IMPL-1",
    })
    orchestrator._hibernate_idle_roles()
    assert role.instance_id not in transport.alive

    store.add(Task(
        id="TASK-REWORK-1",
        title="same lane continuation",
        status="in_progress",
        assigned_to=role.instance_id,
    ))
    rework_briefing = state_dir / "briefings" / "TASK-REWORK-1.md"
    rework_briefing.write_text("continue\n", encoding="utf-8")
    orchestrator._send_transport_task(
        role.instance_id,
        rework_briefing,
        "continue",
        None,
    )
    store.update("TASK-REWORK-1", status="done")
    writer.append(ZfEvent(
        type="run.goal.completed",
        actor="zf-cli",
        correlation_id=run_id,
        payload={
            "run_id": run_id,
            "completed_task_ids": ["TASK-IMPL-1", "TASK-REWORK-1"],
        },
    ))

    archived = store.list_all_with_archive()
    assert {task.id for task in archived} == {
        "TASK-IMPL-1",
        "TASK-REWORK-1",
    }
    assert all(task.assigned_to == role.instance_id for task in archived)
    assert RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    ).get(role.instance_id) == session_id
    assert "--resume" in transport.spawned[-1][1]
