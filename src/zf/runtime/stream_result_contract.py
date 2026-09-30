"""Mechanical completion evidence for a provider stream, not task acceptance."""

from enum import Enum
from typing import Any


class DrainStatus(str, Enum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    INCOMPLETE = "incomplete_result"
    RESULT_ERROR = "result_error"
    CANCELLED = "cancelled"


def is_result_message(message: Any) -> bool:
    return type(message).__name__ == "ResultMessage" or all(
        hasattr(message, field) for field in ("session_id", "total_cost_usd", "usage")
    )


def completion_status(messages: list[Any], status: DrainStatus) -> DrainStatus:
    if status != DrainStatus.OK:
        return status
    results = [message for message in messages if is_result_message(message)]
    if len(results) != 1 or results[0] is not messages[-1]:
        return DrainStatus.INCOMPLETE
    result = results[0]
    if (getattr(result, "is_error", None) is not False
            or getattr(result, "subtype", None) != "success"):
        return DrainStatus.RESULT_ERROR
    return DrainStatus.OK


def failure_reason(status: DrainStatus) -> str:
    return {
        DrainStatus.RATE_LIMITED: "rate_limit_event interrupted provider turn",
        DrainStatus.INCOMPLETE: "provider stream lacks one complete terminal result",
        DrainStatus.RESULT_ERROR: "provider returned an unsuccessful result",
        DrainStatus.CANCELLED: "provider turn cancelled by transport stop",
    }[status]
