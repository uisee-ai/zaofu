from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config
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

RESEARCH_CONFIG = Path(__file__).parent / "fixtures" / "research_fanout.yaml"


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


def _state(tmp_path: Path, config: ZfConfig):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    TaskStore(state_dir / "kanban.json").add(
        Task(
            id="TASK-RESEARCH",
            title="Research channel workflow",
            status="in_progress",
            active_dispatch_id="disp-research",
        )
    )
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(state_dir, config, transport)  # type: ignore[arg-type]
    return state_dir, log, transport, orch


def test_research_fixture_declares_fixed_fanout_template() -> None:
    config = load_config(RESEARCH_CONFIG)

    stage = next(
        stage for stage in config.workflow.stages
        if stage.id == "research-fanout"
    )
    assert stage.trigger == "workflow.invoke.requested"
    assert stage.topology == "fanout_reader"
    assert stage.roles == [
        "source_researcher",
        "product_analyst",
        "technical_analyst",
        "risk_critic",
        "synthesizer",
    ]
    assert [child.role_instance for child in stage.children] == [
        "source_researcher",
        "product_analyst",
        "technical_analyst",
        "risk_critic",
    ]
    assert stage.aggregate.child_success_event == "research.child.completed"
    assert stage.aggregate.child_failure_event == "research.child.failed"
    assert stage.aggregate.synth_role == "synthesizer"
    assert stage.aggregate.success_event == "research.fanout.completed"
    assert stage.aggregate.failure_event == "research.fanout.failed"

    roles = {role.name: role for role in config.roles}
    for role_name in stage.roles:
        assert roles[role_name].role_kind == "reader"


def test_workflow_invoke_fanout_stage_matches_requested_pattern_only(
    tmp_path: Path,
) -> None:
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[
            RoleConfig(name="pm", backend="mock", role_kind="reader"),
            RoleConfig(name="source_researcher", backend="mock", role_kind="reader"),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="prd-draft",
                trigger="workflow.invoke.requested",
                topology="fanout_reader",
                roles=["pm"],
            ),
            WorkflowStageConfig(
                id="research-fanout",
                trigger="workflow.invoke.requested",
                topology="fanout_reader",
                roles=["source_researcher"],
            ),
        ]),
    )
    _state_dir, log, transport, orch = _state(tmp_path, config)

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-RESEARCH",
        payload={
            "task_id": "TASK-RESEARCH",
            "pattern_id": "research-fanout",
            "dispatch_id": "disp-research",
            "expected_output": "research synthesis",
        },
    )])

    started = [event for event in log.read_all() if event.type == "fanout.started"]
    assert [event.payload["stage_id"] for event in started] == ["research-fanout"]
    assert [item[0] for item in transport.sent] == ["source_researcher"]


def test_research_fanout_template_runs_to_channel_update(tmp_path: Path) -> None:
    config = load_config(RESEARCH_CONFIG)
    _state_dir, log, transport, orch = _state(tmp_path, config)

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-RESEARCH",
        correlation_id="ch-research",
        payload={
            "task_id": "TASK-RESEARCH",
            "pattern_id": "research-fanout",
            "dispatch_id": "disp-research",
            "channel_id": "ch-research",
            "thread_id": "main",
            "workflow_run_id": "wf-research-1",
            "workflow_input_manifest_ref": "workflow-inputs/wf-research-1/manifest.json",
            "requested_by": "skill:zf-research-fanout-trigger",
            "reason": "explicit research fanout request from channel",
            "expected_output": "research synthesis plus PRD/refactor prompt inputs",
            "source_refs": {
                "template_id": "research-fanout.fixed.v1",
                "channel_id": "ch-research",
                "thread_id": "main",
            },
        },
    )])

    events = log.read_all()
    fanout_started = next(
        event for event in events
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "research-fanout"
    )
    fanout_id = fanout_started.payload["fanout_id"]
    child_dispatches = [
        event for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert [event.payload["child_id"] for event in child_dispatches] == [
        "source_researcher",
        "product_analyst",
        "technical_analyst",
        "risk_critic",
    ]
    assert [item[0] for item in transport.sent[:4]] == [
        "source_researcher",
        "product_analyst",
        "technical_analyst",
        "risk_critic",
    ]

    orch.run_once(events=[
        ZfEvent(
            type="research.child.completed",
            actor=event.payload["role_instance"],
            task_id="TASK-RESEARCH",
            correlation_id="ch-research",
            payload={
                "fanout_id": fanout_id,
                "stage_id": "research-fanout",
                "child_id": event.payload["child_id"],
                "run_id": event.payload["run_id"],
                "role_instance": event.payload["role_instance"],
                "status": "completed",
                "report": {
                    "summary": f"{event.payload['child_id']} report",
                    "evidence_refs": ["source:fixture"],
                },
            },
        )
        for event in child_dispatches
    ])

    events = log.read_all()
    assert any(event.type == "fanout.synth.dispatched" for event in events)
    assert transport.sent[-1][0] == "synthesizer"

    orch.run_once(events=[ZfEvent(
        type="fanout.synth.completed",
        actor="synthesizer",
        task_id="TASK-RESEARCH",
        correlation_id="ch-research",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "research-fanout",
            "run_id": f"run-{fanout_id}-synth",
            "role_instance": "synthesizer",
            "status": "completed",
            "summary": "Research synthesis ready.",
            "research_summary": "Evidence-backed synthesis.",
            "evidence_refs": ["source:fixture"],
            "open_questions": [],
            "prd_prompt_input": "PRD inputs.",
            "refactor_prompt_input": "Refactor inputs.",
            "report": {
                "summary": "Research synthesis ready.",
                "recommendation": "approve",
            },
        },
    )])

    events = log.read_all()
    aggregate = next(
        event for event in events
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    )
    assert aggregate.payload["status"] == "completed"
    channel_update = next(
        event for event in events
        if event.type == "channel.state_update.posted"
        and event.payload.get("status") == "research_completed"
    )
    assert channel_update.payload["channel_id"] == "ch-research"
    assert channel_update.payload["refs"]["workflow_run_id"] == "wf-research-1"
