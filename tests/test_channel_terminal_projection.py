"""Terminal authority regressions; see docs/impl/channel-terminal-projection.md."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.core.events import EventLog, ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_reply_remediation import fold_channel_reply_states
from zf.web.server import create_app


def seed_channel(tmp_path: Path):
    state = tmp_path / "runtime-state"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    sequence = 0

    def append(event_type: str, **overrides):
        nonlocal sequence
        sequence += 1
        event = ZfEvent(
            type=event_type, actor="test", ts=f"2026-10-16T01:00:{sequence:02d}+00:00",
            correlation_id="ch-terminal",
            payload={
                "channel_id": "ch-terminal", "thread_id": "main",
                "request_id": "reply-1", "run_id": "run-1", "run_generation": 1,
                "message_id": "message-1", "target_member_id": "arch",
                "member_id": "arch", "provider": "codex", **overrides,
            },
        )
        log.append(event)
        return event

    append("channel.created", name="Terminal regression")
    append("channel.member.invited", member_type="provider_agent")
    append("channel.agent.reply.requested")
    append("channel.agent.reply.started")
    append("agent.session.run.started")
    append("agent.session.part.delta", part_id="thinking", kind="thinking", delta="Checking", seq=1)
    return state, append


@pytest.mark.parametrize("terminal", ["failed", "completed"])
@pytest.mark.parametrize("late_delta", [False, True])
def test_terminal_reply_settles_session_presence_and_parts(tmp_path, terminal, late_delta):
    state, append = seed_channel(tmp_path)
    ended = append(f"channel.agent.reply.{terminal}", reason="superseded", retryable=False)
    if late_delta:
        append("agent.session.part.delta", part_id="thinking", kind="thinking", delta="Late", seq=2)
    before = (state / "events.jsonl").read_bytes()

    detail = project_channel(state, "ch-terminal")

    assert detail is not None
    assert detail["running_replies"] == []
    for key in ("provider_runs", "agent_session_runs"):
        run = detail[key][0]
        assert run["status"] == terminal
        assert run.get("live_status", terminal) == terminal
        assert run["active_part_id"] == ""
        assert run["ended_at"] == ended.ts
        assert run["updated_at"] == ended.ts
        assert not any(p["state"] in {"streaming", "submitted", "delta"} for p in run["parts"])
    member = detail["members"][0]
    assert member["presence"] == ("failed" if terminal == "failed" else "ready")
    assert member["active_request_id"] == ""
    assert (state / "events.jsonl").read_bytes() == before


def test_new_request_generation_does_not_inherit_old_stream_or_failure(tmp_path):
    state, append = seed_channel(tmp_path)
    append("channel.agent.reply.failed", reason="timeout")
    append("channel.agent.reply.requested", run_generation=2)

    queued = project_channel(state, "ch-terminal")
    assert queued["provider_runs"][0]["status"] == "pending"
    assert queued["provider_runs"][0]["run_generation"] == 2
    assert queued["agent_session_runs"] == []
    assert queued["members"][0]["presence"] == "pending"

    append("channel.agent.reply.started", run_generation=2)
    append("agent.session.run.started", run_generation=2)
    started = project_channel(state, "ch-terminal")
    assert started["provider_runs"][0]["status"] == "streaming"
    assert started["members"][0]["presence"] == "streaming"
    assert not any(p.get("content") == "Checking" for p in started["agent_session_runs"][0]["parts"])
    assert not started["agent_session_runs"][0].get("ended_at")

    append("agent.session.part.delta", run_generation=2, part_id="current", kind="text", delta="New")
    append("agent.session.part.delta", part_id="thinking", kind="thinking", delta="Old")
    append("agent.session.run.failed", reason="old process finally exited")
    current = project_channel(state, "ch-terminal")["agent_session_runs"][0]
    assert current["run_generation"] == 2
    assert current["status"] == "streaming"
    assert any(p.get("content") == "New" for p in current["parts"])
    assert not any(p.get("content") == "Old" for p in current["parts"])


@pytest.mark.parametrize("late_type", ["channel.agent.reply.started", "channel.agent.reply.completed"])
def test_same_generation_cannot_reopen_terminal_reply(tmp_path, late_type):
    state, append = seed_channel(tmp_path)
    failed = append("channel.agent.reply.failed", reason="superseded")
    append(late_type)
    detail = project_channel(state, "ch-terminal")
    assert detail["reply_requests"][0]["status"] == "failed"
    assert detail["reply_requests"][0]["ended_at"] == failed.ts
    assert detail["provider_runs"][0]["live_status"] == "failed"
    folded = fold_channel_reply_states(EventLog(state / "events.jsonl").read_all())
    assert folded[("ch-terminal", "reply-1")]["status"] == "failed"


def test_contract_rejection_after_transport_completion_still_drives_recovery(tmp_path):
    from tests.test_channel_adapter_parallel_dispatch import CH, _seed
    from zf.runtime.channel_contribution_repair import reject_contribution_contract
    from zf.runtime.channel_reply_remediation import channel_reply_remediation_candidates

    state, writer = _seed(tmp_path, members=1, backend="codex")
    request = project_channel(state, CH)["reply_requests"][0]
    completed = writer.emit("channel.agent.reply.completed", actor="test", payload={
        **request, "channel_id": CH,
    })
    reject_contribution_contract(
        writer=writer, channel_id=CH, thread_id="main", member_id="m-0", request=request,
        reply="invalid structured result", reply_event_id=completed.id,
        actor="test", source="test", status="invalid_channel_contribution", reason="missing questions",
    )

    detail = project_channel(state, CH)
    reply = detail["reply_requests"][0]
    assert reply["status"] == "failed"
    assert reply["failure_status"] == "contract_invalid"
    assert reply["retryable"] is True
    events = writer.event_log.read_all()
    assert fold_channel_reply_states(events)[(CH, "req-0")]["status"] == "failed"
    assert channel_reply_remediation_candidates(events)[0]["kind"] == "redispatch"


def test_new_request_is_not_stopped_by_an_older_request_for_same_member(tmp_path):
    state, append = seed_channel(tmp_path)
    append("channel.agent.reply.failed", reason="superseded")
    other = {"request_id": "reply-2", "run_id": "run-2", "message_id": "message-2"}
    append("channel.agent.reply.requested", **other)
    append("channel.agent.reply.started", **other)
    append("agent.session.run.started", **other)
    detail = project_channel(state, "ch-terminal")
    runs = {run["run_id"]: run for run in detail["provider_runs"]}
    assert runs["run-1"]["status"] == "failed"
    assert runs["run-1"]["live_status"] == "failed"
    assert runs["run-2"]["status"] == "streaming"
    assert detail["members"][0]["active_request_id"] == "reply-2"


@pytest.mark.parametrize("suffix", ["", "/conversation"])
def test_channel_http_projection_settles_superseded_run_without_session_terminal(tmp_path, suffix):
    state, append = seed_channel(tmp_path)
    TaskStore(state / "kanban.json")._save_raw([])
    # Match the incident: cleanup copied the old pending payload into a failed event.
    append("channel.agent.reply.failed", status="pending", reason="superseded by final PRD revision r3", retryable=False)
    before = (state / "events.jsonl").read_bytes()
    client = TestClient(create_app(state, project_root=tmp_path))
    response = client.get(f"/api/projects/default/channels/ch-terminal{suffix}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["reply_requests"][0]["status"] == "failed"
    assert detail["provider_runs"][0]["live_status"] == "failed"
    assert detail["members"][0]["presence"] == "failed"
    assert (state / "events.jsonl").read_bytes() == before
