"""Exact native Session deltas, using the existing full receipt reader and no transcripts on wire."""


def observed_snapshot(service, provider, session_id, project_root):
    result = service.native_snapshot(provider=provider, session_id=session_id, project_root=project_root)
    from zf.integrations.controller.subscription import load_windows
    windows = load_windows(service, provider, session_id, project_root, result)
    if windows:
        result['subscription_windows'] = windows
    return result


def snapshot(context, provider, session_id):
    from zf.web.terminal_usage import TerminalUsageService

    return observed_snapshot(TerminalUsageService(state_dir=context.state_dir), provider, session_id, context.project_root)


def role_snapshot(context, role, session_id, *, cwd=None):
    """Use the runtime's isolated role home and exact launch working directory."""
    from pathlib import Path
    from zf.web.terminal_usage import TerminalUsageService

    options = {}
    if role.backend == 'codex':
        workdirs = (context.state_dir / 'workdirs').resolve()
        home = workdirs / role.instance_id / 'codex-home' / 'sessions'
        if workdirs not in home.resolve().parents:
            return {'status': 'unavailable'}
        options['codex_sessions_root'] = home
    return observed_snapshot(TerminalUsageService(state_dir=context.state_dir, **options),
                             role.backend, session_id, Path(cwd or context.project_root))


def delta(before, after, *, fresh=False):
    if after.get('status') != 'observed' or (not fresh and before.get('status') != 'observed'):
        return {}
    fields = {'input_tokens': 'fresh_input_tokens', 'output_tokens': 'output_tokens',
              'cache_read_input_tokens': 'cached_input_tokens',
              'cache_creation_input_tokens': 'cache_creation_input_tokens',
              'reasoning_output_tokens': 'reasoning_output_tokens'}
    result = {}
    for target, field in fields.items():
        start, end = (0 if fresh else before.get(field)), after.get(field)
        if type(start) is not int or type(end) is not int or end < start:
            return {}
        result[target] = end - start
    return result
