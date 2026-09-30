"""Structured observability evidence checks for product projects."""

from __future__ import annotations

from collections.abc import Mapping


REQUIRED_LOG_FIELDS = ("timestamp", "level", "message")
SENSITIVE_NAMES = {"token", "password", "secret", "api_key", "authorization"}


def validate_log_record(record: Mapping[str, object]) -> None:
    missing = [field for field in REQUIRED_LOG_FIELDS if not record.get(field)]
    assert not missing, f"log record missing fields: {missing}"


def assert_redacted(record: Mapping[str, object]) -> None:
    leaked = [key for key, value in record.items() if key.lower() in SENSITIVE_NAMES and value not in (None, "", "[REDACTED]")]
    assert not leaked, f"sensitive fields are not redacted: {leaked}"

