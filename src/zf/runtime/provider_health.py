"""Read-only provider health projection.

Provider health is derived from events.jsonl. It is not a control plane and
does not decide dispatch truth; it only makes backend/account failures
observable for Web, CLI, and autoresearch.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj, redact_text
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.provider_stop import classify_provider_stop


_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(token|secret|password|api[_-]?key|authorization|credential|refresh)"
)
_HOME_AUTH_PATH_RE = re.compile(
    r"(?:(?:/home/[^/\s]+)|~)/(?:\.(?:claude|codex|config|cache)[^\s,;\"']*)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")


def project_provider_health(
    state_dir: Path,
    *,
    events: list[ZfEvent] | None = None,
) -> dict[str, Any]:
    events = events if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    records: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type not in {
            "agent.api_blocked",
            "agent.timeout",
            "provider.stop.recovery",
            "provider.health.changed",
            "provider.cooldown.started",
            "provider.fallback.selected",
            "provider.account.exhausted",
        }:
            continue
        backend = _payload_str(payload, "backend") or "unknown"
        role = _payload_str(payload, "role") or _payload_str(payload, "assigned_to")
        instance_id = _payload_str(payload, "instance_id") or event.actor or ""
        account_alias = (
            _payload_str(payload, "account_alias")
            or _payload_str(payload, "profile")
            or _payload_str(payload, "provider_profile")
        )
        key = _provider_key(backend, account_alias, role, instance_id)
        record = records.setdefault(key, {
            "key": key,
            "backend": backend,
            "account_alias": account_alias,
            "role": role,
            "instance_id": instance_id,
            "status": "unknown",
            "reason": "",
            "action": "",
            "requires_operator": False,
            "cooldown_until": "",
            "last_event_id": "",
            "last_event_type": "",
            "last_event_at": "",
            "task_id": "",
            "dispatch_id": "",
            "diagnostics": {},
            "fallback": {},
        })
        reason = _provider_reason(event, payload)
        action = _payload_str(payload, "action")
        status = _status_for_event(event.type, payload, reason, action)
        record.update({
            "backend": backend,
            "account_alias": account_alias,
            "role": role or record.get("role", ""),
            "instance_id": instance_id or record.get("instance_id", ""),
            "status": status,
            "reason": reason,
            "action": action,
            "requires_operator": _requires_operator(reason, action, event.type),
            "last_event_id": event.id,
            "last_event_type": event.type,
            "last_event_at": event.ts,
            "task_id": event.task_id or _payload_str(payload, "task_id"),
            "dispatch_id": _payload_str(payload, "dispatch_id"),
            "diagnostics": _safe_diagnostics(payload),
        })
        cooldown_until = _payload_str(payload, "cooldown_until")
        if cooldown_until:
            record["cooldown_until"] = cooldown_until
        if event.type == "provider.fallback.selected":
            record["fallback"] = _safe_diagnostics({
                "selected_backend": payload.get("selected_backend"),
                "selected_account": payload.get("selected_account"),
                "reason": payload.get("reason"),
            })

    providers = sorted(records.values(), key=lambda item: item.get("key", ""))
    status = "healthy"
    if any(item.get("status") in {"blocked", "exhausted"} for item in providers):
        status = "blocked"
    elif any(item.get("status") in {"degraded", "cooldown"} for item in providers):
        status = "degraded"
    return {
        "schema_version": "provider-health.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "providers": providers,
    }


def write_provider_health_projection(state_dir: Path, projection: dict[str, Any]) -> Path:
    path = state_dir / "projections" / "provider_health.json"
    atomic_write_text(path, json.dumps(projection, ensure_ascii=False, indent=2) + "\n")
    return path


def _provider_key(backend: str, account_alias: str, role: str, instance_id: str) -> str:
    parts = [backend or "unknown", account_alias or "default", role or "unknown", instance_id or "unknown"]
    return "|".join(parts)


def _provider_reason(event: ZfEvent, payload: dict[str, Any]) -> str:
    reason = (
        _payload_str(payload, "reason")
        or _payload_str(payload, "provider_stop_reason")
        or _payload_str(payload, "stop_reason")
    )
    if reason:
        return reason
    return classify_provider_stop(payload, status=event.type)


def _status_for_event(event_type: str, payload: dict[str, Any], reason: str, action: str) -> str:
    explicit = _payload_str(payload, "status")
    if explicit:
        return explicit
    if event_type == "provider.account.exhausted":
        return "exhausted"
    if event_type == "provider.cooldown.started" or action == "cooldown":
        return "cooldown"
    if action == "suspend" or reason in {"auth_error", "tool_permission_blocked", "hook_review_required"}:
        return "blocked"
    if event_type == "provider.health.changed" and reason in {"recovered", "healthy"}:
        return "healthy"
    return "degraded"


def _requires_operator(reason: str, action: str, event_type: str) -> bool:
    return (
        event_type == "provider.account.exhausted"
        or action == "suspend"
        or reason in {
            "auth_error",
            "tool_permission_blocked",
            "hook_review_required",
            "missing_token",
            "missing_token_env",
        }
        or "is not set" in reason
    )


def _safe_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if _SENSITIVE_KEY_RE.search(str(key)):
            continue
        out[str(key)] = _redact_provider_value(value)
    redacted = redact_obj(out)
    return redacted if isinstance(redacted, dict) else {}


def _redact_provider_value(value: Any) -> Any:
    if isinstance(value, str):
        text = _BEARER_RE.sub("Bearer [REDACTED_TOKEN]", redact_text(value))
        return _HOME_AUTH_PATH_RE.sub("[REDACTED_AUTH_PATH]", text)
    if isinstance(value, dict):
        return _safe_diagnostics(value)
    if isinstance(value, list):
        return [_redact_provider_value(item) for item in value]
    return value


def _payload_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if value is not None else ""
