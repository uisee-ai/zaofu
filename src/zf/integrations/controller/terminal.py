"""Read the existing durable terminal registry and settled cost-record.v2 receipts.

No transcript discovery, provider calls, terminal mutations or cost re-accounting.
"""

import json
import re
from datetime import datetime, timezone

from zf.integrations.controller.collector import identifier, stable


def collect_terminal(context, collector) -> None:
    from zf.core.cost.tracker import CostTracker
    from zf.web.terminal_registry import REGISTRY_FILENAME, TerminalRegistry
    from zf.web.terminal_usage import TERMINAL_COST_FILENAME

    cloud, outbox = collector.cloud, collector.outbox
    registry_path = context.state_dir / REGISTRY_FILENAME
    identities = {}
    if registry_path.exists():
        header = json.loads(registry_path.read_text())
        registry = TerminalRegistry(context.state_dir, project_id=header['project_id'], project_root=context.project_root)
        for row in registry.read()['sessions']:
            session = identifier(row.get('session_id'))
            if not session:
                continue
            instance = f"terminal:{header['project_id']}:{session}:g{row['generation']}"
            identities[instance] = row
            lifecycle = [(row.get('created_at'), 'started')]
            if row.get('state') in {'stopped', 'missing'}:
                lifecycle.append((row.get('stopped_at') or row.get('updated_at'), 'stopped'))
            for timestamp, status in lifecycle:
                if not timestamp:
                    continue
                binding = outbox.binding_at(timestamp)
                if binding is None:
                    continue
                payload = {'kind': 'session', 'project_id': cloud.project_id, 'session_id': session,
                    'version': int(datetime.fromisoformat(timestamp).timestamp() * 1_000_000),
                    'source': 'terminal', 'status': status, 'provider': identifier(row.get('provider')),
                    'provider_session_id': identifier(row.get('provider_session_id')),
                    'started_at': row.get('created_at'), 'ended_at': timestamp if status == 'stopped' else None}
                outbox.enqueue('terminal-session:' + instance + ':' + status, timestamp, payload, origin_id=binding['deployment'])
    for entry in CostTracker(context.state_dir / TERMINAL_COST_FILENAME).usage_records():
        sample = entry.get('usage_sample_id')
        if entry.get('schema_version') != 'cost-record.v2' or not sample:
            continue  # Legacy receipts lack a stable identity; never mint one from a nearby session.
        timestamp = entry.get('occurred_at') or datetime.fromtimestamp(entry['ts'], timezone.utc).isoformat()
        binding = outbox.binding_at(timestamp)
        if binding is None:
            continue
        identity = identities.get(entry.get('instance_id'), {})
        counters = {'input_tokens': entry.get('input_tokens'), 'output_tokens': entry.get('output_tokens'),
            'cache_read_tokens': entry.get('cache_read_tokens', 0),
            'cache_creation_tokens': entry.get('cache_creation_tokens', 0) + entry.get('cache_creation_1h_tokens', 0)}
        known = all(type(value) is int and 0 <= value <= 10**15 for value in counters.values())
        payload = {'kind': 'usage', 'record_id': stable(cloud.project_id + ':terminal:' + sample), 'revision': 1,
            'project_id': cloud.project_id, 'session_id': identifier(identity.get('session_id')),
            'provider_session_id': identifier(identity.get('provider_session_id')),
            'agent_id': 'web-terminal', 'provider': identifier(entry.get('provider')) or 'unknown',
            'model': identifier(entry.get('model')) or 'unknown', 'mode': binding['mode'],
            'accounting_mode': entry.get('accounting_mode') if entry.get('accounting_mode') in {'api', 'subscription', 'enterprise'} else 'unknown',
            'source': 'runtime', 'quality': 'reported' if known else 'missing', 'completeness': 'complete' if known else 'pending',
            'tokens': counters if known else None, 'semantics': 'delta', 'input_semantics': 'normalized_cost_v2'}
        for field in ('estimated_cost_usd', 'provider_reported_cost_usd'):
            value = entry.get(field)
            payload[field] = value if isinstance(value, str) and re.fullmatch(r'\d{1,12}(\.\d{1,12})?', value) else None
        outbox.enqueue('terminal-usage:' + sample, timestamp, payload, origin_id=binding['deployment'])
