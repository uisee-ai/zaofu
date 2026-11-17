"""Short-lived machine transport for the deployment Connector, never human credentials."""

import time


class WorkspaceTransport:
    def __init__(self, config, credentials):
        self.config, self.credentials = config, credentials
        self.client = None
        self.token = None
        self.refresh_at = 0.0

    def __enter__(self):
        import httpx2

        self.client = httpx2.Client(base_url=self.config.endpoint, timeout=10,
                                    trust_env=False, follow_redirects=False)
        return self

    def __exit__(self, *exc):
        self.client.close()
        self.token = None

    def request(self, method, path, body=None, *, params=None):
        from authlib.integrations.httpx_client import OAuth2Client

        if time.monotonic() >= self.refresh_at:
            with OAuth2Client(client_id=self.credentials['client_id'],
                    client_secret=self.credentials['client_secret'], timeout=5,
                    trust_env=False, follow_redirects=False) as oauth:
                token = oauth.fetch_token(self.config.issuer + '/protocol/openid-connect/token',
                                          grant_type='client_credentials')
            self.token = token['access_token']
            # Controller requires a machine token issued within the last 25 seconds.
            self.refresh_at = time.monotonic() + min(15, max(0, float(token.get('expires_in', 20)) - 5))
        response = self.client.request(method, path, json=body, params=params,
            headers={'Authorization': 'Bearer ' + self.token})
        response.raise_for_status()
        if len(response.content) > 2 * 1024 * 1024:
            raise ValueError('Controller response exceeds supported size')
        return response.json()

    def handshake(self, *, receipt_only=False):
        from jsonschema import Draft202012Validator, FormatChecker

        hello = self.request('GET', '/internal/v1/me')
        if (hello.get('protocol') != 'zfc.telemetry.v1'
                or hello.get('workspace_id') != self.config.workspace_id
                or hello.get('deployment_id') != self.credentials['deployment_id']
                or type(hello.get('generation')) is not int or hello['generation'] != self.credentials['generation']
                or hello.get('status') not in ({'active', 'draining'} if receipt_only else {'active'})
                or type(hello.get('max_batch')) is not int or not 1 <= hello['max_batch'] <= 32):
            raise ValueError('Controller identity, generation or protocol mismatch')
        schema = self.request('GET', '/internal/v1/schema')
        pending = [schema]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                if any(key in item and not str(item[key]).startswith('#')
                       for key in ('$ref', '$dynamicRef', '$recursiveRef')):
                    raise ValueError('remote schema references are unsupported')
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        Draft202012Validator.check_schema(schema)
        self.hello = hello
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def deliver(self, outbox):
        if 'receipt_dispositions' in self.hello.get('capabilities', []):
            status = outbox.status()
            for origin in status['origins']:
                identifiers = outbox.quarantined_ids(origin['origin_id']) or ['watermark-only']
                for start in range(0, len(identifiers), 32):
                    response = self.request('GET', '/internal/v1/me/receipt-dispositions', params={
                        'origin_id': origin['origin_id'], 'stream_id': status['stream_id'],
                        'message_id': identifiers[start:start + 32]})
                    outbox.reconcile(origin['origin_id'], response)
            outbox.prune(retention_seconds=getattr(self.config, 'receipt_retention_seconds', 604800))
        batch = outbox.pending(min(self.config.batch_size, self.hello['max_batch']), receipt_only=self.hello['status'] != 'active')
        if any(message['payload'].get('baseline_only') for message in batch):
            if 'cumulative_baseline' not in self.hello.get('capabilities', []):
                raise ValueError('Controller cumulative baseline capability is required')
        for message in batch:
            self.validator.validate(message)
        if batch:
            response = self.request('POST', '/internal/v1/me/batches',
                                    {'protocol': 'zfc.telemetry.v1', 'records': batch})
            outbox.acknowledge(batch, response)
        return outbox.status()
