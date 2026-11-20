"""Reconnect telemetry for preserved dynamic roles after controller restart."""

from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.session_tailer import (
    ClaudeSessionTailer,
    CodexSessionTailer,
    claude_session_path,
)


def restore_preserved_session_tailers(
    registry: RoleSessionRegistry,
    codex_tailer: CodexSessionTailer,
    claude_tailer: ClaudeSessionTailer,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Tail registered sessions from EOF without spawning or rebinding workers.

    Product Flow lanes may not occur in initial_role_configs. The registry,
    not the startup role list, holds their current provider session binding.
    Existing attachments are deduplicated by the tailer's instance/path key.
    """
    if dry_run:
        return []
    metadata = registry.instance_meta()
    attached = []
    for instance_id, session_id in registry.all().items():
        backend = metadata.get(instance_id, {}).get('backend')
        if backend == 'codex':
            path = registry.get_path(instance_id)
            tailer = codex_tailer
        elif backend == 'claude-code':
            path = claude_session_path(registry.project_root, str(session_id))
            tailer = claude_tailer
        else:
            continue
        if path is not None and path.is_file():
            tailer.tail(instance_id, path, replay_existing=False)
            attached.append(instance_id)
    return attached
