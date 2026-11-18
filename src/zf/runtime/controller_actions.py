"""Controller commands at the existing deterministic action boundary."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def execute(service, payload):
    if service.surface != 'controller' or service.source != 'controller':
        return {'ok': False, 'status': 'denied'}
    from zf.integrations.controller.binding import project_binding
    from zf.integrations.controller.execution import current_policy
    from zf.integrations.controller.ledger import Ledger
    from zf.integrations.controller.workspace_transport import WorkspaceTransport

    binding = project_binding(service.project_root)
    if binding is None:
        return {'ok': False, 'status': 'managed_binding_required'}
    config, credentials, context = binding
    project_id = context.config.observability.controller.project_id
    if payload.get('project_id') != project_id:
        return {'ok': False, 'status': 'project_scope_mismatch'}
    ledger = Ledger(config.state_dir)
    command = ledger.get(payload.get('command_id'))
    if not command or command['kind'] != 'command' or command['status'] != 'executing':
        return {'ok': False, 'status': 'current_command_required'}
    action = payload['action']
    if action in {'project.load', 'project.unload'}:
        with WorkspaceTransport(config, credentials) as transport:
            policy = current_policy(config, credentials, transport, project_id)
            return project_lifecycle(service, payload, config, ledger, transport, policy)
    session, operation = payload.get('session_id'), payload.get('operation_id')
    execution_id = payload.get('execution_id')
    if action not in {'session.stop', 'session.resume', 'operation.cancel'}:
        return {'ok': False, 'status': 'unsupported'}
    if not session and not operation:
        return {'ok': False, 'status': 'target_required'}
    events = service.writer.event_log.read_all()
    matches = [event for event in events if (
        (not session or session in {event.payload.get('thread_id'), event.payload.get('session_id'),
                                   event.payload.get('provider_session_id')})
        and (not operation or operation in {event.payload.get('operation_id'), event.payload.get('run_id')})
    )]
    targeted = [row for row in ledger.rows('execution') if row['body']['project_id'] == project_id
                and (not session or session in {row['body'].get('session_id'), row['body'].get('native_session_id')})
                and (not operation or operation in {row['id'], row['body'].get('parent_operation_id')})
                and (not execution_id or execution_id == row['id'])]
    if (not matches and not targeted) or (execution_id and not targeted):
        return {'ok': False, 'status': 'target_not_found'}
    if action == 'operation.cancel':
        session = None
    key = 'fence:' + project_id + ':' + ('session:' + session if session else 'operation:' + operation)
    body = {'project_id': project_id, 'session_id': session, 'operation_id': operation,
            'command_id': payload['command_id'], 'execution_id': execution_id if action == 'operation.cancel' else None}
    status = 'open' if action == 'session.resume' else 'closed'
    ledger.insert(key, 'fence', status, body)
    ledger.put(key, status, body)
    if status == 'closed' and not await_stopped(ledger, targeted):
        return {'ok': False, 'status': 'stop_unconfirmed', 'effect': 'admission_closed'}
    # A resume removes the Session admission fence. The next user/Kernel turn uses the
    # original native Session; this command never fabricates a new semantic prompt.
    event = service.writer.emit('runtime.action.completed', actor=service.actor, payload={
        'action': action, 'status': 'completed', **body})
    return {'ok': True, 'status': 'completed', 'event_id': event.id,
            'effect': 'admission_open' if status == 'open' else 'execution_stopped'}


def project_lifecycle(service, payload, config, ledger, transport, policy):
    project_id = payload['project_id']
    key = 'project:' + project_id
    old = ledger.get(key)
    if payload['action'] == 'project.load' and old and old['status'] != 'unloaded':
        from zf.integrations.controller.installation import Installation
        from zf.integrations.controller.project_lease import retire_released
        retire_released(ledger, key, transport, policy, Installation(config).credentials(), service.state_dir)
        old = ledger.get(key)
    if payload['action'] == 'project.load' and old and old['status'] == 'loaded':
        from zf.integrations.controller.execution import process_identity
        owner = old['body'].get('owner', {})
        if owner.get('start') and owner == process_identity(old['body'].get('pid', 0)):
            transport.request('POST', '/internal/v1/me/execution-leases/' + old['body']['lease']['id'], {
                'action': 'renew', 'policy_version': policy['version'], 'seconds': config.lease_seconds})
            return {'ok': True, 'status': 'completed', 'effect': 'already_loaded'}
        return {'ok': False, 'status': 'previous_runtime_unresolved'}
    if payload['action'] == 'project.load':
        if old and old['status'] != 'unloaded':
            return {'ok': False, 'status': 'previous_runtime_unresolved'}
        lease = transport.request('POST', '/internal/v1/me/execution-leases', {
            'request_id': payload['command_id'], 'execution_id': payload['command_id'], 'kind': 'project',
            'project_id': project_id, 'policy_version': policy['version'], 'seconds': config.lease_seconds})
        if not lease.get('executable'):
            return {'ok': False, 'status': 'lease_denied'}
        body = {'project_id': project_id, 'lease': lease, 'policy_version': policy['version']}
        ledger.insert(key, 'project', 'starting', body)
        ledger.put(key, 'starting', body)
        env = os.environ.copy()
        env.update(ZF_CONTROLLER_WORKSPACE_CONFIG=str(config.path), ZF_PROJECT_ROOT=str(service.project_root),
                   ZF_STATE_DIR=str(service.state_dir), ZF_CONTROLLER_PROJECT_LEASE=lease['id'])
        log = config.state_dir / 'installation' / (payload['command_id'] + '.log')
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            child = subprocess.Popen(cli('start'), cwd=service.project_root, env=env,
                                     stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
        body['pid'] = child.pid
        from zf.integrations.controller.execution import process_identity
        body['owner'] = process_identity(child.pid)
        ledger.put(key, 'starting', body)
        guard = service.state_dir / 'processes' / 'watcher.pid.json'
        for _ in range(150):
            if child.poll() is not None:
                return {'ok': False, 'status': 'runtime_start_failed'}
            if guard.exists():
                try:
                    record = json.loads(guard.read_text())
                    if record.get('owner_pid') == child.pid and any(
                            event.type == 'loop.started' and event.ts >= command_time(payload['command_id'], ledger)
                            for event in service.writer.event_log.read_all()):
                        ledger.put(key, 'loaded', body)
                        return {'ok': True, 'status': 'completed', 'effect': 'watcher_started', 'pid': child.pid}
                except (ValueError, OSError):
                    pass
            time.sleep(0.2)
        return {'ok': False, 'status': 'runtime_start_unknown'}
    body = old['body'] if old else {'project_id': project_id}
    ledger.insert(key, 'project', 'draining', body)
    ledger.put(key, 'draining', body)
    result = subprocess.run(cli('stop'), cwd=service.project_root, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False)
    from zf.runtime.workflow_runtime_start import _runtime_owner
    if result.returncode or _runtime_owner(service.state_dir):
        return {'ok': False, 'status': 'runtime_stop_unconfirmed'}
    if not await_stopped(ledger, [row for row in ledger.rows('execution') if row['body']['project_id'] == project_id]):
        return {'ok': False, 'status': 'execution_stop_unconfirmed'}
    current = ledger.get(key)
    if body.get('lease') and current['status'] != 'unloaded':
        from zf.integrations.controller.commands import digest
        transport.request('POST', '/internal/v1/me/execution-leases/' + body['lease']['id'], {
            'action': 'release', 'policy_version': body['policy_version'],
            'result_ref': 'command:' + payload['command_id'], 'digest': digest(body)})
    ledger.put(key, 'unloaded', current['body'])
    return {'ok': True, 'status': 'completed', 'effect': 'runtime_stopped'}


def cli(command):
    return [sys.executable, '-c', 'from zf.cli.main import main; raise SystemExit(main())', command]


def await_stopped(ledger, rows, seconds=12):
    deadline = time.monotonic() + seconds
    while any(ledger.get(row['id'])['status'] in {'preparing', 'running'} for row in rows):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def command_time(identifier, ledger):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ledger.get(identifier)['updated'], timezone.utc).isoformat()
