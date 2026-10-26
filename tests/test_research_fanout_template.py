from __future__ import annotations

from pathlib import Path
import hashlib

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
from zf.runtime.event_problem_registry import spec_for_event
from zf.runtime.workflow_anchor import mark_workflow_managed_task

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


def test_adaptive_research_failure_has_recovery_contract() -> None:
    spec = spec_for_event("research.adaptive.failed")

    assert spec is not None
    assert spec.failure_class == "research_adaptive_failed"
    assert spec.owner_route == "run_manager"


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
    synth_briefing = Path(transport.sent[-1][1]).read_text(encoding="utf-8")
    assert "## Research handoff" in synth_briefing
    for field in (
        "architecture", "acceptance_matrix", "test_matrix", "task_map",
        "prd_prompt_input", "refactor_prompt_input",
    ):
        assert f'"{field}"' in synth_briefing
    assert "not an approved implementation plan" in synth_briefing

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
                "architecture": {"proposal": "retain a pure validator"},
                "acceptance_matrix": [{"id": "AC-STRICT", "criterion": "reject bool"}],
                "test_matrix": [{"id": "CHECK-BOOL", "acceptance_id": "AC-STRICT"}],
                "task_map": [{"id": "PROPOSED-VALIDATOR", "status": "proposal"}],
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
    artifact_ref = aggregate.payload["research_artifact_ref"]
    artifact_path = _state_dir / artifact_ref
    assert artifact_path.exists()
    assert (
        hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        == aggregate.payload["research_artifact_digest"]
    )
    assert aggregate.payload["research_summary"] == "Evidence-backed synthesis."
    artifact_body = artifact_path.read_text(encoding="utf-8")
    for authored_fact in ("retain a pure validator", "AC-STRICT", "CHECK-BOOL", "PROPOSED-VALIDATOR"):
        assert authored_fact in artifact_body
    assert any(
        isinstance(item, dict)
        and item.get("ref") == artifact_ref
        and item.get("sha256") == aggregate.payload["research_artifact_digest"]
        for item in aggregate.payload["artifact_refs"]
    )


def test_adaptive_research_uses_one_root_and_one_canonical_result(
    tmp_path: Path,
) -> None:
    config = ZfConfig(
        project=ProjectConfig(name="adaptive-research"),
        roles=[
            RoleConfig(
                name="research_root",
                instance_id="research_root",
                backend="mock",
                role_kind="reader",
                skills=["zf-research-adaptive-root"],
            ),
        ],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="research-adaptive",
                trigger="workflow.invoke.requested",
                topology="fanout_reader",
                roles=["research_root"],
                children=[],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    child_success_event="research.child.completed",
                    child_failure_event="research.child.failed",
                    success_event="research.adaptive.completed",
                    failure_event="research.adaptive.failed",
                ),
            ),
        ]),
    )
    _state_dir, log, transport, orch = _state(tmp_path, config)
    store = TaskStore(_state_dir / "kanban.json")
    task = store.get("TASK-RESEARCH")
    assert task is not None
    mark_workflow_managed_task(task)
    store.update(task.id, contract=task.contract)

    orch.run_once(events=[ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-RESEARCH",
        correlation_id="run-adaptive-1",
        payload={
            "task_id": "TASK-RESEARCH",
            "pattern_id": "research-adaptive",
            "dispatch_id": "disp-research",
            "workflow_run_id": "run-adaptive-1",
            "requested_by": "operator",
            "reason": "explicit adaptive Research pilot",
            "expected_output": "one evidence-backed result",
            "source_refs": {
                "template_id": "research-adaptive.pilot.v1",
                "research_rollout": "opt_in_pilot",
                "topic": "HighwayPilot construction-v0",
            },
        },
    )])

    events = log.read_all()
    started = next(
        event
        for event in events
        if event.type == "fanout.started"
        and event.payload.get("stage_id") == "research-adaptive"
    )
    fanout_id = started.payload["fanout_id"]
    dispatches = [
        event
        for event in events
        if event.type == "fanout.child.dispatched"
        and event.payload.get("fanout_id") == fanout_id
    ]
    assert len(dispatches) == 1
    assert dispatches[0].payload["role_instance"] == "research_root"
    assert [item[0] for item in transport.sent] == ["research_root"]
    root_briefing = Path(
        dispatches[0].payload["briefing_path"]
    ).read_text(encoding="utf-8")
    assert "Adaptive Research Root Contract" in root_briefing
    assert "zero to four Provider-native Explore children" in root_briefing
    for field in (
        "provider_operation_summary",
        "acceptance_matrix",
        "test_matrix",
        "task_map",
    ):
        assert f'"{field}"' in root_briefing

    dispatch = dispatches[0]
    root_report = {
        "summary": "Root joined two read-only Provider children.",
        "findings": [{
            "id": "F-1",
            "status": "confirmed",
            "message": "The bounded scenario is implementable.",
        }],
        "recommendation": "approve",
        "architecture": {"scenario": "construction-v0"},
        "acceptance_matrix": [{"id": "AC-1"}],
        "test_matrix": [{"id": "T-1"}],
        "task_map": [{"id": "TASK-1"}],
        "evidence_refs": ["docs/source.md#case-1"],
        "provider_operation_summary": {
            "schema_version": "provider-operation-summary.v1",
            "workflow_run_id": "run-adaptive-1",
            "operation_id": "provider-root-1",
            "provider_session_id": "provider-session-1",
            "settlement": "settled",
            "child_count": 2,
            "child_status_counts": {"completed": 2},
            "active_child_count": 0,
            "peak_parallel_agents": 2,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "cost_usd": 0.1,
        },
    }
    orch.run_once(events=[ZfEvent(
        type="research.child.completed",
        actor="research_root",
        task_id="TASK-RESEARCH",
        correlation_id="run-adaptive-1",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "research-adaptive",
            "child_id": dispatch.payload["child_id"],
            "run_id": dispatch.payload["run_id"],
            "role_instance": "research_root",
            "status": "completed",
            "report": root_report,
        },
    )])

    assert [item[0] for item in transport.sent] == ["research_root"]
    assert sum(
        event.type == "fanout.synth.dispatched"
        for event in log.read_all()
    ) == 0

    events = log.read_all()
    aggregate = next(
        event
        for event in events
        if event.type == "fanout.aggregate.completed"
        and event.payload.get("fanout_id") == fanout_id
    )
    assert aggregate.payload["status"] == "completed"
    assert aggregate.payload["research_artifact_ref"].startswith(
        "artifacts/research/TASK-RESEARCH/"
    )
    assert aggregate.payload["provider_operation_summary_status"] == (
        "available"
    )
    result = next(
        event
        for event in events
        if event.type == "workflow.result.available"
    )
    assert result.payload["root_result_event_id"]
    assert result.payload["synth_event_id"] == ""
    assert sum(
        event.type == "workflow.result.available"
        for event in events
    ) == 1
    terminal_task = store.get("TASK-RESEARCH")
    assert terminal_task is not None
    assert terminal_task.status == "done"
    assert sum(
        event.type == "task.done.evidence"
        for event in events
    ) == 1
