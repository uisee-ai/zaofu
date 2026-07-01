from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="review-a", backend="mock", role_kind="reader"),
            RoleConfig(name="review-b", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-wave",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-a", "review-b"],
                target_ref="candidate/${task_id}",
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="review.approved",
                    failure_event="review.rejected",
                ),
            ),
        ]),
    )


def _state(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    task = Task(id="TASK-1", title="Review candidate", active_dispatch_id="disp-1")
    TaskStore(state_dir / "kanban.json").add(task)
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, _config(), transport)  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def test_workflow_invoke_accepts_declared_pattern_and_emits_fanout_intent(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "th-plan",
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "dispatch_id": "disp-1",
            "requested_by": "qa",
            "reason": "risk review",
            "source": "web",
            "source_refs": {
                "channel_id": "ch-zaofu",
                "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            },
            "workflow_run_id": "wf-review",
            "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            "artifact_refs": [{"path": "channels/ch-zaofu/spec.md"}],
            "expected_output": "review report",
        },
    )])

    events = log.read_all()
    assert any(event.type == "workflow.invoke.accepted" for event in events)
    accepted = next(event for event in events if event.type == "workflow.invoke.accepted")
    assert accepted.payload["source_refs"]["channel_id"] == "ch-zaofu"
    assert accepted.payload["workflow_input_manifest_ref"] == "workflow-inputs/wf-review/manifest.json"
    fanout = next(event for event in events if event.type == "task.fanout.requested")
    assert fanout.payload["requested_specialists"] == ["review-a", "review-b"]
    assert fanout.payload["expected_output"] == "review report"
    assert fanout.payload["artifact_refs"] == [{"path": "channels/ch-zaofu/spec.md"}]


def test_workflow_invoke_rejects_blocking_open_questions(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "dispatch_id": "disp-1",
            "requested_by": "qa",
            "reason": "risk review",
            "source": "web",
            "source_refs": {"channel_id": "ch-zaofu"},
            "open_questions": ["which target?"],
        },
    )])

    events = log.read_all()
    rejected = next(event for event in events if event.type == "workflow.invoke.rejected")
    assert rejected.payload["reason"] == "blocking open questions"
    assert not any(event.type == "task.fanout.requested" for event in events)


def test_task_fanout_request_rejects_missing_expected_output(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "review",
            "scope": [],
            "requested_specialists": ["review-a"],
            "risk": "",
        },
    )])

    rejected = next(event for event in log.read_all() if event.type == "task.fanout.rejected")
    assert rejected.payload["reason"] == "expected_output missing"


def test_task_fanout_request_rejects_write_capability(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "review",
            "scope": [],
            "requested_specialists": ["review-a"],
            "expected_output": "review report",
            "risk": "",
            "write_files": ["src/app.py"],
        },
    )])

    rejected = next(event for event in log.read_all() if event.type == "task.fanout.rejected")
    assert rejected.payload["reason"] == "reader fanout cannot request write capability"


def test_task_fanout_request_propagates_workflow_input_refs_to_children(tmp_path: Path) -> None:
    _state_dir, log, _transport, orch = _state(tmp_path)

    orch.run_once(events=[ZfEvent(
        type="task.fanout.requested",
        actor="dev",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "dispatch_id": "disp-1",
            "requested_by": "dev",
            "reason": "review",
            "scope": ["docs/"],
            "requested_specialists": ["review-a", "review-b"],
            "expected_output": "review report",
            "risk": "",
            "source_refs": {
                "channel_id": "ch-zaofu",
                "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            },
            "workflow_run_id": "wf-review",
            "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            "artifact_refs": [{"path": "channels/ch-zaofu/spec.md"}],
        },
    )])

    events = log.read_all()
    fanout = next(event for event in events if event.type == "fanout.requested")
    assert fanout.payload["workflow_input_manifest_ref"] == "workflow-inputs/wf-review/manifest.json"
    child_events = [event for event in events if event.type == "fanout.child.dispatched"]
    assert len(child_events) == 2
    assert all(event.payload["scope"] == ["docs/"] for event in child_events)
    assert all(
        event.payload["workflow_input_manifest_ref"] == "workflow-inputs/wf-review/manifest.json"
        for event in child_events
    )
    assert all(event.payload["artifact_refs"] == [{"path": "channels/ch-zaofu/spec.md"}] for event in child_events)
