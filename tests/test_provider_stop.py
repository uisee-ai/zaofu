"""Only provider failures, not arbitrary task prose, choose recovery policy."""

import pytest

from zf.runtime.provider_stop import classify_provider_stop, provider_stop_action


@pytest.mark.parametrize("message", [
    "author metadata saved", "expected assertion: connection refused",
    "permission test completed", "rate limit documentation written",
])
def test_successful_terminal_prose_does_not_suspend_or_retry(message):
    reason = classify_provider_stop({"message": message, "terminal_event": True})
    assert reason == "completed_with_terminal_event"
    assert provider_stop_action(reason) == "observe"


def test_free_form_message_is_not_an_error_channel():
    assert classify_provider_stop({"message": "author reviewed permissions"}) == "completed_without_terminal_event"


@pytest.mark.parametrize("code,expected", [
    ("rate_limit_exceeded", "rate_limited"), ("authentication_error", "auth_error"),
    ("context_length_exceeded", "context_limit"), ("cyber_policy", "provider_policy_rejected"),
])
def test_provider_error_codes_remain_actionable(code, expected):
    assert classify_provider_stop({"error": {"codex_error_info": code}}) == expected


def test_explicit_stop_reason_is_not_reinterpreted_from_message():
    assert classify_provider_stop({"stop_reason": "timeout", "message": "author metadata"}) == "timeout"
