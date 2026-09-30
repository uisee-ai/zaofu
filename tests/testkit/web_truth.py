"""Assertions joining canonical status, API/DOM projections and SSE evidence."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


def assert_status_convergence(
    canonical_status: str,
    observed: Mapping[str, str | None],
) -> None:
    """Require every projection to expose the canonical status exactly."""
    if not observed:
        raise AssertionError("status convergence requires at least one projection")
    missing = [name for name, value in observed.items() if value is None]
    mismatched = {
        name: value
        for name, value in observed.items()
        if value is not None and value != canonical_status
    }
    if missing or mismatched:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if mismatched:
            details.append(f"mismatched={mismatched}")
        raise AssertionError(
            f"web truth does not converge on canonical status {canonical_status!r}: "
            + ", ".join(details)
        )


def _event_id(message: Mapping[str, Any]) -> str | None:
    data = message.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return None
    return str(data.get("id")) if isinstance(data, Mapping) and data.get("id") else None


def assert_sse_event_once(
    event_id: str,
    messages: Iterable[Mapping[str, Any]],
) -> None:
    """Require one, and only one, decodable SSE message for ``event_id``."""
    count = sum(1 for message in messages if _event_id(message) == event_id)
    if count != 1:
        raise AssertionError(f"SSE event {event_id!r} expected once, observed {count}")
