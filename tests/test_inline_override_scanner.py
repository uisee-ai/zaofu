"""ZF-LH-INLINE-001 — inline override scanner tests (doc 26 §3.3).

Verifies the pure-function scanner: which user.message events
match, which are filtered out (disabled / wrong type / non-human
actor / agent quote-attack), and the audit payload shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from zf.core.config.schema import WorkflowConfig, WorkflowInlineOverrides
from zf.core.events.model import ZfEvent
from zf.runtime.inline_overrides import (
    InlineOverrideMatch,
    build_audit_payload,
    scan_inline_overrides,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _StubConfig:
    """Minimal stand-in for ZfConfig — scanner reads only
    ``config.workflow.inline_overrides``."""

    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)


def _config_with_patterns(
    enabled: bool = True,
    patterns: dict[str, list[str]] | None = None,
) -> _StubConfig:
    cfg = _StubConfig()
    cfg.workflow.inline_overrides = WorkflowInlineOverrides(
        enabled=enabled,
        patterns=patterns or {
            "skip_critic": ["skip critic", "别走 critic"],
            "skip_test": ["skip test", "别测了"],
            "skip_judge": ["skip judge", "别走 judge"],
        },
    )
    return cfg


def _user_message(
    text: str,
    actor: str = "user",
    payload_key: str = "text",
) -> ZfEvent:
    return ZfEvent(
        type="user.message",
        actor=actor,
        payload={payload_key: text},
    )


# ---------------------------------------------------------------------------
# Short-circuit cases — empty list returned (no work, no audit)
# ---------------------------------------------------------------------------


def test_disabled_feature_returns_empty() -> None:
    cfg = _config_with_patterns(enabled=False)
    event = _user_message("skip critic now please")
    assert scan_inline_overrides(event, cfg) == []


def test_non_user_message_event_returns_empty() -> None:
    cfg = _config_with_patterns()
    event = ZfEvent(
        type="dev.build.done",
        actor="user",
        payload={"text": "skip critic"},
    )
    assert scan_inline_overrides(event, cfg) == []


def test_non_human_actor_returns_empty() -> None:
    """Closes the quote-attack gap: an agent emitting a user.message
    that quotes 'skip critic' must not trigger."""
    cfg = _config_with_patterns()
    event = _user_message("skip critic", actor="dev")
    assert scan_inline_overrides(event, cfg) == []


def test_empty_payload_returns_empty() -> None:
    cfg = _config_with_patterns()
    event = ZfEvent(type="user.message", actor="user", payload={})
    assert scan_inline_overrides(event, cfg) == []


def test_no_pattern_match_returns_empty() -> None:
    cfg = _config_with_patterns()
    event = _user_message("just a normal message, no keywords here")
    assert scan_inline_overrides(event, cfg) == []


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_single_match_ascii() -> None:
    cfg = _config_with_patterns()
    event = _user_message("hey can we skip critic on this one")
    matches = scan_inline_overrides(event, cfg)
    assert matches == [
        InlineOverrideMatch(key="skip_critic", trigger_phrase="skip critic"),
    ]


def test_single_match_cjk() -> None:
    cfg = _config_with_patterns()
    event = _user_message("这次别走 critic 直接上")
    matches = scan_inline_overrides(event, cfg)
    assert matches == [
        InlineOverrideMatch(key="skip_critic", trigger_phrase="别走 critic"),
    ]


def test_case_insensitive_match() -> None:
    cfg = _config_with_patterns()
    event = _user_message("SKIP CRITIC plz")
    matches = scan_inline_overrides(event, cfg)
    assert len(matches) == 1
    assert matches[0].key == "skip_critic"


def test_multiple_keys_match() -> None:
    cfg = _config_with_patterns()
    event = _user_message("skip critic and skip test, hurry")
    matches = scan_inline_overrides(event, cfg)
    keys = sorted(m.key for m in matches)
    assert keys == ["skip_critic", "skip_test"]


def test_only_one_phrase_per_key_recorded() -> None:
    """If multiple phrases under the same key match, record just one
    (so the audit log is unambiguous)."""
    cfg = _config_with_patterns(
        patterns={"skip_critic": ["skip critic", "别走 critic"]},
    )
    event = _user_message("skip critic AND 别走 critic")
    matches = scan_inline_overrides(event, cfg)
    assert len(matches) == 1
    assert matches[0].key == "skip_critic"


def test_zf_cli_actor_also_treated_as_human() -> None:
    """zf-cli (operator CLI emit) is a legitimate human actor."""
    cfg = _config_with_patterns()
    event = _user_message("skip test", actor="zf-cli")
    matches = scan_inline_overrides(event, cfg)
    assert len(matches) == 1


@pytest.mark.parametrize("payload_key", ["text", "message", "content"])
def test_payload_field_fallback_order(payload_key: str) -> None:
    """Scanner reads the first non-empty of text / message / content."""
    cfg = _config_with_patterns()
    event = _user_message("skip judge here", payload_key=payload_key)
    matches = scan_inline_overrides(event, cfg)
    assert len(matches) == 1
    assert matches[0].key == "skip_judge"


# ---------------------------------------------------------------------------
# Audit payload shape
# ---------------------------------------------------------------------------


def test_audit_payload_human_initiated_always_true() -> None:
    """The scanner only produces matches for human actors, so any
    audit payload built from its matches must declare
    human_initiated=true."""
    matches = [InlineOverrideMatch(key="skip_critic", trigger_phrase="skip critic")]
    event = _user_message("skip critic")
    payload = build_audit_payload(event, matches)
    assert payload["human_initiated"] is True


def test_audit_payload_includes_source_event_metadata() -> None:
    matches = [InlineOverrideMatch(key="skip_test", trigger_phrase="skip test")]
    event = _user_message("skip test")
    payload = build_audit_payload(event, matches)
    assert payload["source_event_id"] == event.id
    assert payload["source_event_type"] == "user.message"
    assert payload["source_actor"] == "user"
    assert payload["matched_keys"] == ["skip_test"]
    assert payload["trigger_phrases"] == ["skip test"]


# ---------------------------------------------------------------------------
# Wire-up grep proof: Orchestrator._scan_inline_overrides exists and
# is invoked from _react_to_events.
# ---------------------------------------------------------------------------


def test_orchestrator_has_scan_inline_overrides_method() -> None:
    """Wire-up: scan_inline_overrides must be invoked from runtime,
    not just live as a library function (Class D anti-pattern)."""
    from zf.runtime.orchestrator import Orchestrator

    assert hasattr(Orchestrator, "_scan_inline_overrides"), (
        "Orchestrator missing _scan_inline_overrides method"
    )


def test_react_to_events_calls_scan_inline_overrides() -> None:
    """Source-level grep proof that _react_to_events invokes the
    scan."""
    import inspect

    from zf.runtime.orchestrator import Orchestrator

    source = inspect.getsource(Orchestrator._react_to_events)
    assert "_scan_inline_overrides" in source, (
        "_react_to_events does not invoke _scan_inline_overrides"
    )


def test_workflow_inline_override_in_known_event_types() -> None:
    """Audit event type must be registered or EventWriter rejects it."""
    from zf.core.events.known_types import KNOWN_EVENT_TYPES

    assert "workflow.inline_override" in KNOWN_EVENT_TYPES
