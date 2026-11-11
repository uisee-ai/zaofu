"""Cancel a concrete PRD result without closing the Channel or dropping run ownership."""
from zf.core.state.locks import locked_path
from zf.runtime.channel_owner_authority import channel_owner_authority_error
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_synthesis_lock import channel_synthesis_lock_path
from zf.runtime.control_actions_helpers import _normal_channel_id


def cancel_channel_synthesis(service, *, requested, action, requested_action, payload):
    cid = _normal_channel_id(str(payload.get("channel_id") or ""))
    tid = str(payload.get("thread_id") or "main")
    rid = str(payload.get("request_id") or "")
    with locked_path(channel_synthesis_lock_path(service.state_dir, cid, tid)):
        channel = project_channel(service.state_dir, cid) or {}
        error = channel_owner_authority_error(channel, actor=service.actor, capability="cancel_synthesis")
        current = next((r for r in reversed(channel.get("synthesis_requests", []))
                        if r.get("request_id") == rid and r.get("thread_id") == tid), None)
        if error or not current:
            return service._failed(requested=requested, action=action, requested_action=requested_action,
                                   task_id=requested.task_id, status_code=403 if error else 404,
                                   reason=error or "找不到本次生成请求")
        if current.get("status") not in {"requested", "repair_requested", "cancelled"}:
            return service._failed(requested=requested, action=action, requested_action=requested_action,
                                   task_id=requested.task_id, status_code=409, reason="本次生成已经结束，无需停止")
        event = requested
        if current.get("status") != "cancelled":
            event = service.writer.emit("channel.synthesis.cancelled", actor=service.actor, causation_id=requested.id,
                                        correlation_id=cid, payload={"channel_id":cid,"thread_id":tid,"request_id":rid,
                                            "status":"cancelled","reason":"用户停止本次生成，保留聊天和既有PRD版本","source":service.surface})
        for reply in channel.get("reply_requests", []):
            if reply.get("status") not in {"pending", "queued"}:
                continue
            message = next((m for m in channel.get("messages", []) if m.get("message_id") == reply.get("message_id")), {})
            if (message.get("refs") or {}).get("synthesis_request_id") == rid:
                service.writer.emit("channel.agent.reply.failed", actor=service.actor, causation_id=event.id, correlation_id=cid,
                                    payload={**reply,"channel_id":cid,"status":"failed","failure_status":"user_cancelled",
                                             "reason":"用户在执行前停止本次生成","source":service.surface})
        service._completed(requested=requested,event=event,action=action,requested_action=requested_action,
                           task_id=requested.task_id,status="cancelled",extra={"channel_id":cid,"thread_id":tid,"request_id":rid})
        return {"ok":True,"_status_code":202,"status":"cancelled","channel_id":cid,"thread_id":tid,"request_id":rid,
                "reason":"已停止采用本次结果；正在执行的Provider仍由原运行记录管理"}
