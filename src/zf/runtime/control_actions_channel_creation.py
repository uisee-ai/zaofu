"""Direct Channel creation with immutable identity and idempotent retries."""
from __future__ import annotations

import hashlib
import json

from zf.core.events import ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.channel_owner_authority import normalize_owner_delegates
from zf.runtime.control_actions_helpers import (
    _normal_channel_id, _optional_str, _required_text, _stable_control_id,
    _task_id_from_payload,
)


class ChannelCreationActionsMixin:
    def _channel_create(
        self, *, requested: ZfEvent, action: str, requested_action: str, payload: dict,
    ) -> dict:
        name = _required_text(payload, "name") or _required_text(payload, "channel_name")
        channel_id = _normal_channel_id(payload.get("channel_id") or payload.get("id") or name)
        request_digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        idempotency_key = str(payload.get("idempotency_key") or requested.id)
        with locked_path(self.state_dir / "locks" / _stable_control_id("channel-create", channel_id)):
            prior = next((
                event for event in self.writer.event_log.read_all()
                if event.type == "channel.created"
                and event.payload.get("channel_id") == channel_id
            ), None)
            if prior is not None:
                if (
                    prior.actor == self.actor
                    and prior.payload.get("creation_idempotency_key") == idempotency_key
                    and prior.payload.get("creation_request_digest") == request_digest
                ):
                    return {
                        "_status_code": 202, "ok": True, "status": "existing",
                        "action": action, "requested_action": requested_action,
                        "channel_id": channel_id, "name": prior.payload["name"],
                        "event_id": prior.id, "reason": "channel creation already accepted",
                    }
                return self._failed(
                    requested=requested, action=action, requested_action=requested_action,
                    task_id=_task_id_from_payload(payload), status_code=409, status="conflict",
                    reason="channel_id already exists", extra={"channel_id": channel_id},
                )
            return self._channel_create_new(
                requested=requested, action=action, requested_action=requested_action,
                payload=payload,
            )

    def _channel_create_new(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        name = _required_text(payload, "name") or _required_text(payload, "channel_name")
        channel_id = _normal_channel_id(payload.get("channel_id") or payload.get("id") or name)
        event = self.writer.emit(
            "channel.created",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=channel_id,
            payload={
                "channel_id": channel_id,
                "name": name,
                "channel_name": name,
                "discussion_mode": str(payload.get("mode") or "libre_discuss"),
                "creation_idempotency_key": str(payload.get("idempotency_key") or requested.id),
                "creation_request_digest": hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
                "thread_id": _optional_str(payload.get("thread_id")) or "main",
                "task_id": str(payload.get("task_id") or ""),
                "created_by": str(payload.get("created_by") or self.actor),
                "owner_actor_ref": str(
                    payload.get("owner_actor_ref") or self.actor
                ),
                "owner_delegates": normalize_owner_delegates(
                    payload.get("owner_delegates")
                ),
                "control_anchor_policy": (
                    "strict"
                    if bool(payload.get("strict_control_anchors"))
                    else "legacy"
                ),
                "leader_member_id": str(
                    payload.get("leader_member_id") or ""
                ),
                "default_responder_id": str(
                    payload.get("default_responder_id") or ""
                ),
                "leader_revision": int(payload.get("leader_revision") or 0),
                "origin_binding": (
                    payload.get("origin_binding")
                    if isinstance(payload.get("origin_binding"), dict)
                    else {
                        "surface": self.surface,
                        "channel_id": channel_id,
                        "thread_id": (
                            _optional_str(payload.get("thread_id")) or "main"
                        ),
                    }
                ),
                "source": self.surface,
                "scope": payload.get("scope") if isinstance(payload.get("scope"), dict) else {},
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="created",
            task_id=_task_id_from_payload(payload),
            extra={"channel_id": channel_id, "name": name},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "created",
            "action": action,
            "requested_action": requested_action,
            "reason": "channel created",
            "channel_id": channel_id,
            "name": name,
            "event_id": event.id,
        }
