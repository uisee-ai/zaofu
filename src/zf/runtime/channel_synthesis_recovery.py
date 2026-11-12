"""Owner-controlled abandonment of an unknown, stopped synthesis attempt.

This fences publication; it does not claim that the provider never received
the input or that remote inference/billing has stopped.
"""
from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from zf.core.state.locks import locked_path
from zf.runtime.channel_dispatch_claim import channel_reply_dispatch_claim_path
from zf.runtime.channel_owner_authority import channel_owner_authority_error
from zf.runtime.channel_synthesis_lock import channel_synthesis_lock_path
from zf.runtime.control_actions_helpers import _normal_channel_id


def bound_synthesis_replies(channel: dict[str, Any], request_id: str, thread_id: str) -> list[dict[str, Any]]:
    messages = channel.get("messages") or []
    if isinstance(messages, dict):
        messages = messages.values()
    message_ids = {str(m.get("message_id") or "") for m in messages
                   if str(m.get("thread_id") or "main") == thread_id
                   and (m.get("refs") or {}).get("synthesis_request_id") == request_id}
    replies = channel.get("reply_requests") or []
    if isinstance(replies, dict):
        replies = replies.values()
    return [r for r in replies if str(r.get("thread_id") or "main") == thread_id
            and str(r.get("message_id") or "") in message_ids]


def recovery_required_requests(channel: dict[str, Any]) -> set[tuple[str, str]]:
    """Compute the UI hint in one pass, without reading provider sidecars."""
    messages = channel.get("messages") or {}
    replies = channel.get("reply_requests") or {}
    if isinstance(messages, dict):
        messages = messages.values()
    if isinstance(replies, dict):
        replies = replies.values()
    unknown = {(str(r.get("thread_id") or "main"), str(r.get("message_id") or ""))
               for r in replies if r.get("status") == "failed"
               and r.get("failure_status") == "delivery_unknown"
               and r.get("terminal_disposition") != "abandoned"}
    return {(str(m.get("thread_id") or "main"), str((m.get("refs") or {}).get("synthesis_request_id")))
            for m in messages if (str(m.get("thread_id") or "main"), str(m.get("message_id") or "")) in unknown
            and (m.get("refs") or {}).get("synthesis_request_id")}


def apply_synthesis_recovery(channel: dict[str, Any], event: Any) -> None:
    """Replay one atomic recovery receipt without rewriting failure evidence."""
    for binding in event.payload.get("replies") or []:
        reply = channel["reply_requests"].get(str(binding.get("request_id") or ""))
        if (reply and reply.get("status") == "failed"
                and reply.get("run_id") == binding.get("run_id")
                and int(reply.get("run_generation") or 1) == int(binding.get("run_generation") or 1)):
            reply.update(terminal_disposition="abandoned", retryable=False,
                         recovery_event_id=event.id)


def recover_channel_synthesis(service, *, requested, action, requested_action, payload):
    from zf.runtime.channel_projection import project_channel

    cid = _normal_channel_id(str(payload.get("channel_id") or ""))
    tid = str(payload.get("thread_id") or "main")
    rid = str(payload.get("synthesis_request_id") or "")

    def fail(reason, *, status="recovery_blocked", code=409):
        return service._failed(requested=requested, action=action, requested_action=requested_action,
                               task_id=requested.task_id, reason=reason, status_code=code, status=status)

    if not cid or not rid:
        return fail("channel_id and synthesis_request_id are required", code=422)
    with locked_path(channel_synthesis_lock_path(service.state_dir, cid, tid)):
        channel = project_channel(service.state_dir, cid) or {}
        error = channel_owner_authority_error(channel, actor=service.actor, capability="recover_synthesis")
        if error:
            return fail(error, code=403)
        events = service.writer.event_log.read_all()
        previous = next((e for e in reversed(events) if e.type == "channel.synthesis.recovered"
                         and e.payload.get("channel_id") == cid
                         and str(e.payload.get("thread_id") or "main") == tid
                         and e.payload.get("request_id") == rid), None)
        if previous:
            return {"ok": True, "_status_code": 200, "status": "already_recovered", "event_id": previous.id}
        scoped = [r for r in channel.get("synthesis_requests") or []
                  if str(r.get("thread_id") or "main") == tid]
        if not scoped or scoped[-1].get("request_id") != rid:
            return fail("生成请求已变化，请刷新后检查原运行", status="stale_request")
        if any(e.type == "channel.synthesis.proposed" and e.payload.get("channel_id") == cid
               and e.payload.get("request_id") == rid
               and str(e.payload.get("thread_id") or "main") == tid for e in events):
            return fail("本次请求已经发布 PRD，不能作为未交付请求收尾")
        replies = bound_synthesis_replies(channel, rid, tid)
        unknown = [r for r in replies if r.get("status") == "failed"
                   and r.get("failure_status") == "delivery_unknown"
                   and r.get("terminal_disposition") != "abandoned"]
        if not unknown:
            return fail("没有需要收尾的未知交付请求")
        if any(r.get("status") in {"pending", "queued", "running", "started"} for r in replies):
            return fail("原生成仍有执行中的回复，请先等待或停止原运行", status="provider_still_running")
        bindings = []
        for reply in unknown:
            terminal = next((e for e in reversed(events)
                             if e.type in {"agent.session.run.started", "agent.session.run.completed",
                                           "agent.session.run.failed", "agent.session.run.cancelled"}
                             and e.payload.get("channel_id") == cid
                             and e.payload.get("request_id") == reply["request_id"]
                             and e.payload.get("run_id") == reply.get("run_id")
                             and int(e.payload.get("run_generation") or 1) == int(reply.get("run_generation") or 1)), None)
            if terminal is None or terminal.type not in {"agent.session.run.failed", "agent.session.run.cancelled"}:
                return fail("缺少同一运行及代次的结束记录，不能安全恢复；请检查原运行")
            bindings.append({"request_id": reply["request_id"], "run_id": reply["run_id"],
                             "run_generation": int(reply.get("run_generation") or 1),
                             "failure_event_id": reply["event_id"], "terminal_event_id": terminal.id})
        # The dispatcher holds this claim for its entire lifetime, including
        # provider cleanup and publication. Never wait while holding the PRD
        # lock: that dispatcher may itself be waiting to publish under it.
        with ExitStack() as stack:
            try:
                for reply in replies:
                    stack.enter_context(locked_path(channel_reply_dispatch_claim_path(
                        service.state_dir, cid, reply["request_id"]), timeout_seconds=0.05))
            except TimeoutError:
                return fail("原执行仍持有运行权，请稍后检查；尚未解除保护", status="provider_still_running")
            event = service.writer.emit("channel.synthesis.recovered", actor=service.actor,
                                        causation_id=requested.id, correlation_id=cid,
                                        payload={"channel_id": cid, "thread_id": tid, "request_id": rid,
                                                 "status": "cancelled", "disposition": "abandoned",
                                                 "reason": "用户结束失败请求；不再采用旧结果，可手动重新生成",
                                                 "replies": bindings, "source": service.surface})
        service._completed(requested=requested, event=event, action=action, requested_action=requested_action,
                           task_id=requested.task_id, status="recovered",
                           extra={"channel_id": cid, "thread_id": tid, "request_id": rid})
        return {"ok": True, "_status_code": 200, "status": "recovered", "event_id": event.id,
                "reason": "失败请求已结束；聊天、原材料和旧 PRD 保留，请手动选择重新生成"}
