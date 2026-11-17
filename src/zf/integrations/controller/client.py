"""OAuth Client Credentials transport; secrets stay in environment, never in outbox."""

import os

from zf.integrations.controller.collector import Collector
from zf.integrations.controller.outbox import OutboxFull


def collect_project(context, collector, *, heartbeat=True) -> bool:
    """Replay sanctioned sources; never write Kernel or provider state."""
    from zf.core.events.factory import event_log_from_project
    from zf.integrations.controller.terminal import collect_terminal

    deferred = False
    try:
        collector.project()
        log = event_log_from_project(context.state_dir, config=context.config)
        try:
            for event in log.read_all():
                collector(event)  # Stable source identity repairs append-before-outbox crash windows.
        finally:
            log.close()
        collect_terminal(context, collector)
        if heartbeat:
            collector.heartbeat()
    except OutboxFull:
        # A full queue must still drain. Missing projections replay on the next sync.
        deferred = True
    return deferred


def synchronize(context) -> dict:
    # Optional dependencies are imported only by the explicitly enabled network caller.
    from authlib.integrations.httpx_client import OAuth2Client
    from jsonschema import Draft202012Validator, FormatChecker
    import httpx2

    config = context.config
    cloud = config.observability.controller
    if not cloud.enabled:
        raise ValueError("observability.controller.enabled is required")
    secret = os.environ.get(cloud.client_secret_env, "")
    if not secret:
        raise ValueError("Controller client secret environment variable is unset")
    collector = Collector(context.state_dir, config)
    deferred = collect_project(context, collector)
    with OAuth2Client(client_id=cloud.client_id, client_secret=secret, timeout=5, trust_env=False) as oauth:
        token = oauth.fetch_token(cloud.issuer + '/protocol/openid-connect/token', grant_type='client_credentials')
    with httpx2.Client(base_url=cloud.endpoint, timeout=10, trust_env=False,
                       headers={'Authorization': 'Bearer ' + token['access_token']}) as client:
        response = client.get('/internal/v1/me')
        response.raise_for_status()
        hello = response.json()
        if (hello.get('protocol') != 'zfc.telemetry.v1' or hello.get('workspace_id') != cloud.workspace_id
                or hello.get('deployment_id') != cloud.deployment_id):
            raise ValueError('Controller protocol or identity mismatch')
        response = client.get('/internal/v1/schema')
        response.raise_for_status()
        validator = Draft202012Validator(response.json(), format_checker=FormatChecker())
        batch = collector.outbox.pending(min(cloud.batch_size, hello['max_batch']))
        if any(message['payload'].get('baseline_only') for message in batch) and 'cumulative_baseline' not in hello.get('capabilities', []):
            raise ValueError('Controller cumulative baseline capability is required')
        for message in batch:
            validator.validate(message)
        if batch:
            response = client.post('/internal/v1/me/batches', json={'protocol': 'zfc.telemetry.v1', 'records': batch})
            response.raise_for_status()
            collector.outbox.acknowledge(batch, response.json())
        from types import SimpleNamespace
        from zf.integrations.controller.health import deliver
        def request(method, path, body):
            response = client.request(method, path, json=body)
            response.raise_for_status()
            return response.json()
        health_failures = deliver(context.state_dir, SimpleNamespace(request=request))
    return {**collector.outbox.status(), 'collection_deferred': deferred,
            'health_delivery_failures': health_failures}
