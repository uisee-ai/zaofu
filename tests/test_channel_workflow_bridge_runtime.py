from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_workflow_bridge import emit_fanout_channel_state_update
from zf.runtime.fanout import FanoutManifestProjector


def test_fanout_manifest_preserves_channel_source_refs_for_bridge(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    requested = writer.emit(
        "fanout.requested",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="ch-research",
        payload={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-council",
            "topology": "fanout_reader",
            "trace_id": "trace-1",
            "task_id": "TASK-1",
            "channel_id": "ch-research",
            "thread_id": "topic-a",
            "pattern_id": "research-council",
            "workflow_run_id": "wf-research",
            "workflow_input_manifest_ref": "workflow-inputs/wf-research/manifest.json",
            "workflow_prompt_ref": "workflow-inputs/wf-research/prompt.md",
            "prompt_kind": "prd",
            "source_refs": {"channel_id": "ch-research", "thread_id": "topic-a"},
            "artifact_refs": [{"kind": "research_seed", "path": "research/seed.md"}],
        },
    )
    writer.emit(
        "fanout.started",
        actor="zf-cli",
        task_id="TASK-1",
        causation_id=requested.id,
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-council",
            "topology": "fanout_reader",
            "trace_id": "trace-1",
            "trigger_event_id": requested.id,
        },
    )

    manifest = FanoutManifestProjector(state_dir).rebuild("fanout-research-1", log.read_all())

    assert manifest["channel_id"] == "ch-research"
    assert manifest["source_refs"]["thread_id"] == "topic-a"
    assert manifest["workflow_prompt_ref"].endswith("prompt.md")
    assert manifest["artifact_refs"][0]["path"] == "research/seed.md"


def test_fanout_terminal_event_posts_research_result_to_channel(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    synth = ZfEvent(
        type="fanout.synth.completed",
        actor="research-synth",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-council",
            "status": "completed",
            "recommendation": "approve",
            "summary": "The research result supports drafting the PRD.",
            "report": {"summary": "research report summary"},
        },
    )
    terminal = writer.emit(
        "fanout.aggregate.completed",
        actor="zf-cli",
        task_id="TASK-1",
        correlation_id="trace-1",
        payload={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-council",
            "status": "completed",
            "synth_event_id": synth.id,
        },
    )

    update = emit_fanout_channel_state_update(
        writer=writer,
        terminal_event=terminal,
        synth_event=synth,
        manifest={
            "fanout_id": "fanout-research-1",
            "stage_id": "research-council",
            "task_id": "TASK-1",
            "channel_id": "ch-research",
            "thread_id": "topic-a",
            "workflow_run_id": "wf-research",
            "workflow_input_manifest_ref": "workflow-inputs/wf-research/manifest.json",
            "workflow_prompt_ref": "workflow-inputs/wf-research/prompt.md",
            "prompt_kind": "prd",
            "source_refs": {"channel_id": "ch-research", "thread_id": "topic-a"},
            "artifact_refs": [{"kind": "research_report", "path": "research/report.md"}],
        },
    )

    assert update is not None
    assert update.type == "channel.state_update.posted"
    assert update.payload["status"] == "research_completed"
    assert update.payload["refs"]["workflow_prompt_ref"] == "workflow-inputs/wf-research/prompt.md"
    detail = project_channel(state_dir, "ch-research")
    assert detail is not None
    assert detail["state_updates"][0]["summary"] == "The research result supports drafting the PRD."
