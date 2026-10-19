"""Queue identity and bounded recovery for durable Channel review requests."""

from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.runtime.channel_run_owner import provider_run_fields_for_request


def reply_coalescing_key(channel: dict[str, Any], request: dict[str, Any]) -> tuple[str, ...]:
    """Every accepted input owns work; coalesce only an identical request."""
    return ("request", str(request.get("request_id") or ""),
            str(request.get("target_member_id") or ""), str(request.get("thread_id") or "main"))


def current_review_reply_bindings(
    events: list[ZfEvent], *, status: str = "requested",
) -> dict[tuple[str, str], ZfEvent]:
    """Join provider replies to still-required current-pass semantic reviews.

    This only identifies work. The existing remediation writer owns generation,
    age, retry caps and the durable re-arm; cancellation remains terminal.
    """
    messages: dict[tuple[str, str], dict] = {}
    reviews: dict[tuple[str, str], tuple[int, ZfEvent]] = {}
    requests: dict[tuple[str, str], dict] = {}
    pass_start: dict[tuple[str, str], int] = {}
    closed_channels: set[str] = set()
    closed_threads: set[tuple[str, str]] = set()
    inactive_members: set[tuple[str, str]] = set()
    for index, event in enumerate(events):
        p = event.payload or {}
        channel = str(p.get("channel_id") or "")
        thread = str(p.get("thread_id") or "main")
        scope = (channel, thread)
        if event.type in {"channel.closed", "channel.archived", "channel.deleted"}:
            closed_channels.add(channel)
        elif event.type == "channel.message.posted":
            messages[(channel, str(p.get("message_id") or ""))] = p
        elif event.type == "channel.agent.reply.requested":
            key = (channel, str(p.get("request_id") or ""))
            requests[key] = {**requests.get(key, {}), **p}
        elif event.type.startswith("channel.cross_review."):
            reviews[(channel, str(p.get("request_id") or ""))] = (index, event)
        elif event.type in {"channel.discussion.started", "channel.discussion.continued"}:
            pass_start[scope] = index
            closed_threads.discard(scope)
        elif event.type == "channel.discussion.closed":
            closed_threads.add(scope)
        elif event.type in {"channel.member.removed", "channel.member.suspended"}:
            inactive_members.add((channel, str(p.get("member_id") or "")))
        elif event.type in {"channel.member.invited", "channel.member.added", "channel.member.resumed"}:
            inactive_members.discard((channel, str(p.get("member_id") or "")))
    recoverable = {}
    for key, request in requests.items():
        channel = key[0]
        scope = (channel, str(request.get("thread_id") or "main"))
        member = str(request.get("target_member_id") or "")
        if channel in closed_channels or scope in closed_threads or (channel, member) in inactive_members:
            continue
        message = messages.get((channel, str(request.get("message_id") or "")), {})
        review_id = str((message.get("refs") or {}).get("cross_review_request_id") or "")
        index, review_event = reviews.get((channel, review_id), (-1, None))
        review = review_event.payload if review_event else {}
        if (review_event and review_event.type == f"channel.cross_review.{status}"
                and index >= pass_start.get(scope, -1)
                and str(message.get("thread_id") or "main") == scope[1]
                and review.get("target_member_id") == member
                and str(review.get("thread_id") or "main") == scope[1]):
            recoverable[key] = review_event
    return recoverable


def supersede_queued_replies(
    writer: EventWriter,
    *,
    channel: dict[str, Any],
    channel_id: str,
    target_member_id: str,
    incoming_key: tuple[str, ...],
    actor: str,
    source: str,
    causation_id: str,
    task_id: str | None,
) -> None:
    for item in channel.get("reply_requests") or []:
        if str(item.get("target_member_id") or "") != target_member_id:
            continue
        if str(item.get("status") or "") != "queued":
            continue
        if reply_coalescing_key(channel, item) != incoming_key:
            continue
        writer.emit(
            "channel.agent.reply.failed",
            actor=actor,
            task_id=task_id,
            causation_id=causation_id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "thread_id": str(item.get("thread_id") or "main"),
                "request_id": str(item.get("request_id") or ""),
                "message_id": str(item.get("message_id") or ""),
                "target_member_id": target_member_id,
                "reason": "superseded by latest queued mention",
                **provider_run_fields_for_request(channel_id, item),
                "source": source,
            },
        )
