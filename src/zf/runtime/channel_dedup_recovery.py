"""Resume question consolidation requests lost before reply materialization."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_question_gate import question_dedup_gate_state
from zf.runtime.channel_synthesis_reactor import react_channel_question_dedup_requested


def resume_unstarted_question_dedup(
    runtime: Any, events: list[ZfEvent], *, now: datetime | None = None,
) -> int:
    """Replay the same request after a grace period; never mint another generation.

    The reactor reuses an existing message and the normal router owns reply
    admission. Failed/started replies remain owned by reply remediation.
    """
    now = now or datetime.now(timezone.utc)
    requests = {event.id: event for event in events
                if event.type == "channel.question.dedup.requested"}
    channels = {str(event.payload.get("channel_id") or "") for event in requests.values()}
    resumed = 0
    for channel_id in sorted(channels - {""}):
        channel = project_channel(runtime.state_dir, channel_id) or {}
        latest = {str(item.get("thread_id") or "main"): item
                  for item in channel.get("question_dedup_requests") or []}
        for thread_id, request in latest.items():
            if resumed >= 8:
                return resumed
            session = (channel.get("discussions") or {}).get(thread_id) or {}
            event = requests.get(str(request.get("event_id") or ""))
            if (event is None or request.get("status") != "requested"
                    or session.get("state") != "phase2_relay"
                    or question_dedup_gate_state(channel, thread_id=thread_id) != "consolidating"
                    or str(request.get("ts") or "") < str(session.get("started_at") or "")):
                continue
            try:
                requested_at = datetime.fromisoformat(event.ts.replace("Z", "+00:00"))
                if (now - requested_at).total_seconds() < 60:
                    continue
            except (TypeError, ValueError):
                continue
            message_ids = {str(message.get("message_id") or "")
                           for message in channel.get("messages") or []
                           if (message.get("refs") or {}).get("question_dedup_request_id")
                           == request.get("request_id")}
            if any(reply.get("message_id") in message_ids
                   for reply in channel.get("reply_requests") or []):
                continue
            react_channel_question_dedup_requested(runtime, event)
            resumed += 1
    return resumed
