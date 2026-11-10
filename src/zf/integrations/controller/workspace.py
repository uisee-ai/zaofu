"""One installation identity, zero or more explicitly consented Project projections."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from zf.core.config.controller import ControllerObservationConfig
from zf.core.config.loader import ConfigError, load_config
from zf.core.config.project_context import ProjectContext
from zf.core.workspace.registry import WorkspaceRegistry
from zf.integrations.controller.client import collect_project
from zf.integrations.controller.collector import Collector, now
from zf.integrations.controller.installation import Installation, private_read
from zf.integrations.controller.outbox import Outbox, OutboxFull
from zf.integrations.controller.workspace_transport import WorkspaceTransport


def observation(config, credentials, project_id=''):
    return ControllerObservationConfig(enabled=True, endpoint=config.endpoint, issuer=config.issuer,
        workspace_id=config.workspace_id, deployment_id=credentials['deployment_id'], project_id=project_id,
        client_id=credentials['client_id'], mode=config.mode, development=config.development,
        interval_seconds=config.interval_seconds, batch_size=config.batch_size,
        max_pending_records=config.max_pending_records, max_pending_bytes=config.max_pending_bytes)


def project_contexts(config, credentials, project_ids):
    registry = WorkspaceRegistry(workspace=config.workspace)
    contexts, states, roots = [], set(), set()
    for project_id in project_ids:
        entry = registry.get(project_id)
        if entry is None or entry.project_id != project_id:
            raise ValueError('policy requires an exact registered Project ID')
        root, path = Path(entry.root).resolve(), Path(entry.config_path).resolve()
        if path != root / 'zf.yaml':
            raise ValueError('Project configuration ownership mismatch')
        project = load_config(path)
        state = Path(project.project.state_dir)
        state = (state if state.is_absolute() else root / state).resolve()
        if state in states or root in roots or state == config.state_dir.resolve():
            raise ValueError('Projects must have independent roots and state directories')
        states.add(state)
        roots.add(root)
        cloud = observation(config, credentials, project_id)
        previous = project.observability.controller
        if previous.enabled and any(getattr(previous, name) != getattr(cloud, name)
                for name in ('endpoint', 'issuer', 'workspace_id', 'deployment_id', 'project_id', 'mode')):
            raise ValueError('Project already has a different Controller binding')
        # This overlay belongs only to the read projection. It never modifies a Project's zf.yaml.
        project = replace(project, observability=replace(project.observability, controller=cloud))
        contexts.append(ProjectContext(root, path, project, state))
    return contexts


def apply_policy(config, credentials, transport, installation):
    result = transport.request('GET', '/internal/v1/me/control')
    if (result.get('protocol') != 'zfc.control.v1'
            or result.get('deployment_id') != credentials['deployment_id']
            or type(result.get('generation')) is not int or result['generation'] != credentials['generation']):
        raise ValueError('Controller policy identity mismatch')
    policy = result.get('policy')
    if (not isinstance(policy, dict) or policy.get('workspace_id') != config.workspace_id
            or type(policy.get('version')) is not int or policy['version'] < 1
            or not isinstance(policy.get('limits'), dict)
            or not isinstance(policy.get('projects'), list) or len(policy['projects']) > 1000
            or not all(isinstance(p, str) for p in policy['projects'])
            or len(set(policy['projects'])) != len(policy['projects'])):
        raise ValueError('invalid Controller policy')
    error, contexts = None, []
    saved = installation.path / 'policy.json'
    snapshot = {'version': policy['version'], 'projects': policy['projects'],
                'deployment_id': credentials['deployment_id'], 'generation': credentials['generation'],
                'management': 'observed'}
    if saved.exists():
        previous = json.loads(private_read(saved))
        if (policy['version'] < previous['version']
                or (policy['version'] == previous['version'] and snapshot != previous)):
            raise ValueError('policy rollback or same-version conflict')
    if policy.get('capabilities') != ['observe'] or policy.get('limits', {}).get('management') != 'observed':
        error = 'unsupported'
    elif not set(policy['projects']).issubset(config.projects):
        error = 'local_consent_required'
    else:
        try:
            contexts = project_contexts(config, credentials, policy['projects'])
            # Persist each source boundary before reporting the scope as applied.
            for context in contexts:
                Collector(context.state_dir, context.config)
        except (ConfigError, ValueError, OSError):
            error = 'local_configuration_invalid'
    if error is None:
        installation.write('policy.json', snapshot)
    transport.request('POST', '/internal/v1/me/control/policy', {
        'protocol': 'zfc.control.v1', 'version': policy['version'], 'applied': error is None, 'error': error})
    if error:
        raise ValueError('Controller policy was not applied: ' + error)
    # Commands returned by poll are never accepted, executed or given terminal receipts here.
    return policy, contexts


def heartbeat(outbox, generation, projects):
    import zf

    status = outbox.status()
    statuses = [status] + projects
    oldest = min((row['oldest_pending'] for row in statuses if row.get('oldest_pending')), default=None)
    timestamp = now()
    outbox.enqueue('heartbeat:' + outbox.config.deployment_id + ':' + timestamp, timestamp, {
        'kind': 'heartbeat', 'version': time.time_ns(), 'connector_version': 'workspace-observer-v1',
        'runtime_version': zf.__version__, 'project_id': None, 'coverage': 'zf_event_ledger',
        'coverage_since': status['coverage_since'], 'kernel_status': 'unknown',
        'applied_generation': generation,
        'pending_records': sum(row['pending_records'] for row in statuses),
        'pending_bytes': sum(row['pending_bytes'] for row in statuses),
        'oldest_pending_at': datetime.fromtimestamp(oldest, timezone.utc).isoformat() if oldest else None})


def synchronize_workspace(config):
    installation = Installation(config)
    credentials = installation.credentials()
    # A single writer for this installation prevents rotation/policy/application races.
    with installation.locked(), WorkspaceTransport(config, credentials) as transport:
        if credentials != installation.decode(json.loads(private_read(installation.path / 'binding.json'))):
            raise ValueError('credentials changed; retry synchronization')
        transport.handshake()
        policy, contexts = apply_policy(config, credentials, transport, installation)
        rows = []
        for context in contexts:
            collector = Collector(context.state_dir, context.config)
            try:
                deferred = collect_project(context, collector, heartbeat=False)
                status = transport.deliver(collector.outbox)
                rows.append({'project_id': collector.cloud.project_id,
                             'status': 'quarantined' if status['quarantined_records'] else 'synchronized',
                             'collection_deferred': deferred, **status})
            except Exception as exc:
                # Keep receipts, continue other Projects, and never print provider or credential bodies.
                rows.append({'project_id': collector.cloud.project_id, 'status': 'delivery_failed',
                             'error_type': type(exc).__name__, **collector.outbox.status()})
        outbox = Outbox(config.state_dir, observation(config, credentials))
        try:
            heartbeat(outbox, credentials['generation'], rows)
        except OutboxFull:
            pass  # A full queue must still drain; the next watch iteration reports a fresh heartbeat.
        status = transport.deliver(outbox)
    complete = all(row['status'] == 'synchronized' for row in rows) and not status['quarantined_records']
    return {'status': 'synchronized' if complete else 'partial',
            'workspace_id': config.workspace_id, 'deployment_id': credentials['deployment_id'],
            'management': 'observed', 'policy_version': policy['version'], 'projects': rows,
            'unapproved_projects': sorted(set(config.projects) - set(policy['projects'])),
            'deployment_outbox': status}


def workspace_status(config):
    installation = Installation(config)
    if not (installation.path / 'binding.json').exists():
        return {'status': 'unenrolled', 'workspace_id': config.workspace_id, 'online_verified': False}
    credentials = installation.credentials()
    result = installation.summary(credentials)
    policy = installation.path / 'policy.json'
    if policy.exists():
        result['last_applied_policy'] = json.loads(private_read(policy))
    return result
