"""Turnkey channel reply turn (feishu B4-core, doc 98 §9).

Replaces the Feishu sidecar's canned echo with the REAL channel agent reply path:
an inbound channel message is routed (route_channel_message → emits
channel.agent.reply.requested) and each reply request is dispatched
(dispatch_reply_request → runs the member's backend → emits the agent's
channel.message.posted + reply lifecycle). With a fake/persona backend the reply
is deterministic; with claude-code/codex it is a real LLM answer that streams
part.delta (consumed by stream_card B1-B3). No reply text is synthesized here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_router import route_channel_message


def run_channel_reply_turn(
    state_dir: Path,
    writer: EventWriter,
    config: Any | None,
    *,
    message_event: ZfEvent,
    message_payload: dict[str, Any],
    actor: str = "feishu-bridge",
    source: str = "feishu",
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Route one inbound channel message and dispatch every resulting reply.

    Returns {route, dispatched: [(request_id, result)]}. The actual reply content
    is produced by the member's backend through the real path — this only wires
    route → dispatch (no synthesized echo)."""
    route = route_channel_message(
        state_dir=state_dir,
        writer=writer,
        message_event=message_event,
        message_payload=message_payload,
        actor=actor,
        source=source,
        config=config,
        project_root=project_root,
    )
    channel_id = str(message_payload.get("channel_id") or "")
    dispatched: list[tuple[str, Any]] = []
    for request_id in route.reply_requests:
        result = dispatch_reply_request(
            state_dir=state_dir,
            writer=writer,
            channel_id=channel_id,
            request_id=request_id,
            actor=actor,
            source=source,
            config=config,
            project_root=project_root,
        )
        dispatched.append((request_id, result))
    return {"route": route, "dispatched": dispatched}
