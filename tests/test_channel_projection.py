from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.core.verification.event_schema import EventSchemaRegistry, channel_event_schema_rules
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_projection import project_channel, project_channels, search_channel_history
from zf.runtime.control_actions import ControlledActionService


def test_channel_projection_rebuilds_roster_transcript_and_attention(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": "ch-zaofu", "name": "zaofu", "source": "web"},
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.member.invited",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "th-plan",
            "member_id": "qa",
            "persona": "QA",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "th-plan",
            "message_id": "msg-1",
            "member_id": "operator",
            "source": "web",
            "text": "请 QA 评估",
            "mentions": ["qa"],
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.message.failed",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "th-plan",
            "message_id": "msg-1",
            "member_id": "qa",
            "source": "web",
            "reason": "session not running",
        },
        correlation_id="ch-zaofu",
    ))

    listing = project_channels(state_dir)
    detail = project_channel(state_dir, "ch-zaofu")

    assert listing["channels"][0]["channel_id"] == "ch-zaofu"
    assert detail is not None
    assert detail["members"][0]["member_id"] == "qa"
    assert detail["messages"][0]["message_id"] == "msg-1"
    assert detail["read_state"][0]["mention_count"] == 1
    assert detail["attention"][0]["attention"]["reason"] == "delivery_failed"


def test_channel_projection_hides_removed_members_and_cleared_history(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": "ch-zaofu", "name": "zaofu", "source": "web"},
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.member.invited",
        actor="web",
        payload={"channel_id": "ch-zaofu", "member_id": "qa", "persona": "QA", "source": "web"},
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "text": "@qa verify",
            "mentions": ["qa"],
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.member.removed",
        actor="web",
        payload={"channel_id": "ch-zaofu", "member_id": "qa", "reason": "done", "source": "web"},
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.history.cleared",
        actor="web",
        payload={"channel_id": "ch-zaofu", "thread_id": "main", "reason": "reset", "source": "web"},
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    assert detail["members"] == []
    assert detail["messages"] == []
    assert detail["reply_requests"] == []
    assert detail["message_count"] == 0
    assert detail["history_clear_reason"] == "reset"


def test_channel_projection_archived_channels_leave_active_listing(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": "ch-old", "name": "# old", "source": "web"},
        correlation_id="ch-old",
    ))
    log.append(ZfEvent(
        type="channel.archived",
        actor="web",
        payload={"channel_id": "ch-old", "thread_id": "main", "reason": "delete", "source": "web"},
        correlation_id="ch-old",
    ))

    listing = project_channels(state_dir)
    detail = project_channel(state_dir, "ch-old")

    assert listing["channels"] == []
    assert detail is not None
    assert detail["status"] == "archived"


def test_channel_projection_records_synthesis_and_workflow_request(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.synthesis.proposed",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "th-plan",
            "decision": "invoke_workflow",
            "summary": "run review-wave",
            "source": "web",
            "recommended_workflow": {"pattern_id": "review-wave"},
            "artifact_ref": "docs/plans/channel-synthesis.md",
            "source_refs": ["channel:ch-zaofu:th-plan"],
            "evidence_refs": ["event:evt-discussion"],
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="workflow.invoke.requested",
        actor="web",
        task_id="TASK-1",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "th-plan",
            "task_id": "TASK-1",
            "pattern_id": "review-wave",
            "requested_by": "qa",
            "reason": "synthesis",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    assert detail["syntheses"][0]["decision"] == "invoke_workflow"
    assert detail["syntheses"][0]["artifact_ref"].endswith("channel-synthesis.md")
    assert detail["syntheses"][0]["source_refs"] == ["channel:ch-zaofu:th-plan"]
    assert detail["workflow_requests"][0]["pattern_id"] == "review-wave"


def test_channel_projection_records_reply_context_and_member_status(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.member.connected",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-1",
            "source": "web",
            "provider_session_id": "provider-1",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.mention.detected",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "target_member_id": "codex-1",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.context_pack.built",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "context_pack_id": "ctx-1",
            "target_member_id": "codex-1",
            "trigger_message_id": "msg-1",
            "message_refs": [{"message_id": "msg-1"}],
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.requested",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "message_id": "msg-1",
            "member_id": "operator",
            "target_member_id": "codex-1",
            "status": "pending",
            "context_pack_id": "ctx-1",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    assert detail["members"][0]["status"] == "connected"
    assert detail["mentions_detected"][0]["target_member_id"] == "codex-1"
    assert detail["context_packs"][0]["context_pack_id"] == "ctx-1"
    assert detail["reply_requests"][0]["request_id"] == "reply-1"
    assert detail["pending_reply_count"] == 1


def test_channel_projection_records_provider_runs_and_member_runtime(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.member.connected",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-1",
            "member_type": "provider_agent",
            "backend": "codex",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.context_pack.built",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "context_pack_id": "ctx-1",
            "target_member_id": "codex-1",
            "trigger_message_id": "msg-1",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.requested",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "message_id": "msg-1",
            "member_id": "operator",
            "target_member_id": "codex-1",
            "status": "pending",
            "context_pack_id": "ctx-1",
            "backend": "codex",
            "run_id": "run-explicit",
            "provider_run_id": "run-explicit",
            "run_generation": 2,
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.started",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "provider_session_id": "provider-1",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    assert detail["reply_requests"][0]["run_id"] == "run-explicit"
    assert detail["reply_requests"][0]["backend"] == "codex"
    assert detail["provider_runs"][0]["run_generation"] == 2
    assert detail["provider_runs"][0]["provider_session_id"] == "provider-1"
    member = detail["members"][0]
    assert member["presence"] == "running"
    assert member["active_request_id"] == "reply-1"
    assert member["latest_run_id"] == "run-explicit"
    assert member["context_status"] == "built"


def test_channel_projection_ignores_late_stale_run_generation(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.agent.reply.requested",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "message_id": "msg-1",
            "target_member_id": "codex-1",
            "status": "pending",
            "run_id": "run-current",
            "provider_run_id": "run-current",
            "run_generation": 2,
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.started",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "target_member_id": "codex-1",
            "run_id": "run-current",
            "provider_run_id": "run-current",
            "run_generation": 2,
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.completed",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "target_member_id": "codex-1",
            "run_id": "run-old",
            "provider_run_id": "run-old",
            "run_generation": 1,
            "reason": "late provider completion",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    reply = detail["reply_requests"][0]
    assert reply["status"] == "running"
    assert reply["run_id"] == "run-current"
    assert reply["run_generation"] == 2
    assert reply["stale_events"][0]["run_generation"] == 1
    assert detail["provider_runs"][0]["status"] == "running"


def test_channel_projection_records_typing_presence_and_capabilities(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.member.connected",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-1",
            "member_type": "provider_agent",
            "provider": "codex",
            "backend": "codex",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.typing.started",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-1",
            "run_id": "run-typing",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    assert detail["active_typing"][0]["member_id"] == "codex-1"
    assert detail["members"][0]["presence"] == "typing"
    assert detail["members"][0]["provider_capabilities"]["supports_resume"] is True
    assert detail["members"][0]["provider_capabilities"]["supports_interrupt"] is True


def test_channel_projection_records_agent_session_stream_parts(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.member.connected",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-1",
            "member_type": "provider_agent",
            "provider": "codex",
            "backend": "codex",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.requested",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "message_id": "msg-1",
            "target_member_id": "codex-1",
            "status": "pending",
            "backend": "codex",
            "run_id": "run-stream",
            "provider_run_id": "run-stream",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.agent.reply.started",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "message_id": "msg-1",
            "target_member_id": "codex-1",
            "run_id": "run-stream",
            "provider_run_id": "run-stream",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="agent.session.run.started",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "target_member_id": "codex-1",
            "run_id": "run-stream",
            "provider": "codex",
            "provider_session_id": "codex-session-1",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="agent.session.part.delta",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "run_id": "run-stream",
            "part_id": "thinking",
            "kind": "thinking",
            "delta": "checking context",
            "seq": 1,
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.message.stream.delta",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "run_id": "run-stream",
            "part_id": "text",
            "delta": "hello ",
            "seq": 2,
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.message.stream.delta",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "run_id": "run-stream",
            "part_id": "text",
            "delta": "world",
            "seq": 3,
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    run = detail["provider_runs"][0]
    assert run["run_id"] == "run-stream"
    assert run["status"] == "streaming"
    assert run["provider_session_id"] == "codex-session-1"
    parts = {item["part_id"]: item for item in run["parts"]}
    assert parts["thinking"]["kind"] == "thinking"
    assert parts["thinking"]["content"] == "checking context"
    assert parts["text"]["content"] == "hello world"
    assert detail["agent_session_runs"][0]["active_part_id"] == "text"
    assert detail["members"][0]["presence"] == "streaming"
    assert detail["members"][0]["latest_run_id"] == "run-stream"


def test_channel_listing_omits_full_provider_run_payload(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.agent.reply.requested",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "message_id": "msg-1",
            "target_member_id": "codex-1",
            "status": "pending",
            "backend": "codex",
            "run_id": "run-stream",
            "provider_run_id": "run-stream",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="agent.session.run.started",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "request_id": "reply-1",
            "target_member_id": "codex-1",
            "run_id": "run-stream",
            "provider": "codex",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.message.stream.delta",
        actor="router",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "run_id": "run-stream",
            "part_id": "text",
            "delta": "hello world",
            "seq": 1,
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))

    listing = project_channels(state_dir)
    detail = project_channel(state_dir, "ch-zaofu")

    channel = listing["channels"][0]
    assert channel["reply_requests"] == []
    assert channel["provider_runs"] == []
    assert channel["agent_session_runs"] == []
    assert channel["context_packs"] == []
    assert channel["reply_request_count"] == 1
    assert channel["provider_run_count"] == 1
    assert channel["agent_session_run_count"] == 1
    assert channel["latest_reply"]["request_id"] == "reply-1"
    assert detail is not None
    text_parts = [
        item for item in detail["provider_runs"][0]["parts"]
        if item.get("part_id") == "text"
    ]
    assert text_parts[0]["content"] == "hello world"


def test_channel_projection_records_attachments_and_context_artifact_refs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "source": "web",
            "text": "see attached",
            "refs": {
                "attachments": [
                    {
                        "attachment_id": "att-msg-1-1",
                        "name": "notes.md",
                        "type": "text/markdown",
                        "size": 42,
                    },
                ],
            },
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.attachment.uploaded",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "attachment_id": "att-msg-1-1",
            "message_id": "msg-1",
            "member_id": "operator",
            "name": "notes.md",
            "mime": "text/markdown",
            "size": 42,
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.artifact.attached",
        actor="codex",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "artifact_id": "art-report-1",
            "message_id": "msg-1",
            "member_id": "codex-1",
            "name": "review.md",
            "kind": "report",
            "path": "reports/review.md",
            "hash": "sha256:abc123",
            "summary": "review findings",
            "provenance": {"run_id": "run-1", "event_id": "evt-source"},
            "source": "runtime",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    assert detail["attachments"][0]["name"] == "notes.md"
    assert detail["artifacts"][0]["artifact_id"] == "art-report-1"
    pack = build_channel_context_pack(
        detail,
        channel_id="ch-zaofu",
        thread_id="main",
        target_member_id="codex-2",
        trigger_message_id="msg-1",
    )
    refs = pack["artifact_refs"]
    assert {item["name"] for item in refs} == {"notes.md", "review.md"}
    assert all("content" not in item for item in refs)


def test_channel_context_pack_includes_state_update_artifact_refs(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "source": "web",
            "text": "Discuss the research result.",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.state_update.posted",
        actor="zf-cli",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "status": "research_completed",
            "summary": "research fanout completed",
            "source": "runtime",
            "refs": {
                "workflow_prompt_ref": "workflow-inputs/wf-prd/prompt.md",
                "artifact_refs": [
                    {
                        "kind": "research_report",
                        "path": "research/TASK-1/report.md",
                        "summary": "research summary",
                    },
                ],
            },
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    pack = build_channel_context_pack(
        detail,
        channel_id="ch-zaofu",
        thread_id="main",
        target_member_id="pm",
        trigger_message_id="msg-1",
    )
    refs = pack["artifact_refs"]
    paths = {item.get("path") for item in refs}
    assert "research/TASK-1/report.md" in paths
    assert "workflow-inputs/wf-prd/prompt.md" in paths


def test_channel_projection_rejects_unprovenanced_artifact_path(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.artifact.proposed",
        actor="codex",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "artifact_id": "art-unsafe",
            "message_id": "msg-1",
            "member_id": "codex-1",
            "name": "report.md",
            "path": "/tmp/report.md",
            "source": "runtime",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    assert detail["artifacts"][0]["status"] == "rejected"
    assert detail["artifacts"][0]["reason"] == "artifact proposal missing provenance"


def test_channel_artifact_and_attachment_events_match_schema() -> None:
    registry = EventSchemaRegistry.from_dict(channel_event_schema_rules())

    attachment = ZfEvent(
        type="channel.attachment.uploaded",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "source": "web",
            "attachment_id": "att-1",
            "message_id": "msg-1",
            "name": "notes.md",
        },
    )
    artifact = ZfEvent(
        type="channel.artifact.attached",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "source": "runtime",
            "artifact_id": "art-1",
            "path": "reports/review.md",
            "hash": "sha256:abc123",
            "provenance": {"run_id": "run-1"},
        },
    )

    assert registry.validate(attachment) == []
    assert registry.validate(artifact) == []


def test_channel_post_message_action_emits_attachment_upload_events(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-post-message", "request": {}},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        actor="web",
        source="channel",
        surface="web",
    )

    response = service.execute(
        action="channel-post-message",
        requested_action="channel.post_message",
        requested=requested,
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "text": "see attached",
            "refs": {
                "attachments": [
                    {
                        "name": "notes.md",
                        "type": "text/markdown",
                        "size": 42,
                        "source": "browser-file-picker",
                    },
                ],
            },
        },
    )

    detail = project_channel(state_dir, "ch-zaofu")
    event_types = [event.type for event in log.read_all()]

    assert response["status"] == "posted"
    assert len(response["attachment_event_ids"]) == 1
    assert "channel.attachment.uploaded" in event_types
    assert detail is not None
    assert detail["attachments"][0]["attachment_id"] == "att-msg-1-1"
    assert detail["attachments"][0]["name"] == "notes.md"


def test_channel_history_search_indexes_thread_member_and_mentions(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "source": "web",
            "text": "please review the launch plan",
            "mentions": ["qa"],
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.message.posted",
        actor="qa",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "qa-thread",
            "message_id": "msg-2",
            "member_id": "qa",
            "role": "assistant",
            "source": "web",
            "text": "launch risk is acceptable",
        },
        correlation_id="ch-zaofu",
    ))

    result = search_channel_history(state_dir, "ch-zaofu", q="launch", limit=10)
    mention_result = search_channel_history(state_dir, "ch-zaofu", mention="qa", limit=10)

    assert [item["message_id"] for item in result["items"]] == ["msg-2", "msg-1"]
    assert result["history_index"]["threads"][0]["id"] == "main"
    assert {item["id"] for item in result["history_index"]["members"]} == {"operator", "qa"}
    assert mention_result["items"][0]["message_id"] == "msg-1"


def test_channel_projection_normalizes_member_role_contract_and_owner_reports(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.member.invited",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "member_id": "codex-techlead-1",
            "persona": "Codex TechLead",
            "member_type": "provider_agent",
            "provider": "codex",
            "backend": "codex",
            "channel_role": "tech_leader",
            "visibility_profile": "planner",
            "role_context_ref": "channel_roles/tech-leader.md",
            "workflow_role_binding": {"role": "arch", "instance_id": "arch-1"},
            "permissions": ["read", "message", "summarize", "propose_workflow"],
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.owner_report.generated",
        actor="boss-agent",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "owner_id": "boss:min",
            "report_id": "owner-report-1",
            "summary": "progress is healthy",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.automation_report.ingested",
        actor="automation",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "report_id": "auto-report-1",
            "automation_id": "daily-brief",
            "summary": "no blockers",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    member = detail["members"][0]
    assert member["member_type"] == "provider_agent"
    assert member["provider"] == "codex"
    assert member["channel_role"] == "tech_leader"
    assert member["visibility_profile"] == "planner"
    assert member["workflow_role_binding"]["role"] == "arch"
    assert detail["owner_reports"][0]["report_id"] == "owner-report-1"
    assert detail["automation_reports"][0]["automation_id"] == "daily-brief"


def test_channel_projection_records_discussion_mode_and_state_update(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.discussion.mode.set",
        actor="web",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "mode": "fanout_then_synthesis",
            "max_rounds": 4,
            "speaker_policy": {"cap": 6},
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))
    log.append(ZfEvent(
        type="channel.state_update.posted",
        actor="web",
        task_id="TASK-1",
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "status": "workflow_requested",
            "summary": "workflow requested",
            "source": "web",
        },
        correlation_id="ch-zaofu",
    ))

    detail = project_channel(state_dir, "ch-zaofu")

    assert detail is not None
    assert detail["discussion"]["mode"] == "fanout_then_synthesis"
    assert detail["discussion"]["max_rounds"] == 4
    assert detail["state_updates"][0]["status"] == "workflow_requested"


def test_channel_post_instance_id_blocked_for_non_member(tmp_path: Path) -> None:
    # 2026-06-10 review P1-7: a raw instance_id in the post payload used to
    # bypass membership gating and drive any role pane directly.
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.emit("channel.created", payload={"channel_id": "ch-zaofu", "name": "# zaofu"})
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-post-message", "request": {}},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        actor="web",
        source="channel",
        surface="web",
    )

    service.execute(
        action="channel-post-message",
        requested_action="channel.post_message",
        requested=requested,
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-bypass",
            "member_id": "operator",
            "text": "do X",
            "instance_id": "dev-1",
        },
    )

    events = log.read_all()
    assert not [e for e in events if e.type == "worker.reply.requested"]
    blocked = [
        e for e in events
        if e.type == "channel.route.blocked"
        and e.payload.get("reason") == "worker_not_channel_member"
    ]
    assert len(blocked) == 1
    assert blocked[0].payload["instance_id"] == "dev-1"


def test_channel_post_instance_id_allowed_for_backing_worker_member(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.emit("channel.created", payload={"channel_id": "ch-zaofu", "name": "# zaofu"})
    writer.emit(
        "channel.member.added",
        payload={
            "channel_id": "ch-zaofu",
            "member_id": "dev-worker",
            "member_type": "provider-agent",
            "backing_worker_session_id": "dev-1",
        },
    )
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "channel-post-message", "request": {}},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        actor="web",
        source="channel",
        surface="web",
    )

    service.execute(
        action="channel-post-message",
        requested_action="channel.post_message",
        requested=requested,
        payload={
            "channel_id": "ch-zaofu",
            "thread_id": "main",
            "message_id": "msg-member",
            "member_id": "operator",
            "text": "do X",
            "instance_id": "dev-1",
        },
    )

    events = log.read_all()
    reply = [e for e in events if e.type == "worker.reply.requested"]
    assert len(reply) == 1
    assert reply[0].payload["instance_id"] == "dev-1"
    assert not [
        e for e in events
        if e.type == "channel.route.blocked"
        and e.payload.get("reason") == "worker_not_channel_member"
    ]


def test_channel_summary_caches_and_invalidates_on_new_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # A4-1 regression: channel_summary must not re-hydrate + re-project the full
    # channel event log on every request. It materializes the result keyed by
    # the projected seq, so a request with no new events is served from cache
    # (no hydrate_events call), and a new channel event invalidates it.
    import json

    from zf.web.projections import read_model

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": "ch-a", "name": "alpha", "source": "web"},
        correlation_id="ch-a",
    ))

    first = read_model.channel_summary(state_dir)
    assert first is not None
    assert "ch-a" in json.dumps(first, default=str)

    # Second request with no new events: must hit the cache and never hydrate.
    def _boom(*_args, **_kwargs):
        raise AssertionError("channel_summary re-hydrated on a cache hit")

    monkeypatch.setattr(read_model, "hydrate_events", _boom)
    cached = read_model.channel_summary(state_dir)
    assert cached is not None
    assert "ch-a" in json.dumps(cached, default=str)

    # A new channel event advances the projected seq → cache invalidated →
    # recompute (hydrate allowed again) and reflect the new channel.
    monkeypatch.undo()
    log.append(ZfEvent(
        type="channel.created",
        actor="web",
        payload={"channel_id": "ch-b", "name": "beta", "source": "web"},
        correlation_id="ch-b",
    ))
    refreshed = read_model.channel_summary(state_dir)
    assert refreshed is not None
    assert "ch-b" in json.dumps(refreshed, default=str)


def test_channel_summary_caches_empty_result_without_rehydrating(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # A4-1: even a project with zero channels paid the full hydrate cost. The
    # empty result is cached via a sentinel so repeated 0-channel requests are
    # served without touching the event log.
    from zf.web.projections import read_model

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    # A non-channel event so the log/projection is non-empty but has 0 channels.
    log.append(ZfEvent(type="task.created", actor="web", payload={"task_id": "t-1"}))

    assert read_model.channel_summary(state_dir) is None

    def _boom(*_args, **_kwargs):
        raise AssertionError("channel_summary re-hydrated an empty-result cache hit")

    monkeypatch.setattr(read_model, "hydrate_events", _boom)
    assert read_model.channel_summary(state_dir) is None


def test_channel_id_normalized_consistently_across_create_invite_post(
    tmp_path: Path,
) -> None:
    # Root-cause regression: channel-create normalizes the channel_id (adds the
    # `ch-` prefix + slugifies), but invite/post/admin ops used to take it raw.
    # A caller passing the human slug ("streamval-review") to invite/post
    # targeted a different id than create produced ("ch-streamval-review"), and
    # the projection's setdefault materialized a second, phantom channel. All
    # channel_id entry points now normalize, so the slug resolves to the one
    # canonical channel and no phantom appears.
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    service = ControlledActionService(
        state_dir, writer, actor="web", source="channel", surface="web",
    )

    def run(action: str, requested_action: str, payload: dict) -> dict:
        requested = writer.emit(
            "web.action.requested",
            actor="web",
            payload={"action": action, "request": {}},
        )
        return service.execute(
            action=action,
            requested_action=requested_action,
            requested=requested,
            payload=payload,
        )

    created = run(
        "channel-create",
        "channel.create",
        {"name": "Streamval Review", "channel_id": "streamval-review"},
    )
    assert created["channel_id"] == "ch-streamval-review"

    # Invite + post using the *un-prefixed* human slug — must resolve to the same
    # canonical channel, not spawn a phantom "streamval-review".
    invited = run(
        "channel-invite-member",
        "channel.invite_member",
        {
            "channel_id": "streamval-review",
            "member_id": "qa",
            "member_type": "codex",
            "persona": "QA",
        },
    )
    assert invited.get("ok", True) is not False, invited

    run(
        "channel-post-message",
        "channel.post_message",
        {
            "channel_id": "streamval-review",
            "thread_id": "main",
            "message_id": "msg-1",
            "member_id": "operator",
            "text": "hi",
        },
    )

    listing = project_channels(state_dir)
    ids = [c["channel_id"] for c in listing["channels"]]
    assert ids == ["ch-streamval-review"], f"phantom channel materialized: {ids}"

    detail = project_channel(state_dir, "ch-streamval-review")
    assert detail is not None
    assert any(m["member_id"] == "qa" for m in detail["members"])
    assert any(m["message_id"] == "msg-1" for m in detail["messages"])
