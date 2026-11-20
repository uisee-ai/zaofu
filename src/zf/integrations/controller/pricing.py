"""Price only complete API usage against the exact approved catalog version."""

from zf.core.cost.calculator import calculate_usage_cost
from zf.core.cost.catalog import PricingCatalogStore, provider_for_backend
from zf.integrations.controller.collector import now
from zf.runtime.provider_usage import canonical_usage_tokens


def cost_fields(context, config, provider, model, usage, accounting_mode, fresh_input):
    if accounting_mode not in {'api', 'enterprise'} or not usage:
        return {}
    tokens = canonical_usage_tokens(usage, backend=provider,
        input_semantics='fresh_plus_cache' if fresh_input else None)
    calculation = calculate_usage_cost(catalog_store=PricingCatalogStore(context.state_dir), legacy_rates=None,
        provider=provider_for_backend(provider, model=model), backend=provider, model=model,
        accounting_mode=accounting_mode, occurred_at=now(), service_tier='standard', request_input_tokens=None,
        input_tokens=tokens['fresh_input_tokens'], output_tokens=tokens['output_tokens'],
        cache_creation_tokens=tokens['cache_creation_input_tokens'], cache_creation_1h_tokens=0,
        cache_read_tokens=tokens['cache_read_input_tokens'], provider_cost_usd=None)
    if (calculation.estimate is None or calculation.cost_status != 'exact' or not calculation.resolution
            or calculation.resolution.catalog_version != config.price_version):
        return {}
    return {'estimated_cost_usd': format(calculation.estimate, '.12f')}
