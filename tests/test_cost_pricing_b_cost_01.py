"""B-COST-01: provider-cost-first + cache-aware per-model pricing.

Covers the three confirmed defects and the corrected fix:
- D0 provider-cost-first: total_cost_usd is authoritative when present.
- D1 per-model resolve: claude-opus-4-8 prices at opus, not default Sonnet.
- D2 cache-aware: cache tokens priced at their own rates; codex (input
  already includes cache) does NOT double-count.
- back-compat: default/sonnet no-cache cost unchanged from legacy 3/15.
"""

from __future__ import annotations

from zf.core.cost.pricing import FALLBACK_RATES, ModelRate, resolve_rate
from zf.core.cost.tracker import CostTracker
from zf.core.events.model import ZfEvent
from zf.runtime.housekeeping import apply_agent_usage_event


# --- D1: model-name resolution ---------------------------------------------

def test_resolve_opus_4_8_hits_opus_not_default():
    rate = resolve_rate("claude-opus-4-8")
    assert (rate.input, rate.output) == (5.0, 25.0)  # NOT default 3/15


def test_resolve_dotted_id_normalizes():
    assert resolve_rate("claude-opus-4.8") is resolve_rate("claude-opus-4-8")


def test_resolve_case_insensitive():
    assert resolve_rate("Claude-Opus-4-8").output == 25.0


def test_resolve_canonical_prefers_longer_match():
    # canonical fallback should prefer the specific opus-4-8 over bare "opus"
    rate = resolve_rate("anthropic/claude-opus-4-8-20260101")
    assert rate.output == 25.0


def test_resolve_unknown_falls_back_to_default_never_raises():
    rate = resolve_rate("totally-made-up-model")
    assert rate is FALLBACK_RATES["default"]
    assert resolve_rate(None) is FALLBACK_RATES["default"]


# --- D0: provider-cost-first -----------------------------------------------

def test_provider_cost_is_authoritative(tmp_path):
    t = CostTracker(tmp_path / "cost.jsonl")
    cost = t.record_usage(
        "dev", 9999, 9999, model="claude-opus-4-8",
        provider_cost_usd=0.0731,
    )
    assert cost == 0.0731  # token math ignored entirely
    entry = (tmp_path / "cost.jsonl").read_text().strip()
    assert '"cost_source": "provider"' in entry


def test_zero_provider_cost_falls_through_to_rate(tmp_path):
    t = CostTracker(tmp_path / "cost.jsonl")
    cost = t.record_usage("dev", 1_000_000, 0, model="claude-opus-4-8",
                          provider_cost_usd=0.0)
    assert cost == 5.0  # 1M input × $5/M opus, not provider's 0
    assert '"cost_source": "rate"' in (tmp_path / "cost.jsonl").read_text()


# --- D2: cache-aware token pricing -----------------------------------------

def test_cache_tokens_priced_at_cache_rates(tmp_path):
    t = CostTracker(tmp_path / "cost.jsonl")
    # opus: in 5, out 25, cache_creation 6.25, cache_read 0.50 (per 1M)
    cost = t.record_usage(
        "dev", 1_000_000, 1_000_000, model="claude-opus-4-8",
        cache_creation_tokens=1_000_000, cache_read_tokens=1_000_000,
    )
    assert abs(cost - (5.0 + 25.0 + 6.25 + 0.50)) < 1e-9


def test_default_sonnet_no_cache_is_back_compat(tmp_path):
    t = CostTracker(tmp_path / "cost.jsonl")
    # legacy behaviour: default and sonnet both 3/15, no cache
    assert t.record_usage("dev", 1_000_000, 1_000_000) == 3.0 + 15.0
    assert t.record_usage("dev", 1_000_000, 1_000_000, model="sonnet") == 3.0 + 15.0


# --- housekeeping wiring (the live cost path) ------------------------------

def _usage_event(actor, payload):
    return ZfEvent(type="agent.usage", actor=actor, payload=payload)


def test_housekeeping_prefers_provider_total_cost(tmp_path):
    t = CostTracker(tmp_path / "cost.jsonl")
    ev = _usage_event("dev-1", {
        "total_cost_usd": 0.042,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "backend": "claude-code",
    })
    apply_agent_usage_event(t, ev)
    assert abs(t.total_usd() - 0.042) < 1e-9
    assert '"cost_source": "provider"' in (tmp_path / "cost.jsonl").read_text()


def test_housekeeping_disk_reader_uses_model_and_cache(tmp_path):
    t = CostTracker(tmp_path / "cost.jsonl")
    ev = _usage_event("dev-1", {
        "source": "disk_reader",
        "model": "claude-opus-4-8",
        "backend": "claude-code",
        "usage": {
            "input_tokens": 1_000_000, "output_tokens": 0,
            "cache_creation_input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
        },
    })
    apply_agent_usage_event(t, ev)
    # 1M input×5 + 1M cache_creation×6.25 + 1M cache_read×0.50 = 11.75
    assert abs(t.total_usd() - 11.75) < 1e-9


def test_housekeeping_codex_does_not_double_count_cache(tmp_path):
    t = CostTracker(tmp_path / "cost.jsonl")
    # Codex input_tokens already includes cache; cache fields present in
    # payload must be ignored to avoid double-counting.
    ev = _usage_event("worker-1", {
        "source": "disk_reader",
        "backend": "codex",
        "usage": {
            "input_tokens": 1_000_000, "output_tokens": 0,
            "cache_read_input_tokens": 1_000_000,
            "cache_creation_input_tokens": 1_000_000,
        },
    })
    apply_agent_usage_event(t, ev)
    # default rate 3/M input only (cache ignored) → 3.0, not 3+cache
    assert abs(t.total_usd() - 3.0) < 1e-9
    txt = (tmp_path / "cost.jsonl").read_text()
    assert '"cache_read_tokens": 0' in txt
