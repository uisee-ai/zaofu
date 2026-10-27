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


def test_memoized_projection_redaction_preserves_context_and_scopes_its_cache():
    shared = {"body": "ordinary text", "password": "private", "label": "TOKEN=secret"}
    values = [shared, shared]
    source = {"normal": values, "attachments": values, "other": (shared, True, None, 3),
              "requires_token": True, "token": values}
    expected = redact_obj(source)
    actual = redact_obj(source, memoize=True)
    assert actual == expected
    assert actual["normal"][0]["body"] == "ordinary text"
    assert actual["attachments"][0]["body"] == "[REDACTED_ATTACHMENT_BODY]"
    assert source["normal"][0]["password"] == "private"
    shared["body"] = "updated"
    assert redact_obj(source, memoize=True)["normal"][0]["body"] == "updated"
    default = redact_obj(source)
    assert default["normal"][0] is not default["normal"][1]


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
