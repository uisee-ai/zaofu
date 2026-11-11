"""Creation defaults and explicit opt-in to legacy discussion startup."""
from zf.runtime.channel_contracts import normalize_product_discussion_mode
from zf.runtime.channel_templates import materialize_channel_template
from zf.runtime.channel_provider_preflight import normalize_channel_dispatch_policy
from zf.runtime.control_actions_helpers import _task_id_from_payload


def template_creation_overrides(payload):
    raw = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
    mode = (normalize_product_discussion_mode(payload["mode"]) if payload.get("mode")
            else raw.get("discussion_mode") or "libre_discuss")
    return {**raw, "discussion_mode": mode}


def prepare_legacy_start(service, *, requested, action, requested_action, payload):
    def fail(reason, status="invalid_payload"):
        return service._failed(requested=requested,action=action,requested_action=requested_action,
                               task_id=_task_id_from_payload(payload),status_code=422,status=status,reason=reason)
    overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
    if payload.get("mode") == "libre_discuss" or overrides.get("discussion_mode") == "libre_discuss":
        return payload, "", fail("自由讨论仅创建群，请进入后发送消息；不能创建并自动开始讨论")
    if not payload.get("mode"):
        template, _ = materialize_channel_template(str(payload.get("template_id") or ""), overrides=payload.get("overrides"))
        payload = {**payload,"mode":(template or {}).get("discussion",{}).get("mode","conversation")}
    try:
        dispatch = normalize_channel_dispatch_policy(payload.get("dispatch_policy"))
    except ValueError as exc:
        return payload, "", fail(str(exc),"invalid_dispatch_policy")
    return payload, dispatch, None
