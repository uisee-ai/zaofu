"""Hold the shared Project slot across a real foreground Kernel watcher lifetime."""

from functools import wraps
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from zf.integrations.controller.binding import ManagedDenied, project_binding
from zf.integrations.controller.commands import digest
from zf.integrations.controller.execution import current_policy, process_identity
from zf.integrations.controller.ledger import Ledger
from zf.integrations.controller.workspace_transport import WorkspaceTransport


def managed_runtime(function):
    @wraps(function)
    def invoke(args):
        binding = project_binding(Path.cwd())
        if binding is None or getattr(args, 'dry_run', False):
            return function(args)
        if getattr(args, 'no_watch', False):
            raise ManagedDenied('managed runtimes require the foreground watcher to enforce Project leases')
        config, credentials, context = binding
        ledger, key = Ledger(config.state_dir), 'project:' + context.config.observability.controller.project_id
        stop = threading.Event()
        with WorkspaceTransport(config, credentials) as transport:
            policy = current_policy(config, credentials, transport, key[8:])
            previous = ledger.get(key)
            claim = os.environ.get('ZF_CONTROLLER_PROJECT_LEASE')
            if previous and previous['status'] == 'draining':
                raise ManagedDenied('Project still has an unresolved draining runtime')
            if previous and previous['status'] in {'starting', 'loaded'}:
                body = previous['body']
                if not claim or claim != body['lease']['id']:
                    raise ManagedDenied('Project already has an unresolved runtime lease')
                lease = transport.request('POST', '/internal/v1/me/execution-leases/' + claim, {
                    'action': 'renew', 'policy_version': policy['version'], 'seconds': config.lease_seconds})
            else:
                identifier = 'project-' + uuid.uuid4().hex
                lease = transport.request('POST', '/internal/v1/me/execution-leases', {
                    'request_id': identifier, 'execution_id': identifier, 'kind': 'project',
                    'project_id': key[8:], 'policy_version': policy['version'], 'seconds': config.lease_seconds})
                body = {'project_id': key[8:], 'policy_version': policy['version'], 'lease': lease}
            if not (lease.get('executable') or lease.get('status') == 'held'):
                raise ManagedDenied('Project lease is not executable')
            body.update(pid=os.getpid(), owner=process_identity(os.getpid()))
            ledger.insert(key, 'project', 'starting', body)
            ledger.put(key, 'starting', body)

        def watch():
            deadline = time.monotonic() + min(config.lease_seconds, lease['expires_at'] - time.time())
            while not stop.wait(min(5, max(.05, deadline - time.monotonic()))):
                try:
                    if time.monotonic() >= deadline:
                        raise ManagedDenied('Project lease expired')
                    with WorkspaceTransport(config, credentials) as transport:
                        current_policy(config, credentials, transport, key[8:])
                        renewed = transport.request('POST', '/internal/v1/me/execution-leases/' + body['lease']['id'], {
                            'action': 'renew', 'policy_version': policy['version'], 'seconds': config.lease_seconds})
                        if renewed.get('status') != 'held':
                            raise ManagedDenied('Project lease revoked')
                        deadline = time.monotonic() + min(config.lease_seconds, renewed['expires_at'] - time.time())
                except Exception as exc:
                    if isinstance(exc, ManagedDenied) or getattr(getattr(exc, 'response', None), 'status_code', 0) in {401, 403, 409}:
                        from zf.runtime.controller_actions import cli
                        ledger.put(key, 'draining', body)
                        subprocess.run(cli('stop'), cwd=context.project_root, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False)
                        return
        thread = threading.Thread(target=watch, daemon=True, name='ControllerProjectLease')
        thread.start()
        try:
            return function(args)
        finally:
            stop.set()
            thread.join(timeout=12)
            from zf.runtime.workflow_runtime_start import _runtime_owner
            if not _runtime_owner(context.state_dir):
                try:
                    with WorkspaceTransport(config, credentials) as transport:
                        transport.request('POST', '/internal/v1/me/execution-leases/' + body['lease']['id'], {
                            'action': 'release', 'policy_version': policy['version'],
                            'result_ref': 'runtime:' + body['lease']['id'], 'digest': digest(body)})
                    ledger.put(key, 'unloaded', body)
                except Exception:
                    ledger.put(key, 'draining', body)
    return invoke
