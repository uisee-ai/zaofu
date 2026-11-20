"""Lease each TUI dispatch and settle only a proven native turn boundary."""

from functools import wraps
from pathlib import Path
import threading
import time

from zf.integrations.controller.binding import ManagedDenied, project_binding
from zf.integrations.controller.execution import Execution
from zf.integrations.controller.native import role_snapshot, delta


def managed_dispatch(function):
    @wraps(function)
    def invoke(transport, role_name, *args, **kwargs):
        root = transport._expected_cwd_for(role_name) or Path.cwd()
        binding = project_binding(root)
        if binding is None or transport.dry_run:
            return function(transport, role_name, *args, **kwargs)
        from zf.core.state.role_sessions import RoleSessionRegistry

        context = kwargs.get('context')
        project = binding[2]
        # Generated resident/fanout roles are registered by the runtime transport;
        # reloading only static zf.yaml roles loses their actual launch contract.
        resolve = getattr(transport, 'execution_role', None)
        role = resolve(role_name) if callable(resolve) else None
        role = role or next((role for role in project.config.roles if role.instance_id == role_name), None)
        if role is None:
            raise ManagedDenied('TUI role is not declared in the managed Project')
        registry = RoleSessionRegistry(project.state_dir / 'role_sessions.yaml', project.project_root)
        native = str(registry.get(role_name) or '')
        before = role_snapshot(project, role, native, cwd=root)
        execution = Execution(binding, operation_id=getattr(context, 'operation_id', '') or '',
            provider=role.backend, model=getattr(role, 'model', ''), session_id=native, source='workflow')
        execution.body['actor'] = role_name
        execution.body['native_session_id'] = native
        execution.body['native_before'] = before
        execution.body['native_project_root'] = str(root)
        execution.begin(lambda: transport.terminate(role_name))
        try:
            result = function(transport, role_name, *args, **kwargs)
        except BaseException:
            # Sending may have partially succeeded. Stop the exact worker and retain missing usage.
            transport.terminate(role_name)
            execution.finish({}, status='unknown', native_session=native)
            raise
        if result is False:
            execution.finish({'input_tokens': 0, 'output_tokens': 0}, status='not_started', native_session=native)
            return result
        threading.Thread(target=observe, args=(transport, role_name, registry, role, before, execution),
                         daemon=True, name='ControllerTuiReceipt-' + execution.id).start()
        return result
    return invoke


def observe(transport, role_name, registry, role, before, execution):
    from datetime import datetime
    from zf.core.events.factory import event_log_from_project
    from zf.runtime.codex_turn_boundary import completed_turn, first_turn

    context = execution.context
    log = event_log_from_project(context.state_dir, config=context.config)
    try:
        while True:
            stopped = not transport.is_alive(role_name)
            # A persistent TUI can finish one native turn while remaining alive.
            # Read identity and counters together; partial token updates are not completion.
            registry = type(registry)(registry.path, registry.project_root)
            native = str(registry.get(role_name) or execution.session_id)
            after = role_snapshot(context, role, native, cwd=execution.body.get('native_project_root'))
            complete = completed_turn(before, after, started_at=execution.body['started_at'], session_id=native)
            terminal = any(
                event.type in {'orchestrator.round.complete', 'workflow.operation.settled',
                               'workflow.operation.cancelled', 'workflow.operation.failed', 'workflow.operation.interrupted'}
                and datetime.fromisoformat(event.ts.replace('Z', '+00:00')).timestamp() >= execution.body['started_at']
                and (event.actor == role_name or (execution.operation_id and event.payload.get('operation_id') == execution.operation_id))
                for event in log.read_all())
            if stopped or complete or (terminal and role.backend != 'codex'):
                try:
                    fresh = datetime.fromisoformat(after.get('session_started_at', '').replace('Z', '+00:00')).timestamp() >= execution.body['started_at']
                except ValueError:
                    fresh = False
                # The shell may start the native session before the first dispatch.
                # A proven first complete turn provides a zero baseline; an interrupted
                # prior turn without counters still retains its unknown liability.
                fresh = fresh or first_turn(after, started_at=execution.body['started_at'], session_id=native)
                usage = delta(before, after, fresh=fresh)
                execution.finish(usage, status='stopped' if stopped else 'completed', native_session=native,
                                 accounting_mode=after.get('accounting_mode', 'unknown'), fresh_input=True,
                                 subscription_windows=after.get('subscription_windows'), usage_complete=complete if role.backend == 'codex' else terminal)
                return
            time.sleep(1)
    finally:
        log.close()
