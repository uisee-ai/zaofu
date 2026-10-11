"""Read-only child activity adapters for Kanban WebChat."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


_TERMINAL_SUFFIXES = (".completed", ".done", ".passed", ".failed", ".cancelled", ".rejected")


def _value(event: Any, name: str, default: Any = "") -> Any:
    return event.get(name, default) if isinstance(event, Mapping) else getattr(event, name, default)


def _payload(event: Any) -> dict[str, Any]:
    raw = _value(event, "payload", {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def _activity_kind(event_type: str, payload: Mapping[str, Any]) -> str:
    if payload.get("channel_id") or event_type.startswith("channel."):
        return "channel_member"
    if payload.get("task_id") or event_type.startswith(("task.", "worker.", "dispatch.")):
        return "delivery_worker"
    return "ephemeral_child"


def _identity(event_type: str, payload: Mapping[str, Any]) -> str:
    for key in (
        "child_id",
        "member_id",
        "role_instance",
        "worker_id",
        "operation_id",
        "attempt_id",
        "run_id",
    ):
        value = str(payload.get(key) or "")
        if value:
            return value
    return f"activity:{event_type}"


def _has_child_identity(event_type: str, payload: Mapping[str, Any]) -> bool:
    """Do not classify the root Kanban turn as a child just because it has a role."""

    # Headless Kanban runs carry member_id=kanban-agent for attribution, but
    # they are the root conversation, not a child worker activity.
    if (
        event_type.startswith("agent.session.")
        and str(payload.get("source") or "").strip() == "kanban-agent.headless"
    ):
        return False
    if any(str(payload.get(key) or "").strip() for key in (
        "child_id", "member_id", "worker_id", "operation_id", "attempt_id",
    )):
        return True
    if event_type.startswith("kanban.agent.turn."):
        return False
    return bool(str(payload.get("role_instance") or "").strip())


_CONTROL_OPERATIONS = frozenset({"queue", "steer", "interrupt", "cancel"})


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _control_descriptors(
    kind: str,
    payload: Mapping[str, Any],
    *,
    identity: str,
    task_id: str,
) -> list[dict[str, Any]]:
    """Translate host-declared capabilities; never infer controls in the browser."""

    declared = set(_strings(payload.get("control_capabilities")))
    allowed_actions = set(_strings(payload.get("allowed_actions")))
    descriptors: list[dict[str, Any]] = []
    if kind == "delivery_worker":
        if "worker-reply" in allowed_actions:
            declared.add("steer")
        if "worker-drain" in allowed_actions:
            declared.add("cancel")
        for operation, action in (("queue", "worker-reply"), ("steer", "worker-reply"), ("cancel", "worker-drain")):
            if operation not in declared or action not in allowed_actions:
                continue
            descriptors.append({
                "operation": operation,
                "route": "controlled_action",
                "action": action,
                "payload": {"instance_id": identity, "task_id": task_id},
                "requires_message": operation in {"queue", "steer"},
            })
    control_turn_id = str(payload.get("control_turn_id") or "")
    if control_turn_id:
        for operation in sorted(declared & (_CONTROL_OPERATIONS - {"queue"})):
            descriptors.append({
                "operation": operation,
                "route": "webchat_turn",
                "target_id": control_turn_id,
                "requires_message": operation in {"queue", "steer", "interrupt"},
            })
    control_conversation_id = str(payload.get("control_conversation_id") or "")
    control_thread_id = str(payload.get("control_thread_id") or payload.get("thread_id") or "")
    if "queue" in declared and control_conversation_id and control_thread_id:
        descriptors.append({
            "operation": "queue",
            "route": "webchat_queue",
            "payload": {
                "conversation_id": control_conversation_id,
                "thread_id": control_thread_id,
                "source_turn_id": control_turn_id,
            },
            "requires_message": True,
        })
    return descriptors


def _age_seconds(value: str, *, now: datetime) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())


def build_child_activity_projection(
    events: Iterable[Any],
    *,
    parent_turn_id: str = "",
    task_status: Mapping[str, str] | None = None,
    now: datetime | None = None,
    stale_after_seconds: float = 300.0,
) -> dict[str, Any]:
    """Fold observed activity without inferring canonical completion."""

    task_status = task_status or {}
    now = now or datetime.now(timezone.utc)
    activities: dict[str, dict[str, Any]] = {}
    for event in events:
        event_type = str(_value(event, "type"))
        payload = _payload(event)
        parent = str(payload.get("parent_turn_id") or payload.get("parent_run_id") or "")
        if parent_turn_id and parent != parent_turn_id:
            continue
        if not _has_child_identity(event_type, payload):
            continue
        identity = _identity(event_type, payload)
        kind = _activity_kind(event_type, payload)
        task_id = str(payload.get("task_id") or _value(event, "task_id") or "").strip()
        controls = _control_descriptors(
            kind, payload, identity=identity, task_id=task_id,
        )
        navigation = []
        if task_id:
            navigation.append("open_task")
        if str(payload.get("channel_id") or ""):
            navigation.append("open_channel")
        if str(payload.get("thread_id") or ""):
            navigation.append("open_thread")
        item = activities.setdefault(identity, {
            "activity_id": identity,
            "kind": kind,
            "parent_turn_id": parent,
            "task_id": task_id,
            "channel_id": str(payload.get("channel_id") or ""),
            "thread_id": str(payload.get("thread_id") or ""),
            "conversation_id": str(payload.get("conversation_id") or ""),
            "role": str(payload.get("role") or payload.get("role_instance") or ""),
            "provider": str(payload.get("provider") or payload.get("backend") or ""),
            "model": str(payload.get("model") or ""),
            "permission_profile": str(payload.get("permission_profile") or ""),
            "status": "active",
            "started_at": str(_value(event, "ts") or payload.get("created_at") or ""),
            "updated_at": "",
            "heartbeat_at": "",
            "current_stage": "",
            "tool_label": "",
            "reasoning_label": "",
            "usage": {},
            "cost": {},
            "artifact_refs": [],
            "terminal_evidence": [],
            "capabilities": [*navigation, *(item["operation"] for item in controls)],
            "controls": controls,
            "source_event_ids": [],
        })
        item["source_event_ids"].append(str(_value(event, "id")))
        item["updated_at"] = str(_value(event, "ts") or payload.get("updated_at") or item["updated_at"])
        if "heartbeat" in event_type:
            item["heartbeat_at"] = item["updated_at"]
        item["current_stage"] = str(payload.get("stage_id") or payload.get("phase") or item["current_stage"])
        item["tool_label"] = str(payload.get("tool_label") or payload.get("tool") or item["tool_label"])
        item["reasoning_label"] = str(payload.get("reasoning_label") or item["reasoning_label"])
        if isinstance(payload.get("usage"), Mapping):
            item["usage"] = dict(payload["usage"])
        if isinstance(payload.get("cost"), Mapping):
            item["cost"] = dict(payload["cost"])
        if controls:
            existing = {str(control.get("operation")) for control in item["controls"]}
            item["controls"].extend(
                control for control in controls
                if str(control.get("operation")) not in existing
            )
            item["capabilities"] = list(dict.fromkeys([
                *item["capabilities"], *(control["operation"] for control in controls),
            ]))
        refs = payload.get("artifact_refs") or payload.get("artifacts") or []
        if isinstance(refs, list):
            for ref in refs:
                raw_ref = ref.get("ref") if isinstance(ref, Mapping) else ref
                value = str(raw_ref or "")
                if value and value not in item["artifact_refs"]:
                    item["artifact_refs"].append(value)
        if event_type.endswith(_TERMINAL_SUFFIXES):
            item["status"] = "failed" if event_type.endswith((".failed", ".rejected")) else "terminal"
            item["terminal_evidence"].append({
                "event_id": str(_value(event, "id")),
                "event_type": event_type,
                "status": str(payload.get("status") or ""),
                "ref": str(payload.get("evidence_ref") or payload.get("artifact_ref") or ""),
            })
        elif event_type.endswith(".unavailable") or payload.get("provider_available") is False:
            item["status"] = "unavailable"

    for item in activities.values():
        task_id = str(item.get("task_id") or "")
        if item["kind"] == "delivery_worker" and item["status"] == "terminal":
            canonical = str(task_status.get(task_id) or "")
            if canonical not in {"done", "completed"}:
                item["why_not_done"] = "Worker terminal is not Task completion; discriminator/gates remain authoritative."
            else:
                item["why_not_done"] = ""
        elif item["status"] == "unavailable":
            item["why_not_done"] = "Provider is unavailable; inspect configuration or use a declared recovery action."
        elif item["status"] == "active":
            heartbeat_age = _age_seconds(
                str(item.get("heartbeat_at") or item.get("updated_at") or ""), now=now,
            )
            if heartbeat_age is not None and heartbeat_age > stale_after_seconds:
                item["status"] = "stale"
                item["why_not_done"] = "Heartbeat is stale; inspect the provider or use a declared recovery action."
            else:
                item["why_not_done"] = "No host-observed terminal evidence."
        else:
            item["why_not_done"] = ""
    return {
        "schema_version": "webchat-child-activity.v1",
        "is_derived_projection": True,
        "parent_turn_id": parent_turn_id,
        "items": sorted(activities.values(), key=lambda item: str(item["updated_at"]), reverse=True),
    }


__all__ = ["build_child_activity_projection"]
