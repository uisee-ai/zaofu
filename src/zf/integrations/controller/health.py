"""Sanitized health from real call outcomes, with durable ordered delivery."""

import hashlib
import json
import time

from zf.core.state.locks import locked_path
from zf.integrations.controller.collector import identifier
from zf.integrations.controller.ledger import Ledger


def classify(status, error=None):
    has_error = bool(error)
    # Accept machine-readable error fields only. Free text can contain credentials/prompts.
    if isinstance(error, str):
        try:
            error = json.loads(error)
        except (ValueError, TypeError):
            error = None
    if isinstance(error, dict):
        nested = error.get('error', error)
        nested = nested if isinstance(nested, dict) else {}
        code = nested.get('code') or nested.get('type')
        http = error.get('status_code') or error.get('status')
    else:
        code = getattr(error, 'code', None)
        http = getattr(error, 'status_code', None)
    code = code if isinstance(code, str) else None
    if code in {'insufficient_quota', 'quota_exhausted', 'billing_hard_limit_reached'}:
        return 'quota_exhausted'
    if code in {'credential_revoked', 'token_revoked', 'api_key_revoked'}:
        return 'credential_revoked'
    if http == 401 or code in {'token_expired', 'invalid_api_key', 'authentication_error'}:
        return 'credential_expired'
    if http == 429 or code in {'rate_limit_exceeded', 'rate_limit_error'}:
        return 'rate_limited'
    if type(http) is int and 500 <= http <= 599:
        return 'outage'
    if status in {'completed', 'success', 'succeeded'} and not has_error:
        return 'healthy'
    return 'unknown'


def capture(state_dir, deployment, project, mode, provider, *, status, error=None, observed_at=None, sample_id=''):
    try:
        return _capture(state_dir, deployment, project, mode, provider, status=status, error=error,
                        observed_at=observed_at, sample_id=sample_id)
    except Exception:
        # Health is diagnostic: a failed observation must never discard a usage receipt.
        # Existing health expires normally; no fresh successful observation is fabricated.
        return False


def _capture(state_dir, deployment, project, mode, provider, *, status, error, observed_at, sample_id):
    # CPA account-pool health is attested by the CPA connector, not by a tenant Runtime.
    if mode not in {'self_managed_api', 'self_managed_cli'} or not identifier(provider):
        return
    observed = observed_at if observed_at is not None else time.time()
    if not time.time() - 300 <= observed <= time.time() + 5:
        return
    binding = hashlib.sha256(':'.join([deployment, project, mode, provider]).encode()).hexdigest()
    ledger = Ledger(state_dir)
    with locked_path(ledger.path.parent / 'health', timeout_seconds=10):
        previous = ledger.get('health:' + binding)
        if previous and (previous['body']['observed_order'] > observed
                or previous['body']['sample_id'] == sample_id):
            return
        payload = {'protocol': 'zfc.control.v1', 'binding_id': binding,
            'version': ledger.next_version('health:' + binding), 'mode': mode, 'provider': provider,
            'status': classify(status, error), 'observed_at': int(observed), 'valid_seconds': 300}
        body = {'payload': payload, 'observed_order': observed, 'sample_id': sample_id}
        if not ledger.insert('health:' + binding, 'provider_health', 'pending', body):
            ledger.put('health:' + binding, 'pending', body)
    return True


def deliver(state_dir, transport):
    try:
        ledger = Ledger(state_dir)
        rows = ledger.rows('provider_health')
    except Exception:
        return 1
    failures = 0
    for row in rows:
        if row['status'] != 'pending':
            continue
        payload = row['body']['payload']
        try:
            if payload['observed_at'] + payload['valid_seconds'] <= int(time.time()):
                status = 'expired'
            else:
                result = transport.request('POST', '/internal/v1/me/provider-health', payload)
                if result.get('status') not in {'accepted', 'duplicate', 'superseded'}:
                    raise ValueError('invalid provider health ACK')
                status = 'delivered'
            with locked_path(ledger.path.parent / 'health', timeout_seconds=10):
                current = ledger.get(row['id'])
                if current['body'] == row['body']:
                    ledger.put(row['id'], status, row['body'])
        except Exception:
            failures += 1  # Durable retry without refreshing the original observation time.
    return failures
