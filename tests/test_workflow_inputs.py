from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.runtime.orchestrator_dispatch import DispatchMixin
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.workflow_inputs import (
    render_workflow_input_briefing_section,
    workflow_input_payload,
)


def _config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test", state_dir=str(state_dir)),
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="review-wave",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-a", "review-b"],
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="review.approved",
                    failure_event="review.rejected",
                ),
            ),
            WorkflowStageConfig(
                id="prd-draft",
                trigger="workflow.invoke.requested",
                topology="fanout_reader",
                roles=["pm"],
            ),
            WorkflowStageConfig(
                id="refactor-plan",
                trigger="workflow.invoke.requested",
                topology="fanout_reader",
                roles=["architect"],
            ),
        ]),
    )


def test_workflow_invoke_action_writes_input_manifest_and_source_refs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"), correlation_id="ch-zaofu")
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={"action": "workflow-invoke"},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=_config(state_dir),
        actor="web",
        source="kanban-agent",
        surface="web",
    )

    result = service.execute(
        action="workflow-invoke",
        requested_action="workflow.invoke",
        requested=requested,
        payload={
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "requested_by": "operator",
            "reason": "approved channel plan",
            "synthesis_event_id": "evt-synth-1",
            "artifact_refs": [{"path": "channels/ch-zaofu/spec.md", "sha256": "abc"}],
        },
    )

    assert result["status"] == "requested"
    assert result["action_result"]["schema_version"] == "controlled-action-result.v1"
    assert result["action_result"]["status"] == "requested"
    manifest_ref = result["workflow_input_manifest_ref"]
    manifest_path = state_dir / manifest_ref
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "workflow-input-manifest.v1"
    assert manifest["source_refs"]["channel_id"] == "ch-zaofu"
    assert manifest["source_refs"]["synthesis_event_id"] == "evt-synth-1"
    assert manifest["artifact_refs"][0]["path"] == "channels/ch-zaofu/spec.md"

    invoke = next(event for event in writer.event_log.read_all() if event.type == "workflow.invoke.requested")
    assert invoke.payload["source_refs"]["workflow_input_manifest_ref"] == manifest_ref
    event_types = [event.type for event in writer.event_log.read_all()]
    assert "runtime.action.attempt.started" in event_types
    assert "runtime.action.attempt.completed" in event_types
    assert invoke.payload["artifact_refs"][0]["sha256"] == "abc"


@pytest.mark.parametrize(
    ("pattern_id", "expected_kind", "expected_title", "expected_contract"),
    [
        ("prd-draft", "prd", "PRD Workflow Prompt", "Produce a PRD"),
        ("refactor-plan", "refactor", "Refactor Workflow Prompt", "Produce a refactor plan"),
    ],
)
def test_workflow_invoke_generates_prd_and_refactor_prompt_packages(
    tmp_path: Path,
    pattern_id: str,
    expected_kind: str,
    expected_title: str,
    expected_contract: str,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"), correlation_id="ch-zaofu")
    writer.emit(
        "channel.message.posted",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "research-thread",
            "message_id": "msg-1",
            "member_id": "operator",
            "role": "user",
            "source": "web",
            "text": "Use the returned research to decide the next workflow.",
        },
    )
    writer.emit(
        "channel.state_update.posted",
        actor="zf-cli",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "research-thread",
            "status": "research_completed",
            "summary": "market scan found import-flow demand",
            "source": "runtime",
            "refs": {
                "artifact_refs": [
                    {
                        "kind": "research_report",
                        "path": "research/TASK-1/import-flow.md",
                        "summary": "import-flow evidence",
                    },
                ],
            },
        },
    )
    synthesis = writer.emit(
        "channel.synthesis.proposed",
        actor="web",
        correlation_id="ch-zaofu",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "research-thread",
            "decision": "invoke_workflow",
            "summary": "Build the import flow from research evidence.",
            "source": "web",
        },
    )
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        correlation_id="ch-zaofu",
        payload={"action": "workflow-invoke"},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=_config(state_dir),
        actor="web",
        source="kanban-agent",
        surface="web",
    )

    result = service.execute(
        action="workflow-invoke",
        requested_action="workflow.invoke",
        requested=requested,
        payload={
            "task_id": "TASK-1",
            "pattern_id": pattern_id,
            "channel_id": "ch-zaofu",
            "thread_id": "research-thread",
            "reason": "discussion synthesis approved",
            "expected_output": f"generate {expected_kind} prompt",
            "synthesis_event_id": synthesis.id,
        },
    )

    prompt_ref = result["workflow_prompt_ref"]
    assert result["prompt_kind"] == expected_kind
    prompt = (state_dir / prompt_ref).read_text(encoding="utf-8")
    assert expected_title in prompt
    assert expected_contract in prompt
    assert "market scan found import-flow demand" in prompt
    assert "Build the import flow from research evidence." in prompt
    assert "research/TASK-1/import-flow.md" in prompt

    manifest = json.loads((state_dir / result["workflow_input_manifest_ref"]).read_text(encoding="utf-8"))
    assert manifest["source_refs"]["workflow_prompt_ref"] == prompt_ref
    assert manifest["source_refs"]["prompt_kind"] == expected_kind
    prompt_refs = [ref for ref in manifest["artifact_refs"] if ref.get("kind") == "workflow_prompt"]
    assert prompt_refs[0]["ref"] == prompt_ref


def test_replan_owner_decision_action_records_event_only(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"), correlation_id="replan")
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        correlation_id="replan",
        payload={"action": "replan-approve"},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=_config(state_dir),
        actor="web",
        source="kanban-agent",
        surface="web",
    )

    result = service.execute(
        action="replan-approve",
        requested_action="replan.approve",
        requested=requested,
        payload={
            "proposal_ref": ".zf/autoresearch/replan.json",
            "eval_ref": ".zf/artifacts/eval.json",
            "candidate_task_map_ref": "tm-v2",
            "reason": "owner approves after review",
            "owner": "owner:min",
        },
    )

    assert result["status"] == "approved"
    events = writer.event_log.read_all()
    decision = [event for event in events if event.type == "replan.owner_decision.approved"][0]
    assert decision.payload["proposal_ref"] == ".zf/autoresearch/replan.json"
    assert decision.payload["eval_ref"] == ".zf/artifacts/eval.json"
    assert decision.payload["direct_adoption"] is False
    assert "task.created" not in [event.type for event in events]


def test_replan_owner_decision_rejects_missing_refs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"), correlation_id="replan")
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        correlation_id="replan",
        payload={"action": "replan-reject"},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=_config(state_dir),
        actor="web",
        source="kanban-agent",
        surface="web",
    )

    result = service.execute(
        action="replan-reject",
        requested_action="replan.reject",
        requested=requested,
        payload={"proposal_ref": ".zf/autoresearch/replan.json"},
    )

    assert result["status"] == "invalid_payload"
    assert "replan.owner_decision.rejected" not in [
        event.type for event in writer.event_log.read_all()
    ]


def test_workflow_input_briefing_section_reads_nested_trigger_payload() -> None:
    payload = {
        "trigger_payload": {
            "workflow_run_id": "wf-review-1",
            "workflow_input_manifest_ref": "workflow-inputs/wf-review-1/manifest.json",
            "source_refs": {"channel_id": "ch-zaofu"},
            "artifact_refs": [{"path": "channels/ch-zaofu/spec.md"}],
        },
    }

    extracted = workflow_input_payload(payload)
    assert extracted["workflow_input_manifest_ref"] == "workflow-inputs/wf-review-1/manifest.json"
    section = render_workflow_input_briefing_section(payload)
    assert "## Workflow Input Manifest" in section
    assert "channels/ch-zaofu/spec.md" in section


def test_dispatch_mixin_renders_latest_workflow_input_context(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.emit(
        "workflow.invoke.accepted",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "source_event_id": "evt-invoke",
            "workflow_run_id": "wf-review",
            "workflow_input_manifest_ref": "workflow-inputs/wf-review/manifest.json",
            "source_refs": {"channel_id": "ch-zaofu"},
            "artifact_refs": [{"path": "channels/ch-zaofu/spec.md"}],
        },
    )

    class Harness(DispatchMixin):
        event_log = log

    section = Harness()._workflow_input_context_for_dispatch(Task(id="TASK-1", title="Review"))

    assert "## Workflow Input Manifest" in section
    assert "workflow-inputs/wf-review/manifest.json" in section
    assert "channels/ch-zaofu/spec.md" in section
