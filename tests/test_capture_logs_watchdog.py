"""Tests for G-RESUME-4: active watchdog in _capture_logs.

On each _capture_logs cycle, the orchestrator polls transport.is_alive
for every worker instance. Consecutive failures past a threshold trigger
a respawn via SpawnCoordinator, which emits worker.respawned.

The threshold defense prevents false positives during startup when the
pane might not be fully ready yet.
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
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.transport import (
    AttachHandle,
    TransportAdapter,
    WorkerLifecycleSnapshot,
)


class _AliveControllableTransport(TransportAdapter):
    """Transport where is_alive is controllable per role."""

    def __init__(self):
        self.alive_flags: dict[str, bool] = {}
        self.spawn_calls: list[str] = []
        self.terminate_calls: list[str] = []

    def init(self): pass
    def is_session_running(self): return True

    def spawn(self, role, argv, *, cwd=None):
        self.spawn_calls.append(role.instance_id)
        self.alive_flags[role.instance_id] = True

    def is_alive(self, role_name):
        return self.alive_flags.get(role_name, True)

    def lifecycle_snapshot(self, role_name):
        return WorkerLifecycleSnapshot(
            role_name=role_name,
            alive=self.is_alive(role_name),
            pane_pid=f"pid-{role_name}",
            current_command="codex",
            current_path="/tmp/project",
        )

    def wait_ready(self, role_name, pattern, timeout): return True

    def send_task(self, role_name, briefing_path, prompt): pass

    def capture_log(self, role_name, lines=200):
        return "some output"

    def poll_events(self): return []
    def attach_handle(self, role_name): return AttachHandle()

    def terminate(self, role_name):
        self.terminate_calls.append(role_name)
        self.alive_flags[role_name] = False

    def shutdown(self): pass


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    # 2026-06-11-0325 (I41 gate): the dead-pane watchdog only recovers
    # workers with pending obligations; an idle dead pane is evidence-only
    # (the R18 completed-worker falsepos fix). These tests exercise the
    # recovery MACHINERY, so seed an active task per worker.
    store = TaskStore(sd / "kanban.json")
    store.add(Task(
        id="TASK-LIVENESS-DEV", title="busy dev",
        status="in_progress", assigned_to="dev",
        active_dispatch_id="disp-dev",
    ))
    store.add(Task(
        id="TASK-LIVENESS-REVIEW", title="busy review",
        status="in_progress", assigned_to="review",
        active_dispatch_id="disp-review",
    ))
    return sd


@pytest.fixture
def config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock"),
        ],
    )


class TestWatchdogThreshold:
    def test_single_is_alive_false_does_not_respawn(self, state_dir, config):
        transport = _AliveControllableTransport()
        transport.spawn(RoleConfig(name="dev"), argv=[])  # seed the alive state
        transport.spawn(RoleConfig(name="review"), argv=[])
        orch = Orchestrator(state_dir, config, transport)
        transport.alive_flags["dev"] = False
        orch.run_once()  # one failure
        assert "dev" not in transport.spawn_calls[1:]  # initial seed doesn't count

    def test_three_consecutive_failures_trigger_respawn(
        self, state_dir, config
    ):
        transport = _AliveControllableTransport()
        transport.spawn(RoleConfig(name="dev"), argv=[])
        transport.spawn(RoleConfig(name="review"), argv=[])
        orch = Orchestrator(state_dir, config, transport)
        transport.alive_flags["dev"] = False

        orch.run_once()  # strike 1
        orch.run_once()  # strike 2
        orch.run_once()  # strike 3 → respawn

        # After respawn, dev should be spawned again
        assert transport.spawn_calls.count("dev") >= 2  # initial + respawn

    def test_alive_recovers_resets_counter(self, state_dir, config):
        """If is_alive flips back to True mid-counting, the counter
        resets and no respawn fires."""
        transport = _AliveControllableTransport()
        transport.spawn(RoleConfig(name="dev"), argv=[])
        transport.spawn(RoleConfig(name="review"), argv=[])
        orch = Orchestrator(state_dir, config, transport)
        transport.alive_flags["dev"] = False

        orch.run_once()  # strike 1
        orch.run_once()  # strike 2
        transport.alive_flags["dev"] = True
        orch.run_once()  # alive again; counter reset
        transport.alive_flags["dev"] = False
        orch.run_once()  # strike 1 (after reset)
        orch.run_once()  # strike 2 — still no respawn

        # Only the initial spawn counts for dev
        assert transport.spawn_calls.count("dev") == 1


class TestWatchdogEvents:
    def test_dead_watchdog_emits_runner_failed_with_lifecycle(
        self,
        state_dir,
        config,
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.update("TASK-LIVENESS-DEV", status="done")  # fixture seed out of the way
        store.add(Task(
            id="TASK-RUNNER",
            title="runner crash",
            status="in_progress",
            assigned_to="dev",
            active_dispatch_id="disp-runner",
        ))
        transport = _AliveControllableTransport()
        transport.spawn(RoleConfig(name="dev"), argv=[])
        transport.spawn(RoleConfig(name="review"), argv=[])
        orch = Orchestrator(state_dir, config, transport)
        orch._dead_threshold = 1
        transport.alive_flags["dev"] = False

        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        runner_failed = [
            e for e in events
            if e.type == "worker.runner.failed" and e.actor == "dev"
        ]
        assert runner_failed
        event = runner_failed[-1]
        assert event.task_id == "TASK-RUNNER"
        assert event.payload["role"] == "dev"
        assert event.payload["instance_id"] == "dev"
        assert event.payload["backend"] == "mock"
        assert event.payload["dispatch_id"] == "disp-runner"
        assert event.payload["source"] == "dead_watchdog"
        assert event.payload["dead_threshold"] == 1
        assert event.payload["lifecycle"] == {
            "role_name": "dev",
            "alive": False,
            "pane_pid": "pid-dev",
            "current_command": "codex",
            "current_path": "/tmp/project",
            "process_probe": {},
        }
        assert any(e.type == "worker.respawned" for e in events)

    def test_respawn_emits_worker_respawned_event(self, state_dir, config):
        transport = _AliveControllableTransport()
        transport.spawn(RoleConfig(name="dev"), argv=[])
        transport.spawn(RoleConfig(name="review"), argv=[])
        orch = Orchestrator(state_dir, config, transport)
        transport.alive_flags["dev"] = False

        for _ in range(3):
            orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.respawned" in types
        actors = [e.actor for e in events if e.type == "worker.respawned"]
        assert "dev" in actors

    def test_repeated_successful_respawns_open_circuit(
        self,
        state_dir,
        config,
    ):
        transport = _AliveControllableTransport()
        transport.spawn(RoleConfig(name="dev"), argv=[])
        transport.spawn(RoleConfig(name="review"), argv=[])
        orch = Orchestrator(state_dir, config, transport)

        for _ in range(3):
            transport.alive_flags["dev"] = False
            for _ in range(3):
                orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        circuit = [
            e for e in events
            if e.type == "worker.respawn.circuit_opened"
            and e.actor == "dev"
        ]
        assert len(circuit) == 1
        assert circuit[0].payload["successes_in_window"] == 3
        assert [
            e.type for e in events
            if e.type == "autoresearch.invocation.requested"
        ]
        assert any(
            e.type == "worker.state.changed"
            and e.actor == "dev"
            and e.payload.get("to") == "blocked_human"
            for e in events
        )

        before = transport.spawn_calls.count("dev")
        transport.alive_flags["dev"] = False
        for _ in range(3):
            orch.run_once()
        assert transport.spawn_calls.count("dev") == before

    def test_manifest_pending_terminal_blocks_dead_respawn_loop(
        self,
        state_dir,
    ):
        config = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(
                    name="arch",
                    backend="mock",
                    instance_id="arch",
                    stuck_threshold_seconds=0.0,
                    publishes=[
                        "artifact.manifest.published",
                        "arch.proposal.done",
                    ],
                ),
            ],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="TASK-PLAN",
            title="plan",
            status="in_progress",
            assigned_to="arch",
            active_dispatch_id="disp-plan",
        ))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="TASK-PLAN",
            payload={
                "role": "arch",
                "assignee": "arch",
                "dispatch_id": "disp-plan",
            },
        ))
        log.append(ZfEvent(
            type="artifact.manifest.published",
            actor="arch",
            task_id="TASK-PLAN",
            payload={
                "manifest": {
                    "task_id": "TASK-PLAN",
                    "role": "arch",
                    "artifact_refs": [
                        {
                            "kind": "spec",
                            "path": "docs/specs/demo.md",
                            "sha256": "a" * 64,
                            "summary": "demo spec",
                        }
                    ],
                }
            },
        ))
        transport = _AliveControllableTransport()
        transport.spawn(RoleConfig(name="arch", instance_id="arch"), argv=[])
        orch = Orchestrator(state_dir, config, transport)
        transport.alive_flags["arch"] = False

        for _ in range(3):
            orch.run_once()

        assert transport.spawn_calls.count("arch") == 1
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "worker.respawned" for e in events)
        recovered = [e for e in events if e.type == "worker.stuck.recovered"]
        assert recovered
        assert recovered[-1].payload["recovery_action"] == "terminal_completion_requested"
        assert recovered[-1].payload["prompt_injected"] is False


class TestI41LivenessGate:
    """2026-06-11-0325: probe 只预筛,决策条件是 kernel 态新鲜度。"""

    def test_dead_probe_with_fresh_worker_heartbeat_does_not_respawn(
        self, state_dir, config,
    ):
        """验收(1):is_alive=False 但 worker 心跳新鲜 → 只留 evidence,
        不 respawn(busy worker 的 probe 抖动不再触发 R18 类围殴)。"""
        from zf.core.state.role_sessions import RoleSessionRegistry

        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        registry.get_or_create("dev")
        registry.record_heartbeat("dev", {
            "instance_id": "dev",
            "state": "busy",
            "current_task_id": "TASK-LIVENESS-DEV",
            "last_action_ts": "now",
        })  # worker 自报心跳(无 source 字段)= 活性证明,刚刚发生

        transport = _AliveControllableTransport()
        transport.spawn(RoleConfig(name="dev"), argv=[])
        transport.spawn(RoleConfig(name="review"), argv=[])
        orch = Orchestrator(state_dir, config, transport)
        orch._dead_threshold = 1
        transport.alive_flags["dev"] = False

        for _ in range(3):
            orch.run_once()

        assert transport.spawn_calls.count("dev") == 1  # 仅初始 spawn
        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not [e for e in events if e.type == "worker.respawned"]
        observed = [
            e for e in events if e.type == "worker.pane.dead_observed"
        ]
        assert len(observed) == 1  # evidence 仍有,且 per 死亡期去重

    def test_kernel_state_mirror_does_not_mask_dead_pane(
        self, state_dir, config,
    ):
        """worker.state.changed 镜像戳是簿记不是活性证明:有任务、无 worker
        自证活性 → 视为 stale → 恢复照常。"""
        from zf.core.state.role_sessions import RoleSessionRegistry
        from zf.runtime.housekeeping import apply_worker_state_changed_event

        registry = RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(state_dir.parent),
        )
        registry.get_or_create("dev")
        apply_worker_state_changed_event(registry, ZfEvent(
            type="worker.state.changed",
            actor="dev",
            payload={"instance_id": "dev", "to": "idle"},
        ))

        transport = _AliveControllableTransport()
        transport.spawn(RoleConfig(name="dev"), argv=[])
        transport.spawn(RoleConfig(name="review"), argv=[])
        orch = Orchestrator(state_dir, config, transport)
        orch._dead_threshold = 1
        transport.alive_flags["dev"] = False

        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert any(e.type == "worker.respawned" for e in events)
