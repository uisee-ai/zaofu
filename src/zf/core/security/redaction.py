"""Redaction helpers for diagnostics and Web projections."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from zf.core.events.model import ZfEvent


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
_API_KEY_RE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b")
_ENV_SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]*)"
    r"(\s*[:=]\s*)"
    r"([^\s,;\"']+)"
)
_SENSITIVE_KEYS = {
    "api_key",
    "app_secret",
    "auth",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "cookies",
    "headers",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "tenant_access_token",
    "token",
}


def _sensitive_key(key: object) -> bool:
    text = str(key).lower().replace("-", "_")
    return (
        text in _SENSITIVE_KEYS
        or text.endswith("_token")
        or text.endswith("_secret")
        or text.endswith("_password")
        or text.endswith("_api_key")
        or text.endswith("_private_key")
    )


def redact_text(text: str) -> str:
    redacted = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", text)
    redacted = _JWT_RE.sub("[REDACTED_JWT]", redacted)
    redacted = _API_KEY_RE.sub("[REDACTED_API_KEY]", redacted)
    redacted = _ENV_SECRET_RE.sub(r"\1\2[REDACTED_SECRET]", redacted)
    return redacted


def redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: _redact_dict_value(k, v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_obj(v) for v in value)
    return value


def _redact_dict_value(key: object, value: Any) -> Any:
    if not _sensitive_key(key):
        return redact_obj(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return "[REDACTED_SECRET]"


def redact_event(event: ZfEvent) -> ZfEvent:
    return replace(event, payload=redact_obj(event.payload))
