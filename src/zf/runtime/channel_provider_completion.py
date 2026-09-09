"""Bounded completion handling for Channel provider replies."""

from __future__ import annotations

from dataclasses import replace
import os
from typing import Any, Callable

from zf.runtime.channel_reply_contract import (
    channel_reply_response_contract,
    fake_channel_reply_text,
)
from zf.runtime.channel_reply_parsing import structured_reply_payload_with_error


DEFAULT_CHANNEL_PROVIDER_MAX_CONTINUATIONS = 2


def build_fake_channel_reply(
    member: dict[str, Any],
    message: dict[str, Any],
    context_pack: dict[str, Any],
) -> str:
    contribution_index = _dict_items(context_pack.get("contribution_index"))
    cross_review_index = [
        item
        for item in _dict_items(context_pack.get("cross_review_index"))
        if str(item.get("status") or "") == "completed"
    ]
    contract_sources = [*contribution_index, *cross_review_index]
    return fake_channel_reply_text(
        member,
        message,
        semantic_source_digests=context_pack.get(
            "semantic_source_required_digests"
        ),
        contribution_refs=[
            str(item.get("artifact_ref") or "")
            for item in contract_sources
            if item.get("artifact_ref")
        ],
        contribution_digests=[
            str(item.get("artifact_digest") or "")
            for item in contract_sources
            if item.get("artifact_digest")
        ],
    )


def run_channel_provider_with_continuations(
    *,
    run_turn: Callable[[str, str], Any],
    initial_prompt: str,
    provider_session_id: str,
    channel: dict[str, Any],
    request: dict[str, Any],
    message: dict[str, Any],
) -> Any:
    result = run_turn(initial_prompt, provider_session_id)
    expected_contract_key = _expected_channel_contract_key(
        channel,
        request,
        message,
    )
    continuation_count = 0
    needs_continuation, incomplete_reason = _continuation_reason(
        result,
        expected_contract_key=expected_contract_key,
    )
    while (
        needs_continuation
        and continuation_count < _provider_max_continuations()
    ):
        session_id = str(
            getattr(result, "provider_session_id", "") or ""
        )
        if not session_id:
            break
        continuation_count += 1
        continued = run_turn(
            _continuation_prompt(
                expected_contract_key=expected_contract_key,
                reason=incomplete_reason,
                attempt=continuation_count,
            ),
            session_id,
        )
        result = _merge_turn_results(
            result,
            continued,
            continuation_count=continuation_count,
        )
        needs_continuation, incomplete_reason = _continuation_reason(
            result,
            expected_contract_key=expected_contract_key,
        )
    if not needs_continuation:
        return result
    return replace(
        result,
        ok=False,
        status="incomplete",
        incomplete=True,
        completion_reason=incomplete_reason,
        continuation_count=continuation_count,
        error=(
            "channel reply remained incomplete after "
            f"{continuation_count} continuation turn(s): "
            f"{incomplete_reason}"
        ),
    )


def _provider_max_continuations() -> int:
    raw = os.environ.get("ZF_CHANNEL_PROVIDER_MAX_CONTINUATIONS")
    if raw is None:
        return DEFAULT_CHANNEL_PROVIDER_MAX_CONTINUATIONS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CHANNEL_PROVIDER_MAX_CONTINUATIONS
    return min(max(value, 0), 4)


def _expected_channel_contract_key(
    channel: dict[str, Any],
    request: dict[str, Any],
    message: dict[str, Any],
) -> str:
    refs = message.get("refs") if isinstance(message.get("refs"), dict) else {}
    if refs.get("cross_review_request_id"):
        return "channel_cross_review"
    if refs.get("consensus_review_id"):
        return "channel_consensus_review"
    if refs.get("question_dedup_request_id"):
        return "channel_question_dedup"
    if refs.get("adaptive_next_round_id"):
        return "channel_contribution"
    if refs.get("synthesis_request_id"):
        return "channel_synthesis"
    if channel_reply_response_contract(channel, request, message):
        return "channel_contribution"
    return ""


def _continuation_reason(
    result: Any,
    *,
    expected_contract_key: str,
) -> tuple[bool, str]:
    status = str(getattr(result, "status", "") or "").strip().lower()
    if bool(getattr(result, "incomplete", False)) or status == "incomplete":
        return True, str(
            getattr(result, "completion_reason", "")
            or getattr(result, "error", "")
            or status
        )
    if not bool(getattr(result, "ok", False)) or not expected_contract_key:
        return False, ""
    _payload, parse_error = structured_reply_payload_with_error(
        str(getattr(result, "reply", "") or ""),
        expected_contract_key,
    )
    if parse_error:
        return True, f"typed_contract_incomplete:{expected_contract_key}"
    return False, ""


def _continuation_prompt(
    *,
    expected_contract_key: str,
    reason: str,
    attempt: int,
) -> str:
    contract = (
        f" Finish the complete {expected_contract_key} machine contract."
        if expected_contract_key
        else ""
    )
    return (
        "Continue exactly from the prior response's stopping point without "
        "repeating earlier text. Finish the user-facing answer and every required "
        f"field.{contract} This is bounded continuation attempt {attempt}. "
        f"Detected incomplete reason: {reason}."
    )


def _merge_turn_results(
    previous: Any,
    current: Any,
    *,
    continuation_count: int,
) -> Any:
    return replace(
        current,
        reply=(
            str(getattr(previous, "reply", "") or "")
            + str(getattr(current, "reply", "") or "")
        ),
        messages=[
            *list(getattr(previous, "messages", []) or []),
            *list(getattr(current, "messages", []) or []),
        ],
        usage=_merge_usage(
            getattr(previous, "usage", {}),
            getattr(current, "usage", {}),
        ),
        continuation_count=continuation_count,
    )


def _merge_usage(previous: object, current: object) -> dict[str, Any]:
    left = previous if isinstance(previous, dict) else {}
    right = current if isinstance(current, dict) else {}
    merged: dict[str, Any] = dict(left)
    for key, value in right.items():
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and isinstance(merged.get(key), int | float)
            and not isinstance(merged.get(key), bool)
        ):
            merged[key] = merged[key] + value
        else:
            merged[key] = value
    return merged


def _dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


__all__ = [
    "build_fake_channel_reply",
    "run_channel_provider_with_continuations",
]
