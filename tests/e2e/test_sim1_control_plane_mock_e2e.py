"""Sim1-shaped deterministic control-plane E2E with scripted providers."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    FanoutAssignmentConfig,
    GitIsolationConfig,
    GoalConfig,
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    WorkflowAffinityLaneConfig,
    WorkflowAffinityLaneProfileConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.goal_dossier import (
    build_goal_dossier,
    write_goal_dossier_projection,
)
from zf.runtime.workflow_spine_projection import refresh_spine_projections


TASKS = [
    ("TASK-SCAFFOLD", [], "app/scaffold.txt", "scaffold"),
    ("TASK-SIM-CORE", ["TASK-SCAFFOLD"], "app/core.txt", "sim-core"),
    ("TASK-RENDER", ["TASK-SCAFFOLD"], "app/render.txt", "render"),
    ("TASK-UI", ["TASK-SCAFFOLD"], "app/ui.txt", "ui"),
    ("TASK-SIM-SCHED", ["TASK-SIM-CORE"], "app/scheduler.txt", "sim-core"),
    ("TASK-STATS", ["TASK-SIM-CORE"], "app/stats.txt", "sim-core"),
    ("TASK-BATCH", ["TASK-SIM-SCHED", "TASK-STATS"], "app/batch.txt", "batch"),
    (
        "TASK-ASSEMBLY",
        [
            "TASK-SCAFFOLD", "TASK-SIM-CORE", "TASK-RENDER", "TASK-UI",
            "TASK-SIM-SCHED", "TASK-STATS", "TASK-BATCH",
        ],
        "app/assembly.txt",
        "assembly",
    ),
]


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Path, str, object]] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _config(state_dir: Path) -> ZfConfig:
    lanes = WorkflowAffinityLaneProfileConfig(
        affinity_key="affinity_tag",
        lanes=[
            WorkflowAffinityLaneConfig(id="lane0", impl="dev-lane-0"),
            WorkflowAffinityLaneConfig(id="lane1", impl="dev-lane-1"),
        ],
    )
    return ZfConfig(
        project=ProjectConfig(name="sim1-mock", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="dev",
                instance_id=f"dev-lane-{index}",
                backend="mock",
                role_kind="writer",
                publishes=["dev.build.done"],
            )
            for index in range(2)
        ] + [RoleConfig(
            name="judge",
            instance_id="judge-prd",
            backend="mock",
            role_kind="reader",
        )],
        workflow=WorkflowConfig(
            stages=[
                WorkflowStageConfig(
                    id="prd-lanes-impl",
                    trigger="task_map.ready",
                    topology="fanout_writer_scoped",
                    synthesize_canonical_tasks=True,
                    task_map="${task_map_ref}",
                    assignment=FanoutAssignmentConfig(
                        strategy="affinity_stage_slots",
                        lane_profile="sim1-2",
                        stage_slot="impl",
                    ),
                    aggregate=FanoutAggregateConfig(
                        mode="candidate_integration",
                        success_event="candidate.ready",
                        failure_event="integration.failed",
                    ),
                ),
                WorkflowStageConfig(
                    id="prd-final-judge",
                    trigger="candidate.ready",
                    topology="fanout_reader",
                    roles=["judge-prd"],
                    target_ref="${candidate_ref}",
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        child_success_event="judge.child.completed",
                        child_failure_event="judge.child.failed",
                        success_event="judge.passed",
                        failure_event="judge.failed",
                    ),
                ),
            ],
            affinity_lanes={"sim1-2": lanes},
        ),
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(candidate_base_ref="main"),
        ),
        goal=GoalConfig(enabled=True),
    )


def _manifest(state_dir: Path, fanout_id: str) -> dict:
    return json.loads(
        (state_dir / "fanouts" / fanout_id / "manifest.json").read_text(
            encoding="utf-8",
        )
    )


def test_sim1_mock_reaches_unique_terminal_without_duplicate_attempts(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "sim1-mock@example.com")
    _git(tmp_path, "config", "user.name", "Sim1 Mock")
    (tmp_path / "README.md").write_text("sim1 mock\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".zf-sim1-mock/\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _git(tmp_path, "branch", "-M", "main")
    baseline = _git(tmp_path, "rev-parse", "HEAD")
    state_dir = tmp_path / ".zf-sim1-mock"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task_map_path = state_dir / "artifacts" / "SIM1-MOCK" / "task_map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(json.dumps({
        "target_commit": baseline,
        "tasks": [
            {
                "task_id": task_id,
                "scope": task_id,
                "blocked_by": blocked_by,
                "allowed_paths": [path],
                "affinity_tag": affinity,
            }
            for task_id, blocked_by, path, affinity in TASKS
        ],
    }), encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _Transport()
    orch = Orchestrator(
        state_dir,
        _config(state_dir),
        transport,  # type: ignore[arg-type]
    )
    orch._typed_task_contract_handoff_enabled = lambda _payload: True  # type: ignore[method-assign]
    goal_started = ZfEvent(
        type="run.goal.started",
        actor="orchestrator",
        correlation_id="trace-sim1-mock",
        payload={
            "run_id": "trace-sim1-mock",
            "goal_id": "SIM1-MOCK",
            "objective": "deliver the Sim1 mock product",
        },
    )
    log.append(goal_started)
    trigger = ZfEvent(
        type="task_map.ready",
        actor="orchestrator",
        correlation_id="trace-sim1-mock",
        payload={
            "pdd_id": "SIM1-MOCK",
            "feature_id": "SIM1-MOCK",
            "task_map_ref": str(task_map_path),
        },
    )
    log.append(trigger)
    orch.run_once(events=[trigger])
    impl_started = next(
        event for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "prd-lanes-impl"
    )
    fanout_id = str(impl_started.payload["fanout_id"])
    completed: set[str] = set()
    retried_busy = False

    for _round in range(12):
        manifest = _manifest(state_dir, fanout_id)
        ready = [
            child for child in manifest["children"]
            if child.get("status") == "dispatched"
            and child.get("task_id") not in completed
        ]
        if not ready:
            orch.run_once(events=[])
            ready = [
                child for child in _manifest(state_dir, fanout_id)["children"]
                if child.get("status") == "dispatched"
                and child.get("task_id") not in completed
            ]
        for child in ready:
            task_id = str(child["task_id"])
            workdir = Path(child["workdir"])
            child_payload = (
                child.get("payload")
                if isinstance(child.get("payload"), dict)
                else {}
            )
            snapshot_ref = str(
                child.get("contract_snapshot_ref")
                or child_payload.get("contract_snapshot_ref")
                or ""
            )
            assert snapshot_ref
            snapshot = json.loads(
                (state_dir / snapshot_ref).read_text(encoding="utf-8")
            )
            assert snapshot["base_commit"] == _git(workdir, "rev-parse", "HEAD")
            task_spec = next(item for item in TASKS if item[0] == task_id)
            for dependency_id in task_spec[1]:
                dependency_path = next(item[2] for item in TASKS if item[0] == dependency_id)
                assert (workdir / dependency_path).is_file(), (
                    f"{task_id} missing dependency ancestry from {dependency_id}"
                )
            if task_id == "TASK-SIM-SCHED" and not retried_busy:
                previous_dispatch = next(
                    event for event in reversed(log.read_all())
                    if event.type == "fanout.child.dispatched"
                    and event.payload.get("task_id") == task_id
                )
                sent_before = len(transport.sent)
                orch._retry_fanout_child(  # type: ignore[attr-defined]
                    manifest=manifest,
                    child=child,
                    previous_dispatch=previous_dispatch,
                    attempt=1,
                )
                assert len(transport.sent) == sent_before
                retried_busy = True
                log.append(ZfEvent(
                    type="agent.usage",
                    actor=child["role_instance"],
                    payload={"output_tokens": 19775},
                ))
            target = workdir / task_spec[2]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{task_id}\n", encoding="utf-8")
            _git(workdir, "add", task_spec[2])
            _git(workdir, "commit", "-q", "-m", f"feat: {task_id}")
            done = ZfEvent(
                type="dev.build.done",
                actor=child["role_instance"],
                task_id=task_id,
                correlation_id="trace-sim1-mock",
                payload={
                    "fanout_id": fanout_id,
                    "child_id": child["child_id"],
                    "run_id": child["run_id"],
                    "pdd_id": "SIM1-MOCK",
                    "source_commit": _git(workdir, "rev-parse", "HEAD"),
                    "source_branch": child["source_branch"],
                    "workdir": str(workdir),
                },
            )
            log.append(done)
            orch.run_once(events=[done])
            completed.add(task_id)
        orch.run_once(events=[])
        if len(completed) == len(TASKS):
            break

    assert completed == {item[0] for item in TASKS}
    candidate = next(
        event for event in reversed(log.read_all())
        if event.type == "candidate.ready"
    )
    orch.run_once(events=[candidate])
    judge_dispatch = next(
        event for event in reversed(log.read_all())
        if event.type == "fanout.child.dispatched"
        and event.payload.get("role_instance") == "judge-prd"
    )
    judged = ZfEvent(
        type="judge.child.completed",
        actor="judge-prd",
        correlation_id="trace-sim1-mock",
        payload={
            "fanout_id": judge_dispatch.payload["fanout_id"],
            "child_id": judge_dispatch.payload["child_id"],
            "run_id": judge_dispatch.payload["run_id"],
            "status": "completed",
            "evidence_refs": ["mock://sim1/judge"],
        },
    )
    log.append(judged)
    orch.run_once(events=[judged])
    judge_passed = next(
        event for event in reversed(log.read_all())
        if event.type == "judge.passed"
    )
    orch.run_once(events=[judge_passed])
    log.append(ZfEvent(
        type="simulation.done",
        actor="test-driver",
        payload={"status": "passed", "mode": "mock"},
    ))
    refresh_spine_projections(state_dir, log)

    events = log.read_all()
    canonical_events = (state_dir / "events.jsonl").read_bytes()
    canonical_tasks = (state_dir / "kanban.json").read_bytes()
    dossier = build_goal_dossier(state_dir, "trace-sim1-mock")
    projection = write_goal_dossier_projection(state_dir, dossier)
    projection.unlink()
    rebuilt = build_goal_dossier(state_dir, "trace-sim1-mock")
    attempts = json.loads(
        (state_dir / "projections" / "task_attempts.json").read_text(
            encoding="utf-8",
        )
    )["tasks"]
    assert len([event for event in events if event.type == "judge.passed"]) == 1
    assert len([event for event in events if event.type == "run.goal.completed"]) == 1
    assert not [event for event in events if event.type == "judge.failed"]
    assert not [event for event in events if event.type == "dispatch.silent_stall"]
    assert not [event for event in events if event.type == "worker.respawn.requested"]
    assert all(entry["open_attempts"] == 0 for entry in attempts.values())
    assert max(entry["open_attempts"] for entry in attempts.values()) == 0
    assert retried_busy is True
    assert TaskStore(state_dir / "kanban.json").get("TASK-ASSEMBLY") is not None
    assert dossier["goal"]["status"] == "complete"
    assert dossier["goal"]["completion_gate_status"] == "passed"
    assert dossier["closure"]["status"] == "goal_completed"
    assert dossier["closure"]["completion_event_id"]
    assert dossier["state"]["task_counts"] == {
        "total": len(TASKS),
        "terminal": len(TASKS),
        "open": 0,
    }
    assert dossier["state"]["handoff"]["open_feedback_count"] == 0
    assert dossier["state"]["handoff"]["pending_handoff_count"] == 0
    assert dossier["source_fingerprint"] == rebuilt["source_fingerprint"]
    assert dossier["source_manifest"] == rebuilt["source_manifest"]
    assert (state_dir / "events.jsonl").read_bytes() == canonical_events
    assert (state_dir / "kanban.json").read_bytes() == canonical_tasks
    assert _git(tmp_path, "rev-parse", "HEAD") == baseline
    assert _git(tmp_path, "status", "--porcelain") == ""
