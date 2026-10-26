"""Operator request for advisory evaluation of one canonical Channel PRD."""

from __future__ import annotations

import uuid

from zf.core.events import ZfEvent
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_owner_authority import channel_owner_authority_error
from zf.runtime.control_actions_helpers import _normal_channel_id, _optional_str, _required_text


class ChannelEvaluationActionsMixin:
    def _channel_prd_evaluate(self, *, requested: ZfEvent, action: str,
                              requested_action: str, payload: dict) -> dict:
        channel_id = _normal_channel_id(_required_text(payload, "channel_id"))
        thread_id = _optional_str(payload.get("thread_id")) or "main"
        role = _optional_str(payload.get("role")) or "full"
        if role not in {"full", "focused"}:
            return self._failed(requested=requested, action=action,
                                requested_action=requested_action, task_id=None,
                                reason="role must be full or focused", status="invalid_payload")
        channel = project_channel(self.state_dir, channel_id) or {}
        owner = str(channel.get("owner_actor_ref") or "")
        effective_actor = ("owner:operator" if self.surface == "web" and owner == "owner:operator"
                           else self.actor)
        authority_error = channel_owner_authority_error(
            channel, actor=effective_actor, capability="evaluate_prd")
        if not owner or authority_error:
            return self._failed(requested=requested, action=action,
                                requested_action=requested_action, task_id=None,
                                reason=authority_error or "Channel Owner binding is missing",
                                status_code=403, status="forbidden")
        prd = next((row for row in reversed(channel.get("syntheses") or [])
                    if str(row.get("thread_id") or "main") == thread_id
                    and row.get("artifact_ref") and row.get("artifact_digest")), None)
        if not prd:
            return self._failed(requested=requested, action=action,
                                requested_action=requested_action, task_id=None,
                                reason="canonical PRD is unavailable", status_code=409,
                                status="prd_unavailable")
        revision = int(prd.get("prd_revision") or 0)
        digest = str(prd.get("artifact_digest") or "").removeprefix("sha256:")
        expected_revision = payload.get("prd_revision")
        expected_digest = str(payload.get("artifact_digest") or "").removeprefix("sha256:")
        if (expected_revision != revision or expected_digest != digest or
            not str(payload.get("artifact_ref") or "") == str(prd.get("artifact_ref") or "")):
            return self._failed(requested=requested, action=action,
                                requested_action=requested_action, task_id=None,
                                reason="PRD version changed; refresh preview", status_code=409,
                                status="prd_stale")
        identity = {"channel_id": channel_id, "thread_id": thread_id,
                    "prd_revision": revision, "artifact_ref": prd["artifact_ref"],
                    "artifact_digest": digest, "role": role}
        events = self.writer.event_log.read_all()
        prior = next((event for event in reversed(events)
                      if event.type == "channel.prd_evaluation.requested"
                      and all((event.payload or {}).get(key) == value
                              for key, value in identity.items())), None)
        if prior:
            from zf.runtime.channel_prd_evaluation_recovery import fail_interrupted_evaluation

            fail_interrupted_evaluation(self.state_dir, self.writer, prior)
            events = self.writer.event_log.read_all()
            terminal = next((event for event in reversed(events)
                             if event.type in {"output.evaluation.completed", "output.evaluation.failed"}
                             and (event.payload or {}).get("request_id") == (prior.payload or {}).get("request_id")), None)
            if terminal is None or (terminal.type == "output.evaluation.completed" and payload.get("retry") is not True):
                return {"_status_code": 202, "ok": True, "status": "existing",
                        "action": action, "requested_action": requested_action,
                        "request_id": prior.payload["request_id"], **identity}
            if payload.get("retry") is not True:
                return self._failed(requested=requested, action=action,
                                    requested_action=requested_action, task_id=None,
                                    reason="previous evaluation failed; explicitly retry", status_code=409,
                                    status="retry_required")
        request_id = str(uuid.uuid4())
        event = self.writer.emit("channel.prd_evaluation.requested", actor=self.actor,
                                 causation_id=requested.id, correlation_id=channel_id,
                                 payload={"request_id": request_id, **identity,
                                          "source": self.surface})
        self._completed(requested=requested, event=event, action=action,
                        requested_action=requested_action, status="requested", task_id=None,
                        extra={"request_id": request_id, **identity})
        return {"_status_code": 202, "ok": True, "status": "requested",
                "action": action, "requested_action": requested_action,
                "request_id": request_id, "event_id": event.id, **identity}
