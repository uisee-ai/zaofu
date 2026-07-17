from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from zf.core.config.schema import (
    FanoutAggregateConfig,
    FanoutAssignmentConfig,
    GitIsolationConfig,
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
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.workflow.lane_pipeline import parse_lane_pipeline
from zf.runtime.delivery_trace import build_delivery_trace
from zf.runtime.orchestrator import Orchestrator


class _RecordingTransport:
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
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "branch", "-M", "main")
    return _git(root, "rev-parse", "HEAD")


def _task_items(count: int) -> list[dict]:
    return [
        {
            "task_id": f"TASK-{index}",
            "scope": f"scope-{index}",
            "affinity_tag": f"lane-task-{index}",
            "allowed_paths": [f"task-{index}.txt"],
            "verification": f"test -f task-{index}.txt",
            "payload": {"instruction": f"Create task-{index}.txt"},
        }
        for index in range(1, count + 1)
    ]


def _pipeline_spec(lane_count: int):
    return parse_lane_pipeline({
        "id": "demo",
        "kind": "lane_pipeline",
        "trigger": "task_map.ready",
        "affinity_key": "affinity_tag",
        "lane_count": lane_count,
        "assembly": "none",
        "barriers": {"stage_transition": "per_lane", "final": "all_tasks_verified"},
        "stages": [
            {
                "id": "impl",
                "terminal": {
                    "success": "dev.build.done",
                    "failure": "dev.failed",
                },
            },
            {
                "id": "verify",
                "terminal": {
                    "success": "verify.child.completed",
                    "failure": "verify.child.failed",
                },
                "on_failure": {"rework_to": "impl", "feedback_artifact": "required"},
            },
        ],
        "final": {
            "when": "all_tasks_verified",
            "role": "judge",
            "success": "judge.passed",
            "failure": "judge.failed",
        },
    })


def _config(state_dir: Path, *, lane_count: int = 2) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        roles=[
            *[
                RoleConfig(
                    name="dev",
                    instance_id=f"dev-{index + 1}",
                    backend="mock",
                    role_kind="writer",
                    publishes=["dev.build.done", "dev.failed"],
                )
                for index in range(lane_count)
            ],
            *[
                RoleConfig(
                    name="test",
                    instance_id=f"test-{index + 1}",
                    backend="mock",
                    role_kind="reader",
                    publishes=["verify.child.completed", "verify.child.failed"],
                )
                for index in range(lane_count)
            ],
            RoleConfig(
                name="judge",
                instance_id="judge",
                backend="mock",
                role_kind="reader",
                publishes=["judge.child.completed", "judge.child.failed"],
            ),
        ],
        workflow=WorkflowConfig(
            stages=[
                WorkflowStageConfig(
                    id="demo-impl",
                    trigger="task_map.ready",
                    topology="fanout_writer_scoped",
                    roles=[f"dev-{index + 1}" for index in range(lane_count)],
                    task_map=".zf/artifacts/${pdd_id}/task_map.json",
                    assignment=FanoutAssignmentConfig(
                        strategy="affinity_stage_slots",
                        lane_profile="demo-slot",
                        stage_slot="impl",
                    ),
                    aggregate=FanoutAggregateConfig(
                        mode="candidate_integration",
                        success_event="candidate.ready",
                        failure_event="integration.failed",
                    ),
                ),
                WorkflowStageConfig(
                    id="demo-verify",
                    trigger="lane.stage.completed",
                    topology="fanout_reader",
                    roles=[f"test-{index + 1}" for index in range(lane_count)],
                    assignment=FanoutAssignmentConfig(
                        strategy="affinity_stage_slots",
                        lane_profile="demo-slot",
                        stage_slot="verify",
                    ),
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        child_success_event="verify.child.completed",
                        child_failure_event="verify.child.failed",
                        success_event="lane.stage.completed",
                        failure_event="lane.stage.failed",
                    ),
                ),
                WorkflowStageConfig(
                    id="demo-final",
                    trigger="test.passed",
                    topology="fanout_reader",
                    roles=["judge"],
                    aggregate=FanoutAggregateConfig(
                        mode="wait_for_all",
                        child_success_event="judge.child.completed",
                        child_failure_event="judge.child.failed",
                        success_event="judge.passed",
                        failure_event="judge.failed",
                    ),
                ),
            ],
            affinity_lanes={
                "demo-slot": WorkflowAffinityLaneProfileConfig(
                    affinity_key="affinity_tag",
                    lanes=[
                        WorkflowAffinityLaneConfig(
                            id=f"lane{index}",
                            impl=f"dev-{index + 1}",
                            verify=f"test-{index + 1}",
                        )
                        for index in range(lane_count)
                    ],
                ),
            },
            pipelines=[_pipeline_spec(lane_count)],
        ),
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(enabled=True, mode="worktree"),
            git=GitIsolationConfig(candidate_base_ref="main"),
        ),
    )


def _state(
    tmp_path: Path,
    *,
    task_count: int = 2,
    lane_count: int = 2,
    event_schemas: dict[str, dict] | None = None,
    schema_profile: str = "",
    schema_mode: str = "disabled",
):
    _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task_map = state_dir / "artifacts" / "F-11111111" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(json.dumps({"tasks": _task_items(task_count)}), encoding="utf-8")
    for item in _task_items(task_count):
        TaskStore(state_dir / "kanban.json").add(Task(
            id=item["task_id"],
            title=item["task_id"],
            status="backlog",
            contract=TaskContract(
                feature_id="F-11111111",
                scope=item["allowed_paths"],
                behavior=item["payload"]["instruction"],
                verification=item["verification"],
                evidence_contract={
                    "source_refs": {
                        "task_map_ref": ".zf/artifacts/F-11111111/task_map.json",
                    },
                },
            ),
        ))
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    config = _config(state_dir, lane_count=lane_count)
    config.workflow.dag.event_schemas = dict(event_schemas or {})
    config.workflow.dag.schema_profile = schema_profile
    config.verification.event_schema.mode = schema_mode
    orch = Orchestrator(state_dir, config, transport)
    return state_dir, log, transport, orch


def _start(orch: Orchestrator) -> None:
    orch.run_once(events=[ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        correlation_id="trace-1",
        payload={"pdd_id": "F-11111111"},
    )])


def _manifest(state_dir: Path, fanout_id: str) -> dict:
    return json.loads(
        (state_dir / "fanouts" / fanout_id / "manifest.json").read_text(
            encoding="utf-8",
        )
    )


def _fanout_id(log: EventLog, stage_id: str) -> str:
    return next(
        event.payload["fanout_id"]
        for event in log.read_all()
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == stage_id
    )


def _child(manifest: dict, task_id: str) -> dict:
    return next(child for child in manifest["children"] if child["task_id"] == task_id)


def _commit(workdir: Path, file_name: str, content: str, message: str) -> str:
    (workdir / file_name).write_text(content, encoding="utf-8")
    _git(workdir, "add", file_name)
    _git(workdir, "commit", "-q", "-m", message)
    return _git(workdir, "rev-parse", "HEAD")


def _complete_writer(
    orch: Orchestrator,
    *,
    fanout_id: str,
    child: dict,
    task_id: str,
    file_name: str,
    content: str | None = None,
) -> None:
    commit = _commit(
        Path(child["workdir"]),
        file_name,
        content if content is not None else f"{task_id}\n",
        task_id,
    )
    orch.run_once(events=[ZfEvent(
        type="dev.build.done",
        actor=child["role_instance"],
        task_id=task_id,
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "dispatch_id": child["run_id"],
            "pdd_id": "F-11111111",
            "source_commit": commit,
            "source_branch": child["source_branch"],
            "workdir": child["workdir"],
        },
    )])


def _complete_verify(orch: Orchestrator, *, state_dir: Path, fanout_id: str) -> None:
    manifest = _manifest(state_dir, fanout_id)
    child = manifest["children"][0]
    orch.run_once(events=[ZfEvent(
        type="verify.child.completed",
        actor=child["role_instance"],
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "role_instance": child["role_instance"],
            "status": "completed",
        },
    )])


def _fail_verify(orch: Orchestrator, *, state_dir: Path, fanout_id: str) -> None:
    manifest = _manifest(state_dir, fanout_id)
    child = manifest["children"][0]
    orch.run_once(events=[ZfEvent(
        type="verify.child.failed",
        actor=child["role_instance"],
        correlation_id="trace-1",
        payload={
            "fanout_id": fanout_id,
            "child_id": child["child_id"],
            "run_id": child["run_id"],
            "role_instance": child["role_instance"],
            "status": "failed",
            "reason": "regression still fails",
        },
    )])


def test_impl_lane_completion_starts_verify_for_same_lane_without_batch_barrier(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path, task_count=2)
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_manifest = _manifest(state_dir, impl_id)

    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(impl_manifest, "TASK-1"),
        task_id="TASK-1",
        file_name="task-1.txt",
    )

    events = log.read_all()
    lane_events = [
        event for event in events
        if event.type == "lane.stage.completed"
        and event.payload.get("stage_slot") == "impl"
    ]
    assert len(lane_events) == 1
    assert lane_events[0].payload["task_id"] == "TASK-1"
    assert lane_events[0].payload["lane_id"] == "lane0"
    assert lane_events[0].payload["next_stage_slot"] == "verify"
    verify_started = [
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "demo-verify"
    ]
    assert len(verify_started) == 1
    verify_manifest = _manifest(state_dir, verify_started[0].payload["fanout_id"])
    assert verify_manifest["children"][0]["task_id"] == "TASK-1"
    assert verify_manifest["children"][0]["lane_id"] == "lane0"
    assert verify_manifest["children"][0]["role_instance"] == "test-1"
    assert [sent[0] for sent in transport.sent] == ["dev-1", "dev-2", "test-1"]
    assert not [event for event in events if event.type == "candidate.ready"]


def test_lane_completion_releases_writer_queue_and_streams_verify(
    tmp_path: Path,
) -> None:
    state_dir, log, transport, orch = _state(tmp_path, task_count=3)
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_manifest = _manifest(state_dir, impl_id)

    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(impl_manifest, "TASK-1"),
        task_id="TASK-1",
        file_name="task-1.txt",
    )

    final_manifest = _manifest(state_dir, impl_id)
    task3 = _child(final_manifest, "TASK-3")
    assert task3["status"] == "dispatched"
    assert task3["role_instance"] == "dev-1"
    assert task3["lane_id"] == "lane0"
    assert "test-1" in [sent[0] for sent in transport.sent]
    assert [event.payload["task_id"] for event in log.read_all()
            if event.type == "fanout.slot.assigned"] == [
        "TASK-1", "TASK-2", "TASK-3",
    ]


def test_web_projection_and_delivery_trace_surface_lane_stage_handoff(
    tmp_path: Path,
) -> None:
    pytest.importorskip("fastapi")
    from zf.web.projections.fanouts import _fanout_detail

    state_dir, log, _transport, orch = _state(tmp_path, task_count=2)
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    impl_manifest = _manifest(state_dir, impl_id)

    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(impl_manifest, "TASK-1"),
        task_id="TASK-1",
        file_name="task-1.txt",
    )

    impl_detail = _fanout_detail(state_dir, impl_id, config=orch.config)
    lane_events = impl_detail["lane_stage_events"]
    assert len(lane_events) == 1
    assert lane_events[0]["event_type"] == "lane.stage.completed"
    assert lane_events[0]["task_id"] == "TASK-1"
    assert lane_events[0]["stage_slot"] == "impl"
    assert lane_events[0]["next_stage_slot"] == "verify"
    assert lane_events[0]["root_fanout_id"] == impl_id

    verify_id = _fanout_id(log, "demo-verify")
    verify_detail = _fanout_detail(state_dir, verify_id, config=orch.config)
    verify_child = verify_detail["children"][0]
    assert verify_child["pipeline_id"] == "demo"
    assert verify_child["root_fanout_id"] == impl_id
    assert verify_child["upstream_stage_slot"] == "impl"
    assert verify_detail["lane_projection"]["scope"] == "scoped_reverify"

    tasks = {
        task.id: task
        for task in TaskStore(state_dir / "kanban.json").list_all()
    }
    trace = build_delivery_trace(
        feature_id="F-11111111",
        generated_at="2026-06-30T00:00:00+00:00",
        tasks=tasks,
        task_map={"tasks": _task_items(2)},
        events=list(enumerate(log.read_all(), start=1)),
        task_map_ref=".zf/artifacts/F-11111111/task_map.json",
    )
    lane_nodes = [
        node for node in trace["workflow_spine"]["nodes"]
        if node["kind"] == "lane_stage_completed"
    ]
    assert len(lane_nodes) == 1
    assert lane_nodes[0]["task_id"] == "TASK-1"
    assert lane_nodes[0]["lane_id"] == "lane0"
    assert lane_nodes[0]["stage_slot"] == "impl"
    assert lane_nodes[0]["next_stage_slot"] == "verify"
