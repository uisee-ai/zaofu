"""Unit tests for the Web truth-chain assertions."""

from __future__ import annotations

import pytest

from tests.testkit.web_truth import assert_sse_event_once, assert_status_convergence


def test_status_convergence_accepts_matching_projections() -> None:
    assert_status_convergence(
        "in_progress",
        {"api": "in_progress", "dom": "in_progress", "reload": "in_progress"},
    )


def test_status_convergence_rejects_missing_or_stale_projection() -> None:
    with pytest.raises(AssertionError, match="does not converge"):
        assert_status_convergence(
            "in_progress", {"api": "done", "dom": None}
        )


def test_sse_event_once_accepts_json_data_message() -> None:
    assert_sse_event_once(
        "evt-1",
        [{"data": '{"id":"evt-1"}'}, {"data": '{"id":"evt-2"}'}],
    )


@pytest.mark.parametrize(
    "messages",
    [
        [{"data": '{"id":"evt-1"}'}, {"data": '{"id":"evt-1"}'}],
        [{"data": "not-json"}],
    ],
)
def test_sse_event_once_rejects_duplicate_or_malformed_messages(messages) -> None:  # noqa: ANN001
    with pytest.raises(AssertionError, match="expected once"):
        assert_sse_event_once("evt-1", messages)
