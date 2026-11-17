"""Cloud Project creation joins an applied policy before writing canonical state."""

from contextlib import nullcontext
from functools import wraps
import hashlib
import json
from pathlib import Path
import uuid

from zf.core.state.locks import locked_path
from zf.core.workspace.registry import WorkspaceRegistry
from zf.integrations.controller.installation import Installation, private_read
from zf.integrations.controller.workspace_transport import WorkspaceTransport


def intent_name(root):
    return 'project-init-' + hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest() + '.json'


def serialize_initialization(call):
    @wraps(call)
    def wrapped(self, *, cwd, **kwargs):
        from zf.integrations.controller.binding import managed_config, project_directory
        project_directory(cwd, self.workspace, kwargs.get('explicit_state_dir'))
        config = managed_config()
        lock = (locked_path(Installation(config).path / intent_name(cwd), timeout_seconds=30)
                if config else nullcontext())
        with lock:
            return call(self, cwd=cwd, **kwargs)
    return wrapped


def pending_metadata_only(context):
    """An ACK retry may already have an Outbox, but must never reset project state."""
    from zf.integrations.controller.binding import managed_config
    config = managed_config()
    if not config or config.hosting != 'cloud':
        return False
    path = Installation(config).path / intent_name(context.project_root)
    if not path.exists():
        return False
    intent = json.loads(private_read(path))
    return (intent.get('root') == str(context.project_root)
            and intent.get('state_dir') == str(context.state_dir)
            and context.state_dir.is_dir()
            and all(item.name == 'controller' and item.is_dir() and not item.is_symlink()
                    for item in context.state_dir.iterdir()))


def admit_project(context, workspace):
    from zf.integrations.controller.binding import ManagedDenied, managed_config, project_directory
    workspace = project_directory(context.project_root, workspace, context.state_dir)
    config = managed_config()
    if config is None or config.hosting != 'cloud':
        return None
    from zf.integrations.controller.workspace import apply_policy

    installation = Installation(config)
    credentials = installation.credentials()
    entry = WorkspaceRegistry(workspace=workspace).upsert_context(context)
    name = intent_name(context.project_root)
    identity = {'root': str(context.project_root), 'state_dir': str(context.state_dir),
                'project_id': entry.project_id}
    path = installation.path / name
    intent = json.loads(private_read(path)) if path.exists() else None
    if intent and any(intent.get(k) != v for k, v in identity.items()):
        raise ManagedDenied('pending Project initialization identity changed')
    saved = json.loads(private_read(installation.path / 'policy.json'))
    if intent is None:
        intent = {**identity, 'request': {'protocol': 'zfc.control.v1', 'request_id': str(uuid.uuid4()),
            'project_id': entry.project_id, 'generation': credentials['generation'], 'policy_version': saved['version']}}
        installation.write(name, intent)
    with WorkspaceTransport(config, credentials) as transport:
        try:
            result = transport.request('POST', '/internal/v1/me/projects/admit', intent['request'])
        except Exception as exc:
            # Do not expose an IdP response or credentials through Web/CLI error bodies.
            response = getattr(exc, 'response', None)
            code = ''
            if response is not None:
                try:
                    code = response.json().get('error', '')
                except (ValueError, AttributeError):
                    pass
            if code in {'project_admission_busy', 'project_quota_exceeded', 'policy_version_conflict',
                        'policy_not_applied', 'cloud_project_admission_revoked'}:
                # A version conflict has no admission side effect. Refresh the request for a manual retry.
                if code == 'policy_version_conflict':
                    policy, _ = apply_policy(config, credentials, transport, installation)
                    intent['request'].update(request_id=str(uuid.uuid4()), policy_version=policy['version'])
                    installation.write(name, intent)
                raise ManagedDenied(code) from None
            raise ManagedDenied('cloud Project admission unavailable or denied; retry without force') from None
        if (result.get('protocol') != 'zfc.control.v1' or result.get('admitted') is not True
                or result.get('workspace_id') != config.workspace_id
                or result.get('deployment_id') != credentials['deployment_id']
                or result.get('generation') != credentials['generation']
                or result.get('project_id') != entry.project_id
                or type(result.get('policy_version')) is not int):
            raise ManagedDenied('cloud Project admission identity mismatch')
        try:
            policy, _ = apply_policy(config, credentials, transport, installation)
        except Exception:
            raise ManagedDenied('cloud Project policy ACK failed; retry without force') from None
        if policy['version'] < result['policy_version'] or entry.project_id not in policy['projects']:
            raise ManagedDenied('cloud Project grant was superseded')
    return entry
