"""Fail-closed text sanitation for externally disclosable Self-Issue fields."""

from __future__ import annotations

import re
from typing import Any

from zf.core.security.redaction import redact_text

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PERSONAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users)/[^\s,;]+")


def safe_report_text(value: str, *, preserve_personal_paths: bool = False) -> str:
    redacted = redact_text(value)
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", redacted)
    if not preserve_personal_paths:
        redacted = _PERSONAL_PATH_RE.sub("[REDACTED_PERSONAL_PATH]", redacted)
    return redacted


def safe_export_obj(value: Any) -> Any:
    if isinstance(value, str):
        return safe_report_text(value)
    if isinstance(value, dict):
        return {
            str(key): (
                _safe_location_value(item)
                if str(key).casefold() in {"location", "locations"}
                else safe_export_obj(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_export_obj(item) for item in value]
    return value


def _safe_location_value(value: Any) -> Any:
    """Keep concrete source locations while retaining secret redaction.

    Locations are evidence provenance, not user prose. Replacing an absolute
    path with a placeholder makes a published report impossible to audit, so
    only credential-like material is redacted in this field.
    """
    if isinstance(value, str):
        return safe_report_text(value, preserve_personal_paths=True)
    if isinstance(value, dict):
        return {str(key): _safe_location_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_location_value(item) for item in value]
    return value
