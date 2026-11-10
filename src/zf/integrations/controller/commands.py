"""Current machine commands enter ControlledActionService once, with durable receipts."""

import hashlib
import json
import re
import time

from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.integrations.controller.ledger import Ledger


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def receipt(transport, identifier, status, result=None, reason=None):
    body = {'protocol': 'zfc.control.v1', 'status': status}
    if status == 'failed' and reason is None:
        reason = 'execution_failed'
    if result is not None:
        body.update(result_ref='connector-command:' + identifier, digest=digest(result))
    if reason:
        body['reason'] = reason
    return transport.request('POST', '/internal/v1/me/control/commands/' + identifier, body)


def consume_commands(config, credentials, transport, policy, contexts, commands):
    from zf.runtime.control_actions import ControlledActionService

    ledger = Ledger(config.state_dir)
    projects = {ctx.config.observability.controller.project_id: ctx for ctx in contexts}
    offered = {row['id']: row for row in commands}
    for row in ledger.rows('command'):
        if row['status'] != 'receipted':
            offered.setdefault(row['id'], row['body']['command'])
    for identifier, command in offered.items():
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', identifier):
            raise ValueError('invalid command identifier')
        row = ledger.get(identifier)
        if row and row['body']['command'] != command:
            # Transport status/receipt may change, but the authority and action must not.
            protected = ('payload', 'generation', 'deployment_id', 'policy_version', 'expires_at', 'user_id', 'session_id')
            if any(row['body']['command'].get(key) != command.get(key) for key in protected):
                raise ValueError('command identity conflict')
        if row and row['status'] in {'completed', 'failed', 'receipted'}:
            if row['status'] != 'receipted':
                receipt(transport, identifier, row['status'], row['body']['result'])
                ledger.put(identifier, 'receipted', row['body'])
            continue
        payload = command.get('payload', {})
        context = projects.get(payload.get('project_id'))
        valid = (command.get('workspace_id') == config.workspace_id
                 and command.get('deployment_id') == credentials['deployment_id']
                 and command.get('generation') == credentials['generation']
                 and command.get('policy_version') == policy['version']
                 and command.get('expires_at', 0) > time.time() and context is not None)
        if not valid:
            if not row:
                receipt(transport, identifier, 'rejected', reason='stale_version')
            elif row['status'] in {'accepted', 'executing'}:
                body = {**row['body'], 'result': {'ok': False, 'status':
                    'unknown_execution' if row['status'] == 'executing' else 'authority_expired_before_execution'}}
                ledger.put(identifier, 'failed', body)
                receipt(transport, identifier, 'failed', body['result'])
                ledger.put(identifier, 'receipted', body)
            continue
        result = transport.request('POST', '/internal/v1/me/control/commands/' + identifier + '/authorize')
        if (result.get('protocol') != 'zfc.control.v1' or result.get('authorized') is not True
                or result.get('command', {}).get('payload') != payload
                or result.get('command', {}).get('id') != identifier):
            raise ValueError('command is no longer authorized')
        if row and row['status'] == 'executing':
            # The process may have died after the side effect. Do not repeat an unproven action.
            body = {**row['body'], 'result': {'ok': False, 'status': 'unknown_execution'}}
            ledger.put(identifier, 'failed', body)
            receipt(transport, identifier, 'failed', body['result'], 'execution_failed')
            ledger.put(identifier, 'receipted', body)
            continue
        body = {'command': command}
        ledger.insert(identifier, 'command', 'offered', body)
        receipt(transport, identifier, 'accepted')
        ledger.put(identifier, 'accepted', body)
        receipt(transport, identifier, 'running')
        ledger.put(identifier, 'executing', body)
        writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
        requested = writer.append(ZfEvent(type='runtime.action.requested', actor='controller-connector',
            payload={'action': 'controller-command', 'command_id': identifier, **payload}))
        service = ControlledActionService(context.state_dir, writer, config=context.config,
            project_root=context.project_root, actor='controller-connector', source='controller', surface='controller')
        try:
            outcome = service.execute(action='controller-command', requested_action=payload['action'],
                payload={**payload, 'command_id': identifier}, requested=requested)
        except Exception as exc:
            outcome = {'ok': False, 'status': 'execution_failed', 'error_type': type(exc).__name__}
        finally:
            writer.event_log.close()
        body['result'] = outcome
        status = 'completed' if outcome.get('ok') and outcome.get('status') == 'completed' else 'failed'
        ledger.put(identifier, status, body)
        receipt(transport, identifier, status, outcome, None if status == 'completed' else 'execution_failed')
        ledger.put(identifier, 'receipted', body)
