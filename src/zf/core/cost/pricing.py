"""Model pricing — cache-aware per-model token rates with name resolution.

Used by CostTracker as the *fallback* pricing path: when a turn carries a
provider-reported ``total_cost_usd`` that value wins (it is authoritative);
only token-derived turns (disk-reader / codex / tmux-hosted, which have no
provider cost) are priced here.

The table uses four-field rates (input / output / cache_creation / cache_read),
a dated hardcoded fallback table, and multi-level model-name resolution
(exact → dotted-to-dashed → case-insensitive → canonical). No network
dependency — the fallback table is the source of truth; an optional litellm
refresh can override it later.
"""

from __future__ import annotations

from dataclasses import dataclass


# Bump whenever FALLBACK_RATES values change so a startup seeder (if added)
# knows to re-upsert cached pricing records.
FALLBACK_VERSION = "2026-06-17"


@dataclass(frozen=True)
class ModelRate:
    """Per-model pricing in USD per 1M tokens."""

    input: float
    output: float
    cache_creation: float = 0.0
    cache_read: float = 0.0


# Dated fallback, USD per 1M tokens. Values current as of 2026-06.
# Cache rates: Claude convention — creation ≈ 1.25× input, read ≈ 0.1× input.
FALLBACK_RATES: dict[str, ModelRate] = {
    "default": ModelRate(input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30),
    "claude-sonnet-4-6": ModelRate(3.0, 15.0, 3.75, 0.30),
    "claude-opus-4-6": ModelRate(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-7": ModelRate(5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-8": ModelRate(5.0, 25.0, 6.25, 0.50),
    "claude-fable-5": ModelRate(10.0, 50.0, 12.5, 1.0),
    "claude-haiku-4-5": ModelRate(0.25, 1.25, 0.30, 0.03),
    # Coarse family aliases so a bare "opus" / "sonnet" / "haiku" still lands
    # on a sane rate (legacy CostTracker rate-bucket compatibility).
    "opus": ModelRate(5.0, 25.0, 6.25, 0.50),
    "sonnet": ModelRate(3.0, 15.0, 3.75, 0.30),
    "haiku": ModelRate(0.25, 1.25, 0.30, 0.03),
}


def _normalize(model: str) -> str:
    """Dots to dashes so dotted ids (claude-opus-4.8) match dashed keys."""
    return model.replace(".", "-")


def _canonical(s: str) -> str:
    """Strip provider prefix (after last '/'), lowercase, keep alnum only."""
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    return "".join(c for c in s.lower() if c.isalnum())


def resolve_rate(
    model: str | None,
    rates: dict[str, ModelRate] | None = None,
) -> ModelRate:
    """Resolve a model id to its rate.

    Levels, falling through on miss:
    1. exact, 2. dotted-to-dashed, 3. case-insensitive (exact + normalized),
    4. canonical (provider-stripped, alnum-only) substring, then ``default``.
    Never raises — unknown models fall back to ``default`` so recording a
    cost never blocks on an unrecognised model name.
    """
    table = rates or FALLBACK_RATES
    if not model:
        return table["default"]
    # 1. exact
    if model in table:
        return table[model]
    # 2. dotted-to-dashed
    norm = _normalize(model)
    if norm != model and norm in table:
        return table[norm]
    # 3. case-insensitive (against both raw and normalized)
    lower = model.lower()
    lower_norm = norm.lower()
    for key, rate in table.items():
        kl = key.lower()
        if kl == lower or kl == lower_norm:
            return rate
    # 4. canonical: longest matching key wins so "claude-opus-4-8" prefers
    #    the opus-4-8 entry over a bare "opus".
    canon = _canonical(model)
    best: tuple[int, ModelRate] | None = None
    for key, rate in table.items():
        ck = _canonical(key)
        if ck and (ck in canon or canon in ck):
            if best is None or len(ck) > best[0]:
                best = (len(ck), rate)
    if best is not None:
        return best[1]
    return table["default"]
