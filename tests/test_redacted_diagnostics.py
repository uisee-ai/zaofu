"""Tests for redaction and diagnostics collector."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.security.redaction import redact_obj, redact_text
from zf.core.trace import DiagnosticsCollector


def test_redact_text_covers_api_keys_jwt_private_keys_and_env_secrets():
    jwt = "aaaaaaaaaa.bbbbbbbbbb.cccccccccc"
    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"
        "super-secret\n"
        "-----END PRIVATE KEY-----"
    )
    text = (
        "sk-abcdefghijklmnopqrstuvwxyz "
        f"JWT={jwt} "
        f"{private_key} "
        "OPENAI_API_KEY=plainsecret "
        "PASSWORD=hunter2"
    )

    redacted = redact_text(text)

    assert "sk-abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "plainsecret" not in redacted
    assert jwt not in redacted
    assert "super-secret" not in redacted
    assert "hunter2" not in redacted
    assert "[REDACTED_API_KEY]" in redacted
    assert "[REDACTED_JWT]" in redacted
    assert "[REDACTED_PRIVATE_KEY]" in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_redact_obj_recurses_nested_structures():
    data = {
        "tool": {
            "output": ["TOKEN=abc123", {"password": "PASSWORD=abc123"}],
        },
    }

    redacted = redact_obj(data)

    assert "abc123" not in json.dumps(redacted)
    assert "[REDACTED_SECRET]" in json.dumps(redacted)


def test_redact_obj_preserves_non_secret_token_capability_flags():
    redacted = redact_obj({
        "requires_token": True,
        "token": "secret-token-value",
        "nested": {"refresh_token": None},
    })

    assert redacted["requires_token"] is True
    assert redacted["token"] == "[REDACTED_SECRET]"
    assert redacted["nested"]["refresh_token"] is None


def test_diagnostics_collector_writes_redacted_jsonl(tmp_path: Path):
    collector = DiagnosticsCollector(tmp_path / ".zf", "trace/one")

    path = collector.write_error({
        "message": "API_KEY=secret-value",
        "nested": {"token": "TOKEN=abc123"},
    })

    assert path.name == "errors.jsonl"
    assert "trace_one" in str(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["trace_id"] == "trace/one"
    assert "secret-value" not in json.dumps(data)
    assert "abc123" not in json.dumps(data)
