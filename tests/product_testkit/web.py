"""Web projection assertions shared by generated product projects."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from tests.testkit.web_truth import assert_sse_event_once, assert_status_convergence


def assert_web_truth(*, canonical_status: str, api_status: str | None,
                     dom_status: str | None, refreshed_status: str | None) -> None:
    """Require API, DOM and refreshed DOM to agree with the canonical state."""

    assert_status_convergence(canonical_status, {
        "api": api_status, "dom": dom_status, "refresh": refreshed_status,
    })


def assert_sse_delivery_once(event_id: str, messages: Iterable[Mapping[str, Any]]) -> None:
    assert_sse_event_once(event_id, messages)

