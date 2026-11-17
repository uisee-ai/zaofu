"""Managed interactive terminal responsibility lasts until its native process stops."""

from functools import wraps
import threading
import time

from zf.integrations.controller.binding import ManagedDenied, project_binding
from zf.integrations.controller.execution import Execution
from zf.integrations.controller.native import delta


def managed_terminal(function):
    @wraps(function)
    def invoke(service, *args, **kwargs):
        binding = project_binding(service.project_root)
        if binding is None:
            return function(service, *args, **kwargs)
        records = service.registry.read()['sessions']
        active = next((row for row in records if row.get('slot') == kwargs['slot'] and row.get('state') == 'active'), None)
        if active:
            from zf.integrations.controller.ledger import Ledger
            held = [row for row in Ledger(binding[0].state_dir).rows('execution')
                    if row['status'] == 'running' and row['body'].get('session_id') == active['session_id']]
            if not held or held[0]['body']['budget']['expires_at'] <= time.time():
                raise ManagedDenied('existing terminal has no current managed grant')
            return function(service, *args, **kwargs)
        holder = {}
        def cancel():
            if holder.get('record'):
                service.stop_session(holder['record'].session_id)
        execution = Execution(binding, operation_id='', provider=kwargs['provider'], model='', source='terminal').begin(cancel)
        try:
            record = function(service, *args, **kwargs)
            holder['record'] = record
            execution.session_id = record.session_id
            execution.body['session_id'] = record.session_id
            execution.body['native_session_id'] = record.provider_session_id
            execution.ledger.put(execution.id, 'running', execution.body)
            if execution.revoked:
                cancel()
            threading.Thread(target=observe, args=(service, record, execution), daemon=True,
                             name='ControllerTerminalReceipt-' + execution.id).start()
            return record
        except BaseException:
            cancel()
            execution.finish({}, status='unknown')
            raise
    return invoke


def observe(service, record, execution):
    while True:
        current = service.get_session(record.session_id)
        counters = service.usage.snapshot(current)
        if counters.get('total_tokens', 0) is not None and counters.get('total_tokens', 0) >= execution.config.request_tokens:
            current = service.stop_session(record.session_id)
        if current.state != 'active':
            from zf.runtime.codex_turn_boundary import completed_session
            native = service.usage.native_snapshot(provider=current.provider,
                session_id=current.provider_session_id, project_root=service.project_root)
            final = native if native.get('status') == 'observed' else counters
            execution.finish(delta({}, final, fresh=True), status=current.state,
                native_session=current.provider_session_id, accounting_mode=final.get('accounting_mode', 'unknown'),
                fresh_input=True, usage_complete=current.provider == 'codex'
                and completed_session(native, session_id=current.provider_session_id))
            return
        time.sleep(1)
