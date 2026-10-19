"""Complete durable handoff intents that crashed before reply materialization."""

from datetime import datetime, timezone
from typing import Any

from zf.core.events import ZfEvent
from zf.runtime.channel_handoff import request_channel_handoff
from zf.runtime.channel_projection import project_channel


def resume_unstarted_channel_handoffs(
    runtime: Any, events: list[ZfEvent], *, now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    resumed = 0
    for event in events:
        if event.type != "channel.handoff.requested":
            continue
        try:
            if (now - datetime.fromisoformat(event.ts.replace("Z", "+00:00"))).total_seconds() < 60:
                continue
        except ValueError:
            continue
        p = event.payload
        channel_id, thread_id = p["channel_id"], p.get("thread_id") or "main"
        related = {event.id, *(e.id for e in events
                              if e.type == "channel.handoff.accepted" and e.causation_id == event.id)}
        if any(e.type in {"channel.handoff.failed", "channel.handoff.rejected"}
               and e.causation_id in related for e in events):
            continue
        channel = project_channel(runtime.state_dir, channel_id) or {}
        session = (channel.get("discussions") or {}).get(thread_id) or {}
        if (not channel or channel.get("status") in {"closed", "archived", "deleted"}
                or session.get("state") == "closed"):
            continue
        if any(r.get("message_id") == p["message_id"]
               and r.get("target_member_id") == p["target_member_id"]
               for r in channel.get("reply_requests") or []):
            continue
        result = request_channel_handoff(
            state_dir=runtime.state_dir, writer=runtime.event_writer,
            channel_id=channel_id, thread_id=thread_id, message_id=p["message_id"],
            member_id=p["member_id"], target_member_id=p["target_member_id"],
            reason=p.get("reason") or "", actor="orchestrator-remediation",
            source="runtime:handoff-resume", depth=int(p.get("depth") or 0),
            round_no=int(p.get("round") or 0), max_depth=int(p.get("max_depth", 3)),
            max_rounds=int(p.get("max_rounds", 6)),
            project_root=getattr(runtime, "project_root", None),
            config=getattr(runtime, "config", None), resume_requested=event,
            dispatch_inline=False,
        )
        resumed += bool(result.reply_requests)
        if resumed >= 8:
            break
    return resumed
