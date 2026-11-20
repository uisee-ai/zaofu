"""Read-only inspection of native Codex counters scoped to the current project.

This does not import old spend into a newly enrolled deployment or read auth files.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from zf.core.cost.catalog import PricingCatalogStore
from zf.runtime.backend_session_reader import CodexSessionReader
from zf.runtime.provider_usage import canonical_usage_tokens


def local_codex_usage(project_root: Path, state_dir: Path, *, limit: int = 5, session_id: str = '') -> dict:
    if not 1 <= limit <= 50:
        raise ValueError('limit must be 1..50')
    root = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'sessions'
    candidates = []
    for path in root.glob('*/*/*/rollout-*.jsonl'):
        try:
            with path.open(encoding='utf-8') as stream:
                metadata = json.loads(stream.readline(65536))
            if not isinstance(metadata, dict) or not isinstance(metadata.get('payload'), dict):
                continue
            payload = metadata['payload']
            native_id = str(payload.get('id') or '')
            if (metadata.get('type') != 'session_meta' or not native_id or not payload.get('cwd')
                    or Path(str(payload.get('cwd') or '')).resolve() != project_root.resolve()
                    or (session_id and native_id != session_id)):
                continue
            candidates.append((path.stat().st_mtime, path, native_id))
        except (OSError, ValueError, TypeError):
            continue
    rows = []
    catalog = PricingCatalogStore(state_dir)
    for _, path, native_id in sorted(candidates, reverse=True)[:limit]:
        try:
            report = CodexSessionReader().read_latest_usage(path)
        except (AttributeError, TypeError, ValueError):
            report = None
        counters = report.raw if report else {}
        valid = (all(key in counters for key in ('input_tokens', 'output_tokens'))
            and all(type(value) is int and value >= 0 for value in counters.values())
            and counters.get('cached_input_tokens', 0) + counters.get('cache_creation_input_tokens', 0) <= counters.get('input_tokens', 0)
            and counters.get('reasoning_output_tokens', 0) <= counters.get('output_tokens', 0))
        if report is None or not valid:
            rows.append({'session_id': native_id, 'quality': 'missing', 'tokens': None})
            continue
        receipt = canonical_usage_tokens(report.raw, backend='codex', input_semantics=report.input_semantics)
        pricing = catalog.resolve(provider=report.provider, model=report.model,
            accounting_mode=report.accounting_mode, occurred_at=report.timestamp)
        rows.append({'session_id': native_id, 'latest_model': report.model, 'provider': report.provider,
            'session_started_at': report.series_started_at or None, 'observed_at': report.timestamp,
            'accounting_mode': report.accounting_mode, 'semantics': report.usage_semantics,
            'counter_scope': 'session_lifetime' if report.usage_semantics == 'cumulative' else 'last_reported_turn',
            'quality': 'reported', 'tokens': receipt,
            'estimated_cost_usd': None, 'provider_reported_cost_usd': None, 'billed_cost_usd': None,
            'cost_status': 'unpriced' if pricing is None else 'historical_price_attribution_required',
            'cost_reason': 'Native usage is not an invoice; a cumulative session may span multiple models and prices'})
    return {'source': 'local_codex', 'scope': 'current_project_native_sessions',
        'read_at': datetime.now(timezone.utc).isoformat(), 'matched_sessions': len(candidates),
        'limit': limit, 'sessions': rows, 'uploaded': False}
