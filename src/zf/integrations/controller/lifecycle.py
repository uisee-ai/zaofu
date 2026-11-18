"""Explicit local maintenance, cloud serving and authorized external-installation release."""

import base64
import json
import os
from pathlib import Path
import threading
import time
import uuid

from zf.integrations.controller.binding import bind, binding_path, ManagedDenied
from zf.integrations.controller.installation import Installation, private_read
from zf.integrations.controller.ledger import Ledger
from zf.integrations.controller.workspace import project_contexts, synchronize_workspace


def drain(config):
    installation = Installation(config)
    installation.credentials()
    with installation.locked():
        installation.write('draining.json', {'started_at': time.time(), 'reason': 'local_maintenance'})
    import subprocess
    from zf.runtime.controller_actions import cli, await_stopped
    from zf.web.terminal_service import TerminalService
    from zf.integrations.controller.binding import approved_projects
    from zf.integrations.controller.workspace_transport import WorkspaceTransport
    from zf.integrations.controller.commands import digest
    from zf.runtime.workflow_runtime_start import _runtime_owner
    credentials, ledger = installation.credentials(), Ledger(config.state_dir)
    projects = approved_projects(config) if (installation.path / 'policy.json').exists() else config.projects
    unresolved = []
    for context in project_contexts(config, credentials, projects):
        stopped = subprocess.run(cli('stop'), cwd=context.project_root, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False)
        service = TerminalService(state_dir=context.state_dir, project_root=context.project_root,
            project_id=context.config.observability.controller.project_id,
            config=context.config.runtime.web_terminal, allowed_providers=[])
        for row in service.registry.read()['sessions']:
            if row['state'] == 'active':
                service.stop_session(row['session_id'])
        project_id = context.config.observability.controller.project_id
        row = ledger.get('project:' + project_id)
        if stopped.returncode or _runtime_owner(context.state_dir) or not await_stopped(
                ledger, [r for r in ledger.rows('execution') if r['body']['project_id'] == project_id]):
            unresolved.append(project_id)
        elif row and row['status'] != 'unloaded':
            try:
                row = ledger.get(row['id'])
                if row['status'] == 'unloaded':
                    continue
                body = row['body']
                with WorkspaceTransport(config, credentials) as transport:
                    transport.request('POST', '/internal/v1/me/execution-leases/' + body['lease']['id'], {
                        'action': 'release', 'policy_version': body['policy_version'],
                        'result_ref': 'maintenance:' + project_id, 'digest': digest(body)})
                ledger.put(row['id'], 'unloaded', body)
            except Exception:
                unresolved.append(project_id)
    # Existing execution watchdogs stop through their native transport; unknown calls retain liability.
    return {'status': 'draining', 'new_execution': 'closed', 'local_data': 'retained', 'unresolved_projects': unresolved}


def resume(config):
    from zf.integrations.controller.workspace_transport import WorkspaceTransport
    installation = Installation(config)
    credentials = installation.credentials()
    with WorkspaceTransport(config, credentials) as transport:
        transport.handshake()
        result = transport.request('GET', '/internal/v1/me/control')
        if result.get('policy', {}).get('limits', {}).get('management') != 'managed':
            raise ManagedDenied('platform management is no longer granted')
    with installation.locked():
        (installation.path / 'draining.json').unlink(missing_ok=True)
    return {'status': 'maintenance_ended'}


def unenroll(config):
    from zf.integrations.controller.runtime_settings import applied
    config = applied(config)
    import httpx2
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from zf.integrations.controller.collector import Collector

    installation = Installation(config)
    if config.hosting != 'external':
        raise ManagedDenied('cloud workspaces use export and platform deletion, not local unenrollment')
    credentials = installation.credentials()
    if not (installation.path / 'draining.json').exists():
        raise ManagedDenied('drain this installation before release')
    require_quiet(config)
    ledger = Ledger(config.state_dir)
    if any(row['status'] not in {'settled', 'not_started'} for row in ledger.rows('execution')):
        raise ManagedDenied('execution or accounting obligations remain')
    if any(row['status'] != 'unloaded' for row in ledger.rows('project')):
        raise ManagedDenied('loaded Projects must be unloaded before release')
    from zf.integrations.controller.binding import retired_projects
    contexts = project_contexts(config, credentials, sorted(set(config.projects) | set(retired_projects(config))))
    from zf.integrations.controller.outbox import Outbox
    from zf.integrations.controller.workspace import observation
    if Outbox(config.state_dir, observation(config, credentials)).status()['pending_records']:
        raise ManagedDenied('installation receipts must drain before release')
    for context in contexts:
        if Collector(context.state_dir, context.config).outbox.status()['pending_records']:
            raise ManagedDenied('Project receipts must drain before release')
    proof = {key: credentials[key] for key in ('workspace_id', 'deployment_id', 'generation')}
    proof.update(nonce=uuid.uuid4().hex, timestamp=int(time.time()))
    message = json.dumps(proof, sort_keys=True, separators=(',', ':')).encode()
    proof['signature'] = base64.b64encode(installation.key().sign(message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())).decode()
    with httpx2.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        response = client.post(config.endpoint + '/enrollment/v1/release-status', json=proof)
        response.raise_for_status()
        result = response.json()
    if (result.get('protocol') != 'zfc.control.v1' or result.get('allowed') is not True
            or any(result.get(key) != proof[key] for key in ('workspace_id', 'deployment_id', 'generation', 'nonce'))):
        raise ManagedDenied('Controller did not authorize installation release')
    with installation.locked():
        installation.write('release.json', result)
        # Destroy platform machine decryption material; Project files, EventLog and personal login stay local.
        for name in ('binding.json', 'private.pem', 'managed.json'):
            (installation.path / name).unlink(missing_ok=True)
        binding_path().unlink(missing_ok=True)
    for name in ('ZF_CONTROLLER_WORKSPACE_CONFIG', 'ZF_CONTROLLER_CLIENT_SECRET', 'ZF_CONTROLLER_CLIENT_ID',
                 'ZF_CONTROLLER_DEPLOYMENT_ID', 'ZF_CONTROLLER_ISSUER', 'ZF_CONTROLLER_URL', 'ZFC_WORKSPACE_ID'):
        os.environ.pop(name, None)
    for name in list(os.environ):
        if name.startswith(('ZF_CPA_', 'CPA_')) or (config.effective_mode == 'managed_cpa' and name in {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'}):
            os.environ.pop(name, None)
    return {'status': 'unenrolled', 'management': 'standalone', 'local_data': 'retained'}


def require_quiet(config):
    from zf.runtime.workflow_runtime_start import _runtime_owner
    from zf.web.terminal_registry import TerminalRegistry
    for context in project_contexts(config, Installation(config).credentials(), config.projects):
        if _runtime_owner(context.state_dir):
            raise ManagedDenied('stop the existing Project runtime before takeover or release')
        if any(row['state'] == 'active' for row in TerminalRegistry(context.state_dir, project_id=context.config.observability.controller.project_id,
                project_root=context.project_root).read()['sessions']):
            raise ManagedDenied('stop existing native terminals before takeover or release')


def serve(config, *, host, port, ssl_keyfile=None, ssl_certfile=None):
    import uvicorn
    from zf.web.server import create_app
    from zf.integrations.controller.runtime_settings import PolicyNotApplied

    installation = Installation(config)
    if config.hosting == 'cloud' and os.environ.get('ZF_CONTROLLER_CLIENT_SECRET'):
        installation.bootstrap()
    bind(config)
    previous = os.environ.get('ZF_CONTROLLER_WORKSPACE_CONFIG')
    os.environ['ZF_CONTROLLER_WORKSPACE_CONFIG'] = str(config.path)
    stop, thread = threading.Event(), None
    try:
        try:
            synchronize_workspace(config)
        except PolicyNotApplied:
            # Trusted but rejected policy keeps its failed ACK. The connector
            # must stay alive to receive a correction; access still requires ACK.
            pass
        app = create_app(config.state_dir, project_root=config.path.parent, default_project_enabled=False)
        def synchronize():
            from zf.core.config.controller_workspace import load_controller_workspace
            from zf.integrations.controller.runtime_settings import applied
            interval = applied(config).interval_seconds
            while not stop.wait(interval):
                try:
                    synchronize_workspace(load_controller_workspace(config.path))
                    interval = applied(load_controller_workspace(config.path)).interval_seconds
                except Exception:
                    # Access/admission independently fail closed; pending receipts are durable.
                    pass
        thread = threading.Thread(target=synchronize, daemon=True, name='ControllerConnector')
        thread.start()
        uvicorn.run(app, host=host, port=port, ssl_keyfile=ssl_keyfile, ssl_certfile=ssl_certfile)
    finally:
        stop.set()
        if thread:
            thread.join(timeout=15)
        if previous is None:
            os.environ.pop('ZF_CONTROLLER_WORKSPACE_CONFIG', None)
        else:
            os.environ['ZF_CONTROLLER_WORKSPACE_CONFIG'] = previous
