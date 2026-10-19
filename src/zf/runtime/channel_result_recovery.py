"""Resume contract application after a durable provider completion, without rerunning it."""
from datetime import datetime, timezone
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.channel_dispatch_claim import ChannelDispatchResult, with_channel_reply_dispatch_claim
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_provider_completion import _expected_channel_contract_key
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_reply_remediation import emit_channel_reply_failed
from zf.runtime.channel_sidecar import hydrate_channel_message_text
from zf.runtime.sidecar_refs import SidecarRefError


def resume_unapplied_channel_results(
    runtime: Any, events: list[ZfEvent], *, now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    applied = {event.causation_id for event in events if event.type.startswith("channel.")}
    resumed = 0
    for event in events:
        if event.type != "channel.agent.reply.completed" or event.id in applied:
            continue
        age = (now - datetime.fromisoformat(event.ts.replace("Z", "+00:00"))).total_seconds()
        if not 60 <= age <= 86400:
            continue
        result = _resume_one_result(runtime=runtime, event=event, state_dir=runtime.state_dir,
                                    channel_id=event.payload["channel_id"], request_id=event.payload["request_id"])
        resumed += bool(result.dispatched)
        if resumed >= 8:
            break
    return resumed


@with_channel_reply_dispatch_claim
def _resume_one_result(*, runtime: Any, event: ZfEvent, state_dir: Any,
                       channel_id: str, request_id: str) -> ChannelDispatchResult:
    events = runtime.event_log.read_all()
    if any(e.causation_id == event.id and e.type.startswith("channel.") for e in events):
        return ChannelDispatchResult()
    by_id = {e.id: e for e in events}
    p = event.payload
    channel = project_channel(runtime.state_dir, p["channel_id"]) or {}
    thread = p.get("thread_id") or "main"
    if channel.get("status") in {"closed", "archived", "deleted"} or (
        ((channel.get("discussions") or {}).get(thread) or {}).get("state") == "closed"
    ):
        return ChannelDispatchResult()
    request = next((r for r in channel.get("reply_requests") or []
                    if r.get("request_id") == p["request_id"]), {})
    if request.get("status") != "completed" or int(request.get("run_generation") or 1) != int(p.get("run_generation") or 1):
        return ChannelDispatchResult()
    source = next((m for m in channel.get("messages") or []
                   if m.get("message_id") == request.get("message_id")), {})
    if not source or not _expected_channel_contract_key(channel, request, source):
        return ChannelDispatchResult()
    body = by_id.get(event.causation_id)
    try:
        if body is None or body.type != "channel.message.posted":
            return ChannelDispatchResult()  # Historical completion without a body binding is not replayable.
        reply = hydrate_channel_message_text(runtime.state_dir, body.payload, strict=True)
    except SidecarRefError as exc:
        emit_channel_reply_failed(writer=runtime.event_writer, request=request,
            request_id=p["request_id"], started_event_id=event.id, actor="orchestrator-remediation",
            source="runtime:result-resume", channel_id=p["channel_id"], reason=f"result body unreadable: {exc.code}",
            provider_session_id=p.get("provider_session_id") or "", failure_status="result_unreadable")
    else:
        emit_structured_reply_events(state_dir=runtime.state_dir, writer=runtime.event_writer,
            channel=channel, request=request, message=source, reply=reply, reply_event_id=event.id,
            actor="orchestrator-remediation", source="runtime:result-resume")
    return ChannelDispatchResult(dispatched=[request_id])
