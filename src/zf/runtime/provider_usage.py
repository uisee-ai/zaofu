"""Provider-neutral per-turn accounting without discarding raw usage."""

from __future__ import annotations

from typing import Any


class ProviderCallNotStarted(RuntimeError):
    """The provider call boundary rejected admission before invoking the provider."""


def normalize_provider_usage(
    usage: dict[str, Any] | None,
    *,
    backend: str = "",
) -> dict[str, Any]:
    raw = usage if isinstance(usage, dict) else {}
    token_usage = raw.get("tokenUsage")
    if not isinstance(token_usage, dict):
        token_usage = raw.get("token_usage")
    if not isinstance(token_usage, dict):
        token_usage = {}

    cumulative_source = _dict_value(token_usage, "total")
    turn_source = _dict_value(token_usage, "last")
    if not turn_source:
        cumulative_source = _dict_value(raw, "total")
        turn_source = _dict_value(raw, "last")

    if turn_source:
        mode = "provider_cumulative_with_turn_delta"
        turn = _normalize_bucket(turn_source)
        cumulative = _normalize_bucket(cumulative_source)
    else:
        mode = "per_turn"
        turn = _normalize_bucket(raw)
        cumulative = {}
    return {
        "schema_version": "provider.usage_accounting.v1",
        "backend": str(backend or ""),
        "mode": mode,
        "turn": turn,
        "cumulative": cumulative,
        "budget_usage": dict(turn),
        "receipt": canonical_usage_tokens(
            turn,
            backend=backend,
        ),
    }


def canonical_usage_tokens(
    usage: dict[str, Any] | None,
    *,
    backend: str = "",
    input_semantics: str = "",
) -> dict[str, Any]:
    """Return non-overlapping token classes for accounting.

    Codex reports ``input_tokens`` as a combined counter that already includes
    cache reads/writes. Claude reports fresh input separately. Callers may pin
    ``input_semantics`` when the provider receipt is explicit; otherwise the
    backend and presence of cache counters select the conservative shape.
    """

    raw = usage if isinstance(usage, dict) else {}
    normalized = _normalize_bucket(raw)
    combined_input = int(normalized.get("input_tokens", 0) or 0)
    cache_read = int(normalized.get("cached_input_tokens", 0) or 0)
    cache_write = int(
        normalized.get("cache_creation_input_tokens", 0) or 0
    )
    semantics = str(input_semantics or "").strip()
    codex_like = str(backend or "").startswith("codex")
    if semantics == "combined_includes_cache" or (
        not semantics and codex_like and (cache_read or cache_write)
    ):
        fresh_input = max(combined_input - cache_read - cache_write, 0)
        semantics = "combined_includes_cache"
    else:
        fresh_input = combined_input
        semantics = semantics or (
            "combined_cache_unknown" if codex_like else "fresh_plus_cache"
        )
    total_input = fresh_input + cache_read + cache_write
    output = int(normalized.get("output_tokens", 0) or 0)
    return {
        "schema_version": "provider-usage-receipt.v1",
        "input_semantics": semantics,
        "fresh_input_tokens": fresh_input,
        "combined_input_tokens": (
            combined_input
            if semantics.startswith("combined_")
            else total_input
        ),
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "output_tokens": output,
        "reasoning_output_tokens": int(
            normalized.get("reasoning_tokens", 0) or 0
        ),
        "total_tokens": total_input + output,
    }


def sum_turn_usage(
    runs: list[dict[str, Any]],
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for run in runs:
        accounting = (
            run.get("usage_accounting")
            if isinstance(run.get("usage_accounting"), dict)
            else {}
        )
        turn = (
            accounting.get("turn")
            if isinstance(accounting.get("turn"), dict)
            else {}
        )
        for key, value in turn.items():
            if isinstance(value, bool):
                continue
            try:
                parsed = int(value or 0)
            except (TypeError, ValueError):
                continue
            totals[key] = totals.get(key, 0) + max(parsed, 0)
    return totals


def _normalize_bucket(value: dict[str, Any]) -> dict[str, int]:
    aliases = {
        "input_tokens": (
            "input_tokens",
            "inputTokens",
            "input",
        ),
        "output_tokens": (
            "output_tokens",
            "outputTokens",
            "output",
        ),
        "cached_input_tokens": (
            "cached_input_tokens",
            "cachedInputTokens",
            "cache_read_input_tokens",
            "cacheReadInputTokens",
        ),
        "cache_creation_input_tokens": (
            "cache_creation_input_tokens",
            "cacheCreationInputTokens",
            "cache_write_input_tokens",
            "cacheWriteInputTokens",
        ),
        "reasoning_tokens": (
            "reasoning_tokens",
            "reasoningTokens",
            "reasoning_output_tokens",
            "reasoningOutputTokens",
        ),
        "total_tokens": (
            "total_tokens",
            "totalTokens",
        ),
    }
    normalized: dict[str, int] = {}
    for target, names in aliases.items():
        for name in names:
            if name not in value:
                continue
            try:
                normalized[target] = max(int(value.get(name) or 0), 0)
            except (TypeError, ValueError):
                normalized[target] = 0
            break
    if "total_tokens" not in normalized:
        total = (
            normalized.get("input_tokens", 0)
            + normalized.get("output_tokens", 0)
        )
        if total:
            normalized["total_tokens"] = total
    return normalized


def _dict_value(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    return item if isinstance(item, dict) else {}


__all__ = [
    "canonical_usage_tokens",
    "normalize_provider_usage",
    "sum_turn_usage",
]
