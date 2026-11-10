"""Managed provider-call admission. Shared rights live in Controller, facts in EventLog."""

from functools import wraps
import hashlib
import json
import os
import threading
import time
import uuid

from zf.integrations.controller.binding import ManagedDenied, project_binding
from zf.integrations.controller.collector import Collector
from zf.integrations.controller.ledger import Ledger
from zf.integrations.controller.workspace_transport import WorkspaceTransport


def current_policy(config, credentials, transport, project_id):
    from zf.integrations.controller.installation import Installation, private_read

    saved = json.loads(private_read(Installation(config).path / 'policy.json'))
    result = transport.request('GET', '/internal/v1/me/control')
    policy = result.get('policy', {})
    if (result.get('protocol') != 'zfc.control.v1'
            or result.get('deployment_id') != credentials['deployment_id']
            or result.get('generation') != credentials['generation']
            or policy.get('workspace_id') != config.workspace_id
            or policy.get('version') != saved.get('version')
            or policy.get('applied_version') != policy.get('version')
            or policy.get('applied_deployment') != credentials['deployment_id']
            or policy.get('limits', {}).get('management') != 'managed'
            or not {'budget', 'commands'}.issubset(policy.get('capabilities', []))
            or project_id not in policy.get('projects', [])
            or (config.hosting == 'external' and project_id not in config.projects)):
        raise ManagedDenied('current managed policy is not applied to this Project')
    if (Installation(config).path / 'draining.json').exists():
        raise ManagedDenied('installation is draining; new execution is closed')
    return policy


def check_project(root):
    binding = project_binding(root)
    if binding is not None:
        config, credentials, context = binding
        Collector(context.state_dir, context.config).outbox.admit()
        with WorkspaceTransport(config, credentials) as transport:
            project_id = context.config.observability.controller.project_id
            policy = current_policy(config, credentials, transport, project_id)
            result = transport.request('POST', '/internal/v1/me/execution-readiness', {
                'project_id': project_id, 'policy_version': policy['version'], 'mode': config.mode,
                'price_version': config.price_version, 'tokens': config.request_tokens, 'money': config.request_money})
            if (result.get('protocol') != 'zfc.control.v1' or result.get('executable') is not True
                    or result.get('deployment_id') != credentials['deployment_id']
                    or result.get('generation') != credentials['generation']
                    or result.get('project_id') != project_id or result.get('policy_version') != policy['version']):
                raise ManagedDenied('current execution entitlement could not be proven')
    return binding


def stream_turn(call, *, root, context, role, session_id, cancel):
    binding = project_binding(root)
    if binding is None:
        return call()
    execution = Execution(binding, operation_id=context.operation_id or context.run_id or '',
                          provider='claude-code', model=getattr(role, 'model', ''),
                          session_id=session_id, source='workflow').begin(cancel)
    result = None
    try:
        result = call()
        return result
    finally:
        messages = result[0] if result else []
        usage = next((getattr(message, 'usage') for message in reversed(messages)
                      if isinstance(getattr(message, 'usage', None), dict)), {})
        execution.finish(usage, status=result[1].value if result else 'unknown', native_session=session_id)


class Execution:
    def __init__(self, binding, *, operation_id, provider, model, session_id='', source='helper'):
        self.config, self.credentials, self.context = binding
        self.id = 'zfc-' + uuid.uuid4().hex
        self.operation_id = operation_id
        self.provider, self.model = provider, model or 'unknown'
        self.session_id, self.source = session_id, source
        self.ledger = Ledger(self.config.state_dir)
        self.collector = Collector(self.context.state_dir, self.context.config)
        self.stop = threading.Event()
        self.thread = None
        self.revoked = False
        self.body = {'operation_id': self.id, 'parent_operation_id': operation_id,
                     'project_id': self.collector.cloud.project_id, 'provider': provider,
                     'model': self.model, 'session_id': session_id, 'source': source,
                     'deployment_id': self.credentials['deployment_id'], 'generation': self.credentials['generation'],
                     'owner': process_identity(os.getpid())}

    def begin(self, cancel):
        try:
            return self._begin(cancel)
        except Exception:
            row = self.ledger.get(self.id)
            if row and 'started_at' not in self.body:
                self.body['result'] = 'not_started'
                self.ledger.put(self.id, row['status'], self.body)
            raise

    def _begin(self, cancel):
        self.check_fences()
        self.collector.outbox.admit()
        self.ledger.admit_execution(self.id, self.body)
        try:
            self.collector.outbox.reserve_capacity(self.id)
        except Exception:
            self.ledger.put(self.id, 'not_started', self.body)
            raise
        with WorkspaceTransport(self.config, self.credentials) as transport:
            policy = current_policy(self.config, self.credentials, transport, self.body['project_id'])
            self.body['policy_version'] = policy['version']
            self.body['lease_request'] = {
                'request_id': self.id, 'execution_id': self.id,
                'kind': 'agent', 'project_id': self.body['project_id'], 'policy_version': policy['version'],
                'seconds': self.config.lease_seconds}
            self.ledger.put(self.id, 'preparing', self.body)
            lease = transport.request('POST', '/internal/v1/me/execution-leases', self.body['lease_request'])
            if not lease.get('executable') or lease.get('execution_id') != self.id:
                raise ManagedDenied('execution lease is not executable')
            self.body['lease'] = lease
            self.ledger.put(self.id, 'preparing', self.body)
            try:
                self.body['budget_request'] = {
                    'protocol': 'zfc.control.v1', 'request_id': self.id, 'operation_id': self.id,
                    'project_id': self.body['project_id'], 'mode': self.config.mode,
                    'provider': self.provider, 'model': self.model, 'policy_version': policy['version'],
                    'max_tokens': self.config.request_tokens, 'max_money': self.config.request_money,
                    'price_version': self.config.price_version, 'expires_in': self.config.request_seconds}
                self.ledger.put(self.id, 'preparing', self.body)
                budget = transport.request('POST', '/internal/v1/me/budgets/reservations', self.body['budget_request'])
                if not budget.get('executable') or budget.get('input', {}).get('operation_id') != self.id:
                    raise ManagedDenied('budget reservation is not executable')
            except Exception as exc:
                # No provider has started, so freeing the execution slot is provable. A lost
                # reservation response remains an unknown financial liability on Controller.
                self.body['result'] = 'not_started'
                denied = getattr(getattr(exc, 'response', None), 'status_code', 0) in {400, 401, 403, 409, 422}
                self.ledger.put(self.id, 'not_started' if denied else 'preparing', self.body)
                if denied:
                    self.collector.outbox.release_capacity(self.id)
                self.release(transport)
                raise
            self.body['budget'] = budget
            self.body['started_at'] = time.time()
            self.ledger.put(self.id, 'running', self.body)
        self.lifecycle('started')
        self.thread = threading.Thread(target=self.watch, args=(cancel,), daemon=True,
                                       name='ControllerLease-' + self.id)
        self.thread.start()
        return self

    def watch(self, cancel):
        # Budget liability cannot be extended without a new reservation. Stop at its expiry;
        # execution lease renewal never turns an expired budget into an executable grant.
        budget_deadline = time.monotonic() + min(self.config.request_seconds, self.body['budget']['expires_at'] - time.time())
        deadline = min(time.monotonic() + min(self.config.lease_seconds, self.body['lease']['expires_at'] - time.time()), budget_deadline)
        while not self.stop.wait(min(5, max(0.05, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                self.revoked = True
                cancel()
                return
            try:
                self.check_fences()
                with WorkspaceTransport(self.config, self.credentials) as transport:
                    current_policy(self.config, self.credentials, transport, self.body['project_id'])
                    updated = transport.request('POST', '/internal/v1/me/execution-leases/' + self.body['lease']['id'], {
                        'action': 'renew', 'policy_version': self.body['policy_version'],
                        'seconds': self.config.lease_seconds})
                    if updated.get('status') != 'held' or updated.get('execution_id') != self.id:
                        raise ManagedDenied('execution lease revoked')
                    deadline = min(time.monotonic() + min(self.config.lease_seconds, updated['expires_at'] - time.time()), budget_deadline)
            except Exception as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if isinstance(exc, ManagedDenied) or status in {401, 403, 409}:
                    self.revoked = True
                    cancel()
                    return
                # Transport failure does not extend either signed-in-time server grant.

    def check_fences(self):
        project = self.ledger.get('project:' + self.body['project_id'])
        if project and project['status'] in {'draining', 'unloaded'}:
            raise ManagedDenied('Project is unloaded or draining')
        for row in self.ledger.rows('fence'):
            target = row['body']
            if (row['status'] == 'closed' and target['project_id'] == self.body['project_id']
                    and (not target.get('execution_id') or target['execution_id'] == self.id)
                    and ((target.get('session_id') and target['session_id'] == self.session_id)
                         or (target.get('operation_id') and target['operation_id'] in {self.id, self.operation_id}))):
                raise ManagedDenied('Session or Operation admission is closed')

    def finish(self, usage, *, status, native_session='', accounting_mode='unknown', fresh_input=False):
        from zf.core.events import EventWriter
        from zf.core.events.factory import event_log_from_project

        self.stop.set()
        if self.thread:
            self.thread.join(timeout=12)
        payload = {'operation_id': self.id, 'run_id': self.operation_id or None,
                   'session_id': self.session_id or native_session or None,
                   'provider_session_id': native_session or None,
                   'backend': self.provider, 'provider': self.provider, 'model': self.model,
                   'usage': usage or {}, 'usage_sample_id': self.id, 'usage_scope': 'exclusive',
                   'accounting_mode': accounting_mode, 'budget_reservation_id': self.body['budget']['id']}
        if fresh_input:
            payload['input_semantics'] = 'fresh_plus_cache'
        payload['price_version'] = self.config.price_version
        from zf.integrations.controller.pricing import cost_fields
        payload.update(cost_fields(self.context, self.config, self.provider, self.model, usage, accounting_mode, fresh_input))
        self.body.update(result=status, receipt=payload, finished_at=time.time(), native_session_id=native_session,
                         record_id=hashlib.sha256(
            (self.body['project_id'] + ':' + self.id).encode()).hexdigest())
        # Save receipt before EventLog append so recovery can finish a crash in between.
        self.ledger.put(self.id, 'receipt_pending', self.body)
        log = event_log_from_project(self.context.state_dir, config=self.context.config)
        try:
            EventWriter(log).emit('controller.usage.receipt', actor='controller-connector', payload=payload)
        finally:
            log.close()
        self.lifecycle('finished', status=status, native_session=native_session)
        self.collector.outbox.release_capacity(self.id)
        self.ledger.put(self.id, 'settlement_pending', self.body)
        try:
            with WorkspaceTransport(self.config, self.credentials) as transport:
                self.release(transport)
        except Exception:
            pass  # Durable obligation, never report a remote release that was not acknowledged.

    def lifecycle(self, phase, *, status='', native_session=''):
        from zf.core.events import EventWriter
        from zf.core.events.factory import event_log_from_project
        log = event_log_from_project(self.context.state_dir, config=self.context.config)
        try:
            EventWriter(log).emit('controller.execution.' + phase, actor='controller-connector', payload={
                'operation_id': self.id, 'run_id': self.operation_id or None,
                'thread_id': self.session_id or native_session or None, 'provider_session_id': native_session or None,
                'backend': self.provider, 'model': self.model, 'source': self.source,
                'status': status, 'usage': {'_controller_accounted': True}})
        finally:
            log.close()

    def release(self, transport):
        proof = self.body.setdefault('release_digest', hashlib.sha256(
            json.dumps(self.body, sort_keys=True).encode()).hexdigest())
        self.ledger.put(self.id, self.ledger.get(self.id)['status'], self.body)
        result = transport.request('POST', '/internal/v1/me/execution-leases/' + self.body['lease']['id'], {
            'action': 'release', 'policy_version': self.body['policy_version'],
            'result_ref': 'connector:' + self.id, 'digest': proof})
        self.body['release'] = result
        self.ledger.put(self.id, self.ledger.get(self.id)['status'], self.body)


def managed_turn(function):
    """Runs at the backend call boundary, including continuations and resumed Sessions."""
    @wraps(function)
    def invoke(self, *args, **kwargs):
        binding = project_binding(kwargs['cwd'])
        if binding is None:
            return function(self, *args, **kwargs)
        from zf.web.agent_session_runtime import cancel_agent_session_run, run_key

        values = dict(kwargs)
        operation = values.get('run_id') or 'helper-' + uuid.uuid4().hex
        values['run_id'] = operation
        key = run_key(run_id=operation, thread_id=values.get('run_thread_id') or values['thread_id'],
                      project_id=values.get('project_id', ''), conversation_id=values.get('conversation_id', ''))
        provider = self.backend_id.split('-')[0]
        native_provider = 'claude-code' if provider == 'claude' else provider
        from zf.integrations.controller.native import snapshot, delta
        previous_id = values.get('provider_session_id', '')
        before = snapshot(binding[2], native_provider, previous_id)
        execution = Execution(binding, operation_id=operation, provider=provider,
                              model=values.get('model', ''), session_id=values['thread_id'])
        execution.body.update(native_before=before, native_session_id=previous_id)
        execution.begin(lambda: cancel_agent_session_run(key))
        callback = values.get('on_session_id')
        def native_identified(identifier):
            execution.body['native_session_id'] = identifier
            execution.ledger.put(execution.id, 'running', execution.body)
            if callback:
                callback(identifier)
        values['on_session_id'] = native_identified
        # Provider deadline and registry cancellation both bound a lost control channel.
        budget_deadline = time.monotonic() + max(0, execution.body['budget']['expires_at'] - time.time())
        values['execution_deadline'] = min(values.get('execution_deadline') or budget_deadline, budget_deadline)
        result = None
        try:
            result = function(self, *args, **values)
            return result
        finally:
            usage = dict(getattr(result, 'usage', {}) or {})
            native_id = getattr(result, 'provider_session_id', '') or execution.body.get('native_session_id', '')
            after = snapshot(binding[2], native_provider, native_id)
            native_usage = delta(before, after, fresh=not previous_id or native_id != previous_id)
            if native_usage:
                usage = native_usage
            else:
                from zf.runtime.provider_usage import normalize_provider_usage
                # A Codex last-call counter is not an entire tool-using turn receipt.
                normalized = normalize_provider_usage(usage, backend=provider)
                usage = normalized['turn'] if normalized['mode'] == 'per_turn' else {}
            execution.finish(usage, status=getattr(result, 'status', 'unknown'),
                             native_session=native_id, accounting_mode=after.get('accounting_mode', 'unknown'),
                             fresh_input=bool(native_usage))
            if result is not None:
                # Preserve local cost counters; the aggregate Session event must not upload them twice.
                result.usage['_controller_accounted'] = True
    return invoke


def reconcile_executions(config, credentials, transport, contexts):
    """Retry only receipts/releases; never replay a provider side effect after a crash."""
    from zf.core.events import EventWriter
    from zf.core.events.factory import event_log_from_project

    ledger = Ledger(config.state_dir)
    projects = {ctx.config.observability.controller.project_id: ctx for ctx in contexts}
    unresolved = 0
    for row in ledger.rows('execution'):
        body = row['body']
        if row['status'] == 'settled':
            continue
        context = projects.get(body['project_id'])
        if (context and body['deployment_id'] == credentials['deployment_id']
                and row['status'] in {'preparing', 'not_started'}
                and (body.get('result') == 'not_started'
                     or process_identity(body.get('owner', {}).get('pid', 0)) != body.get('owner'))):
            try:
                if body.get('lease_request') and not body.get('lease'):
                    body['lease'] = transport.request('POST', '/internal/v1/me/execution-leases', body['lease_request'])
                if body.get('budget_request') and not body.get('budget') and row['status'] != 'not_started':
                    body['budget'] = transport.request('POST', '/internal/v1/me/budgets/reservations', body['budget_request'])
                # Recovered grant, but the write-ahead running boundary was never crossed.
                execution = Execution((config, credentials, context), operation_id=body['parent_operation_id'],
                    provider=body['provider'], model=body['model'], session_id=body['session_id'], source=body['source'])
                execution.id, execution.body = row['id'], body
                if body.get('budget'):
                    execution.finish({'input_tokens': 0, 'output_tokens': 0}, status='not_started')
                else:
                    if body.get('lease') and not body.get('release'):
                        execution.release(transport)
                    ledger.put(row['id'], 'not_started', execution.body)
                    execution.collector.outbox.release_capacity(row['id'])
                    continue
                row = ledger.get(row['id'])
                body = row['body']
            except Exception:
                unresolved += 1
                continue
        if row['status'] == 'not_started' and (not body.get('lease') or body.get('release')):
            continue
        if (body['deployment_id'] != credentials['deployment_id'] or not context
                or row['status'] not in {'receipt_pending', 'settlement_pending'}):
            unresolved += 1
            continue
        if row['status'] == 'receipt_pending':
            log = event_log_from_project(context.state_dir, config=context.config)
            try:
                writer = EventWriter(log)
                writer.emit('controller.usage.receipt', actor='controller-connector', payload=body['receipt'])
                writer.emit('controller.execution.finished', actor='controller-connector', payload={
                    'operation_id': row['id'], 'run_id': body.get('parent_operation_id') or None,
                    'thread_id': body.get('session_id') or body.get('native_session_id') or None,
                    'provider_session_id': body.get('native_session_id'), 'backend': body['provider'],
                    'model': body['model'], 'source': body['source'], 'status': body['result'],
                    'usage': {'_controller_accounted': True}})
            finally:
                log.close()
            Collector(context.state_dir, context.config).outbox.release_capacity(row['id'])
            ledger.put(row['id'], 'settlement_pending', body)
        try:
            if not body.get('release'):
                proof = body.setdefault('release_digest', hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest())
                ledger.put(row['id'], 'settlement_pending', body)
                body['release'] = transport.request('POST', '/internal/v1/me/execution-leases/' + body['lease']['id'], {
                    'action': 'release', 'policy_version': body['policy_version'],
                    'result_ref': 'connector:' + row['id'], 'digest': proof})
                ledger.put(row['id'], 'settlement_pending', body)
            result = transport.request('POST', '/internal/v1/me/budgets/reservations/' + body['budget']['id'] + '/settle', {
                'protocol': 'zfc.control.v1', 'origin_id': body['deployment_id'], 'record_id': body['record_id']})
            if result.get('status') != 'settled':
                raise ManagedDenied('budget settlement was not acknowledged')
            body['settlement'] = result
            ledger.put(row['id'], 'settled', body)
        except Exception:
            unresolved += 1
    return unresolved


def process_identity(pid):
    from pathlib import Path
    try:
        # Linux start ticks distinguish a new process reusing an old PID.
        return {'pid': pid, 'start': Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]}
    except OSError:
        return {'pid': pid, 'start': None}
