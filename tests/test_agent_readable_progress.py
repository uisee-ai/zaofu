"""Public commentary snapshots remain readable after live and durable replay."""
from zf.core.events import EventLog, EventWriter
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_conversation_projection import project_channel_conversation
from zf.web.headless_agent import HeadlessMessage


def emitter(tmp_path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    writer.emit("channel.created", actor="web", payload={"channel_id": "ch"})
    stream = AgentSessionStreamEmitter(writer=writer, identity=AgentSessionIdentity(run_id="run", thread_id="main",
                source="channel.headless", actor="runtime", channel_id="ch", target_member_id="arch"), flush_interval_s=999)
    stream.start()
    return writer, stream


def test_commentary_deltas_coalesce_into_one_readable_part(tmp_path):
    writer, stream = emitter(tmp_path)
    message = "正在核对身份隔离和恢复规则。"
    for char in message:
        stream.emit_message(HeadlessMessage(type="status", content=char, raw={
            "method": "item/agentMessage/delta", "params": {"itemId": "native-commentary"}}))
    stream.complete()
    events = [e for e in writer.event_log.read_all() if e.type == "agent.session.part.delta"]
    assert len(events) == 2  # First update plus a coalesced complete snapshot.
    assert len({e.payload["part_id"] for e in events}) == 1
    run = project_channel(tmp_path, "ch")["provider_runs"][0]
    parts = [p for p in run["parts"] if p["part_id"].startswith("commentary-")]
    assert len(parts) == 1 and parts[0]["content"] == message


def test_completed_stage_snapshot_replaces_started_stage_and_keeps_receipt(tmp_path):
    writer, stream = emitter(tmp_path)
    activity = {"kind": "synthesis", "id": "one", "title": "保存需求映射", "state": "started", "completed": 0}
    stream.emit_message(HeadlessMessage(type="status", content=activity["title"], raw={"activity": activity}))
    activity = {**activity, "state": "completed", "completed": 1, "checkpoint": {"ref": "bound", "sha256": "hash"}}
    stream.emit_message(HeadlessMessage(type="status", content=activity["title"], raw={"activity": activity}))
    run = project_channel(tmp_path, "ch")["provider_runs"][0]
    parts = [p for p in run["parts"] if p["part_id"].startswith("activity-")]
    assert len(parts) == 1 and parts[0]["content"] == "保存需求映射"
    assert parts[0]["refs"]["raw"]["activity"]["checkpoint"]["sha256"] == "hash"


def test_paged_conversation_keeps_bounded_readable_history_without_native_payloads(tmp_path):
    writer, stream = emitter(tmp_path)
    writer.emit("channel.message.posted", actor="web", payload={"channel_id": "ch", "message_id": "user", "role": "user", "text": "整理需求"})
    writer.emit("channel.agent.reply.requested", actor="runtime", payload={"channel_id": "ch", "thread_id": "main", "message_id": "user",
        "request_id": "reply", "target_member_id": "arch", "run_id": "run"})
    activity = {"kind": "synthesis", "id": "saved", "index": 0, "title": "已保存文稿", "state": "completed", "completed": 1, "total": 5,
        "checkpoint": {"ref": "internal-checkpoint", "sha256": "audit-digest"}}
    stream.emit_message(HeadlessMessage(type="status", content=activity["title"], raw={"activity": activity, "private_native_payload": "must-not-project"}))
    for index in range(32):
        stream.emit_message(HeadlessMessage(type="tool_use", content="读取文件", tool="read", raw={"private_native_payload": "must-not-project", "item": index}))
    stream.complete()
    writer.emit("channel.agent.reply.completed", actor="runtime", payload={"channel_id": "ch", "request_id": "reply", "target_member_id": "arch", "run_id": "run"})
    page = project_channel_conversation(tmp_path, "ch")
    run = page["provider_runs"][0]
    phases = [p for p in run["parts"] if p.get("refs", {}).get("raw", {}).get("activity")]
    assert len(phases) == 1
    assert phases[0]["refs"]["raw"]["activity"]["completed"] == 1
    assert len(run["parts"]) <= 25
    assert "must-not-project" not in str(page) and "internal-checkpoint" not in str(run)


def test_kanban_ephemeral_bridge_sends_whole_public_commentary_snapshots(tmp_path):
    from zf.core.events.model import ZfEvent
    from zf.runtime.live_delta_bus import LiveDeltaBus
    from zf.web.headless_turn_observability import HeadlessDeltaEmitter
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    emitter = HeadlessDeltaEmitter(writer=writer, task_id=None, turn_started=ZfEvent(type="kanban.agent.turn.started", actor="web"),
        user_message=ZfEvent(type="user.message", actor="web"), turn_id="turn", thread_key="main", project_id="p", conversation_id="c", backend="codex", flush_interval_s=999)
    text = "正在只读核对输出和重连记录。"
    for char in text:
        emitter.emit(HeadlessMessage(type="status", content=char, raw={"method": "item/agentMessage/delta", "params": {"itemId": "note"}}))
    emitter.flush()
    rows, _ = LiveDeltaBus(tmp_path).read_since()
    assert len(rows) == 2
    assert rows[-1].payload["content"] == text
    assert len({row.payload["part_id"] for row in rows}) == 1
    assert rows[-1].payload["refs"]["raw"]["public_commentary"]["snapshot"] is True
