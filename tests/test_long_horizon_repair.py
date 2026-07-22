from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    ContractDConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    VerificationConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_types import OrchestratorDecision


class _Transport:
    def __init__(self) -> None:
        self.sends: list[tuple[str, Path, str]] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sends.append((role_name, briefing_path, prompt))
        return None

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return "unchanged output"

    def pane_current_command(self, role_name):  # noqa: ANN001
        return "node"


class _AlwaysStuckDetector:
    def __init__(self) -> None:
        self.reset_count = 0

    def update(self, output: str) -> None:
        return None

    def is_stuck(self) -> bool:
        return True

    def reset(self) -> None:
        self.reset_count += 1


def _make_orchestrator(tmp_path: Path) -> tuple[Orchestrator, TaskStore, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(name="test", backend="mock", instance_id="test-1"),
            RoleConfig(name="test", backend="mock", instance_id="test-2"),
        ],
    )
    return (
        Orchestrator(state_dir, config, _Transport()),  # type: ignore[arg-type]
        TaskStore(state_dir / "kanban.json"),
        EventLog(state_dir / "events.jsonl"),
    )


def _make_arch_orchestrator(
    tmp_path: Path,
) -> tuple[Orchestrator, TaskStore, EventLog, _Transport]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    transport = _Transport()
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(
                name="arch",
                backend="mock",
                instance_id="arch",
                publishes=[
                    "artifact.manifest.published",
                    "arch.proposal.done",
                    "clarification.needed",
                ],
            ),
        ],
    )
    return (
        Orchestrator(state_dir, config, transport),  # type: ignore[arg-type]
        TaskStore(state_dir / "kanban.json"),
        EventLog(state_dir / "events.jsonl"),
        transport,
    )


def _manifest_payload(task_id: str, role: str = "arch") -> dict:
    return {
        "manifest": {
            "task_id": task_id,
            "role": role,
            "artifact_refs": [
                {
                    "kind": "spec",
                    "path": "docs/specs/demo.md",
                    "sha256": "a" * 64,
                    "summary": "demo spec",
                }
            ],
        }
    }


def test_stuck_worker_requeues_task_and_records_recovery(tmp_path: Path) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-STUCK",
        title="stuck task",
        status="in_progress",
        assigned_to="test-1",
        active_dispatch_id="disp-1",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-STUCK",
        payload={
            "role": "test",
            "assignee": "test-1",
            "dispatch_id": "disp-1",
            "briefing": ".zf/briefings/test-1-TASK-STUCK.md",
        },
    ))

    def fake_respawn(role):  # noqa: ANN001
        return OrchestratorDecision(
            action="respawn",
            role=role.instance_id,
            reason="test respawn",
        )

    orch._respawn_instance = fake_respawn  # type: ignore[method-assign]

    decision = orch._report_stuck_worker(  # type: ignore[attr-defined]
        orch._find_role_by_instance("test-1"),  # type: ignore[arg-type]
    )

    task = store.get("TASK-STUCK")
    assert decision.action == "recover"
    assert task is not None
    assert task.status == "backlog"
    assert task.assigned_to == "test-1"
    assert task.active_dispatch_id == ""
    events = log.read_all()
    assert any(e.type == "worker.stuck" for e in events)
    assert any(e.type == "task.requeued" for e in events)
    assert any(
        e.type == "task.assigned"
        and e.payload.get("source") == "worker_stuck_recovery"
        and e.payload.get("assignee") == "test-1"
        for e in events
    )
    recovered = [e for e in events if e.type == "worker.stuck.recovered"]
    assert len(recovered) == 1
    assert recovered[0].payload["task_id"] == "TASK-STUCK"
    assert orch._last_worker_state["test-1"] == "idle"


def test_stuck_worker_does_not_requeue_after_recorded_progress(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PROGRESSED",
        title="progress already landed",
        status="in_progress",
        assigned_to="test-1",
        active_dispatch_id="disp-progress",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-PROGRESSED",
        payload={
            "role": "test",
            "assignee": "test-1",
            "dispatch_id": "disp-progress",
        },
    ))
    progress = ZfEvent(
        type="test.passed",
        actor="test-1",
        task_id="TASK-PROGRESSED",
        payload={"dispatch_id": "disp-progress"},
    )
    log.append(progress)

    decision = orch._report_stuck_worker(  # type: ignore[attr-defined]
        orch._find_role_by_instance("test-1"),  # type: ignore[arg-type]
    )

    task = store.get("TASK-PROGRESSED")
    assert decision.action == "recover"
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "test-1"
    assert task.active_dispatch_id == "disp-progress"
    events = log.read_all()
    assert any(e.type == "worker.stuck" for e in events)
    assert not any(e.type == "task.requeued" for e in events)
    recovered = [e for e in events if e.type == "worker.stuck.recovered"]
    assert len(recovered) == 1
    assert recovered[0].payload["recovery_action"] == "progress_already_recorded"
    assert recovered[0].payload["progress_event_id"] == progress.id
    assert orch._last_worker_state["test-1"] == "idle"


def test_stuck_worker_does_not_requeue_after_dev_blocked_progress(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-BLOCKED",
        title="blocked after implementation",
        status="in_progress",
        assigned_to="test-1",
        active_dispatch_id="disp-blocked",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-BLOCKED",
        payload={
            "role": "test",
            "assignee": "test-1",
            "dispatch_id": "disp-blocked",
        },
    ))
    progress = ZfEvent(
        type="dev.blocked",
        actor="test-1",
        task_id="TASK-BLOCKED",
        payload={"dispatch_id": "disp-blocked", "status": "BLOCKED_AFTER_IMPLEMENTATION"},
    )
    log.append(progress)

    decision = orch._report_stuck_worker(  # type: ignore[attr-defined]
        orch._find_role_by_instance("test-1"),  # type: ignore[arg-type]
    )

    task = store.get("TASK-BLOCKED")
    assert decision.action == "recover"
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "test-1"
    assert task.active_dispatch_id == "disp-blocked"
    events = log.read_all()
    assert any(e.type == "worker.stuck" for e in events)
    assert not any(e.type == "task.requeued" for e in events)
    recovered = [e for e in events if e.type == "worker.stuck.recovered"]
    assert len(recovered) == 1
    assert recovered[0].payload["recovery_action"] == "progress_already_recorded"
    assert recovered[0].payload["progress_event_id"] == progress.id
    assert orch._last_worker_state["test-1"] == "blocked_human"


def test_stuck_worker_requests_terminal_after_manifest_without_requeue(
    tmp_path: Path,
) -> None:
    orch, store, log, transport = _make_arch_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-PLAN",
        title="plan task",
        status="in_progress",
        assigned_to="arch",
        active_dispatch_id="disp-plan",
    ))
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
    manifest = ZfEvent(
        type="artifact.manifest.published",
        actor="arch",
        task_id="TASK-PLAN",
        payload=_manifest_payload("TASK-PLAN"),
    )
    log.append(manifest)

    decision = orch._report_stuck_worker(  # type: ignore[attr-defined]
        orch._find_role_by_instance("arch"),  # type: ignore[arg-type]
    )

    task = store.get("TASK-PLAN")
    assert decision.action == "recover"
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "arch"
    assert task.active_dispatch_id == "disp-plan"
    events = log.read_all()
    assert not any(e.type == "task.requeued" for e in events)
    recovered = [e for e in events if e.type == "worker.stuck.recovered"]
    assert len(recovered) == 1
    assert recovered[0].payload["recovery_action"] == "terminal_completion_requested"
    assert recovered[0].payload["expected_event"] == "arch.proposal.done"
    assert orch._last_worker_state["arch"] == "completion_pending"
    assert transport.sends
    briefing_path = transport.sends[-1][1]
    briefing = briefing_path.read_text(encoding="utf-8")
    assert "Do not rewrite, regenerate, or replace" in briefing
    assert "zf guard ownership --task TASK-PLAN --actor arch" in briefing
    assert "arch.proposal.done" in briefing
    assert manifest.id in briefing


def test_capture_logs_skips_stuck_detector_after_dev_blocked_progress(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-BLOCKED-CAPTURE",
        title="blocked before watchdog",
        status="in_progress",
        assigned_to="test-1",
        active_dispatch_id="disp-capture",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-BLOCKED-CAPTURE",
        payload={
            "role": "test",
            "assignee": "test-1",
            "dispatch_id": "disp-capture",
        },
    ))
    log.append(ZfEvent(
        type="dev.blocked",
        actor="test-1",
        task_id="TASK-BLOCKED-CAPTURE",
        payload={"dispatch_id": "disp-capture"},
    ))
    detector = _AlwaysStuckDetector()
    orch._stuck_detectors["test-1"] = detector  # type: ignore[assignment]

    decisions = orch._capture_logs()  # type: ignore[attr-defined]

    assert not decisions
    assert detector.reset_count == 1
    assert not any(e.type == "worker.stuck" for e in log.read_all())
    assert orch._last_worker_state["test-1"] == "blocked_human"


def test_capture_logs_skips_terminal_fanout_worker_with_stale_wip_dispatch(
    tmp_path: Path,
) -> None:
    """A completed fanout child has no active obligation despite stale WIP."""
    orch, store, log = _make_orchestrator(tmp_path)
    task_id = "TASK-FANOUT-DONE-CAPTURE"
    fanout_id = "fanout-impl"
    child_id = f"test-1-{task_id}"
    run_id = f"run-{child_id}"
    store.add(Task(
        id=task_id,
        title="completed writer awaiting verification",
        status="in_progress",
        assigned_to="test-1",
        active_dispatch_id="disp-capture",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id=task_id,
        payload={
            "role": "test",
            "assignee": "test-1",
            "dispatch_id": "disp-capture",
        },
    ))
    child_payload = {
        "fanout_id": fanout_id,
        "child_id": child_id,
        "run_id": run_id,
        "role_instance": "test-1",
        "task_id": task_id,
    }
    log.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload=child_payload,
    ))
    log.append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        payload={**child_payload, "status": "completed"},
    ))
    detector = _AlwaysStuckDetector()
    orch._stuck_detectors["test-1"] = detector  # type: ignore[assignment]

    decisions = orch._capture_logs()  # type: ignore[attr-defined]

    assert not decisions
    assert detector.reset_count == 1
    assert not any(event.type == "worker.stuck" for event in log.read_all())


def test_capture_logs_suppresses_codex_active_turn_stuck_grace(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(
                name="dev",
                backend="codex",
                instance_id="dev",
                stuck_threshold_seconds=1.0,
            ),
        ],
    )
    orch = Orchestrator(state_dir, config, _Transport())  # type: ignore[arg-type]
    store = TaskStore(state_dir / "kanban.json")
    log = EventLog(state_dir / "events.jsonl")
    store.add(Task(
        id="TASK-CODEX-LONG-TURN",
        title="codex long turn",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-codex",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-CODEX-LONG-TURN",
        payload={
            "role": "dev",
            "assignee": "dev",
            "dispatch_id": "disp-codex",
        },
    ))
    detector = _AlwaysStuckDetector()
    orch._stuck_detectors["dev"] = detector  # type: ignore[assignment]

    decisions = orch._capture_logs()  # type: ignore[attr-defined]

    assert not decisions
    assert detector.reset_count == 0
    assert not any(event.type == "worker.stuck" for event in log.read_all())
    assert orch._last_worker_state["dev"] == "busy"


def test_active_task_ignores_dispatch_superseded_by_assignment(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-ROUTED",
        title="routed to another role",
        status="in_progress",
        assigned_to="test-2",
        active_dispatch_id="disp-old",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-ROUTED",
        payload={
            "role": "test",
            "assignee": "test-1",
            "dispatch_id": "disp-old",
        },
    ))
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="TASK-ROUTED",
        payload={
            "role": "test",
            "assignee": "test-2",
            "source": "route_to_review",
        },
    ))

    assert orch._latest_dispatched_per_task().get("TASK-ROUTED") is None
    assert orch._active_task_for_instance("test-1") is None
    assert orch._active_task_for_instance("test-2") is not None


def test_autoresearch_stuck_injection_uses_recovery_path(tmp_path: Path) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-INJECT",
        title="inject task",
        status="in_progress",
        assigned_to="test-1",
        active_dispatch_id="disp-inject",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-INJECT",
        payload={
            "role": "test",
            "assignee": "test-1",
            "dispatch_id": "disp-inject",
            "briefing": ".zf/briefings/test-1-TASK-INJECT.md",
        },
    ))

    def fake_respawn(role):  # noqa: ANN001
        return OrchestratorDecision(
            action="respawn",
            role=role.instance_id,
            reason="test respawn",
        )

    orch._respawn_instance = fake_respawn  # type: ignore[method-assign]
    injection = ZfEvent(
        type="autoresearch.inject.worker_stuck",
        actor="zf-autoresearch",
        task_id="TASK-INJECT",
        payload={
            "source": "autoresearch",
            "instance_id": "test-1",
            "dispatch_id": "disp-inject",
        },
    )

    decisions = orch.run_once([injection])

    task = store.get("TASK-INJECT")
    assert any(decision.action == "recover" for decision in decisions)
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "test-1"
    assert task.active_dispatch_id
    assert task.active_dispatch_id != "disp-inject"
    events = log.read_all()
    assert any(e.type == "worker.stuck" for e in events)
    assert any(e.type == "task.requeued" for e in events)
    assert any(e.type == "worker.stuck.recovered" for e in events)


def test_pending_handoff_ignores_success_from_requeued_dispatch(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        verification=VerificationConfig(
            contract=ContractDConfig(dispatch_token_required=True),
        ),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(
                name="dev",
                backend="mock",
                instance_id="dev-1",
                publishes=["dev.build.done"],
            ),
            RoleConfig(
                name="critic",
                backend="mock",
                publishes=["design.critique.done"],
            ),
            RoleConfig(
                name="review",
                backend="mock",
                triggers=["dev.build.done"],
            ),
        ],
    )
    orch = Orchestrator(state_dir, config, _Transport())  # type: ignore[arg-type]
    store = TaskStore(state_dir / "kanban.json")
    log = EventLog(state_dir / "events.jsonl")
    store.add(Task(
        id="TASK-STALE",
        title="stale success",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-STALE",
        payload={
            "role": "dev",
            "assignee": "dev-1",
            "dispatch_id": "disp-old",
        },
    ))
    log.append(ZfEvent(
        type="task.requeued",
        actor="zf-cli",
        task_id="TASK-STALE",
        payload={"source": "worker_stuck_recovery", "dispatch_id": "disp-old"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-STALE",
        payload={
            "role": "critic",
            "assignee": "critic",
            "dispatch_id": "disp-critic",
        },
    ))
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-STALE",
        payload={"dispatch_id": "disp-old"},
    ))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    task = store.get("TASK-STALE")
    assert decisions == []
    assert task is not None
    assert task.assigned_to == "critic"


def test_dispatch_skips_stuck_replica(tmp_path: Path) -> None:
    orch, store, _log = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-NEXT",
        title="next task",
        status="backlog",
        assigned_to="test",
    ))
    orch._last_worker_state["test-1"] = "stuck"

    role = orch._find_available_role(store.get("TASK-NEXT"))  # type: ignore[arg-type]

    assert role is not None
    assert role.instance_id == "test-2"


def test_terminal_done_settles_task_chain_workers_idle(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(name="dev", backend="mock", instance_id="dev-1"),
            RoleConfig(name="review", backend="mock"),
            RoleConfig(name="test", backend="mock", instance_id="test-1"),
            RoleConfig(name="judge", backend="mock"),
        ],
    )
    orch = Orchestrator(state_dir, config, _Transport())  # type: ignore[arg-type]
    log = EventLog(state_dir / "events.jsonl")
    for instance_id, role in (
        ("dev-1", "dev"),
        ("review", "review"),
        ("test-1", "test"),
        ("judge", "judge"),
    ):
        log.append(ZfEvent(
            type="task.dispatched",
            actor="orchestrator",
            task_id="TASK-DONE",
            payload={
                "role": role,
                "assignee": instance_id,
                "dispatch_id": f"disp-{instance_id}",
            },
        ))
        orch._last_worker_state[instance_id] = "busy"

    orch._settle_task_chain_workers_idle(  # type: ignore[attr-defined]
        "TASK-DONE",
        reason="terminal done",
    )

    assert orch._last_worker_state["dev-1"] == "idle"
    assert orch._last_worker_state["review"] == "idle"
    assert orch._last_worker_state["test-1"] == "idle"
    assert orch._last_worker_state["judge"] == "idle"


def test_terminal_done_does_not_idle_worker_with_other_active_task(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-OTHER",
        title="other active task",
        status="in_progress",
        assigned_to="test-1",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-DONE",
        payload={
            "role": "test",
            "assignee": "test-1",
            "dispatch_id": "disp-test",
        },
    ))
    orch._last_worker_state["test-1"] = "busy"

    orch._settle_task_chain_workers_idle(  # type: ignore[attr-defined]
        "TASK-DONE",
        reason="terminal done",
    )

    assert orch._last_worker_state["test-1"] == "busy"
