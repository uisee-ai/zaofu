"""Admission and cancellable execution for provider CLIs used by auxiliary callers."""

import json
import os
from pathlib import Path
import signal
import subprocess

from zf.integrations.controller.binding import managed_config, project_binding, approved_projects, ManagedDenied
from zf.integrations.controller.execution import Execution, process_identity


def run_provider(command, *, backend, state_dir=None, model='', runner=None,
                 plain_output=False, **kwargs):
    config = managed_config()
    if config is None:
        return (runner or subprocess.run)(command, **kwargs)
    from zf.integrations.controller.installation import Installation
    from zf.integrations.controller.workspace import project_contexts
    if state_dir is not None:
        credentials = Installation(config).credentials()
        contexts = project_contexts(config, credentials, approved_projects(config))
        context = next((ctx for ctx in contexts if ctx.state_dir == Path(state_dir).resolve()), None)
        if context is None:
            raise ManagedDenied('helper has no approved Project owner')
        binding = config, credentials, context
    else:
        binding = project_binding(Path(os.environ.get('ZF_PROJECT_ROOT') or Path.cwd()))
    holder = {}
    def cancel():
        process = holder.get('process')
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    execution = Execution(binding, operation_id='', provider=backend, model=model).begin(cancel)
    result, usage, native, provider_error = None, {}, '', None
    args = list(command)
    if plain_output:
        if backend == 'codex':
            args.insert(args.index('exec') + 1, '--json')
        else:
            args.extend(['--output-format', 'json'])
    options = dict(kwargs)
    timeout, data = options.pop('timeout', 300), options.pop('input', None)
    options.pop('capture_output', None)
    options.pop('check', None)
    try:
        process = subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, **options)
        holder['process'] = process
        execution.body['provider_process'] = process_identity(process.pid)
        execution.ledger.put(execution.id, 'running', execution.body)
        if execution.revoked:
            cancel()
        output, error = process.communicate(input=data, timeout=min(timeout, config.request_seconds))
        final = ''
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') in {'error', 'turn.failed'} or event.get('is_error'):
                provider_error = event.get('error') or {'code': event.get('subtype', 'unknown')}
            if event.get('type') == 'thread.started':
                native = event.get('thread_id', '')
            if event.get('type') in {'turn.completed', 'result'}:
                usage = event.get('usage') or {}
                native = event.get('session_id') or native
                final = event.get('result') or final
            if event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'agent_message':
                final = event['item'].get('text', '')
        result = subprocess.CompletedProcess(args, process.returncode, final if plain_output else output, error)
        return result
    finally:
        cancel()
        if result and result.returncode and not provider_error:
            provider_error = result.stderr.decode(errors='replace') if isinstance(result.stderr, bytes) else result.stderr
        execution.finish(usage, status='completed' if result and result.returncode == 0 and not provider_error else 'unknown',
                         native_session=native, provider_error=provider_error)
