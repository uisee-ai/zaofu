"""Deployment binding outside Project config; removing credentials never means standalone."""

import json
import os
from pathlib import Path

from zf.core.config.controller_workspace import load_controller_workspace
from zf.core.state.atomic_io import atomic_write_text
from zf.core.workspace.registry import workspace_home
from zf.integrations.controller.installation import Installation, private_read

SYSTEM_CONFIG = Path('/etc/zaofu/zf.yaml')


class ManagedDenied(RuntimeError):
    """A managed boundary could not prove authorization; never include remote bodies."""


def binding_path() -> Path:
    return workspace_home() / 'controller-binding.json'


def bind(config) -> None:
    """Explicit takeover is installation-wide, before any managed policy receipt."""
    if config.management != 'managed':
        raise ManagedDenied('managed configuration required')
    installation = Installation(config)
    installation.credentials()
    pin = {'config': str(config.path.resolve()), 'target': config.target(),
           'gateway_origin': config.gateway_origin, 'hosting': config.hosting,
           'project_root': str(config.project_root) if config.project_root else None}
    path = binding_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and json.loads(private_read(path)) != pin:
        raise ManagedDenied('another managed installation is already bound')
    if not path.exists() and config.hosting == 'external':
        from zf.integrations.controller.lifecycle import require_quiet
        require_quiet(config)
    atomic_write_text(path, json.dumps(pin))
    with installation.locked():
        installation.write('managed.json', pin)


def managed_config():
    """Both the launcher pointer and durable local pin must agree when present."""
    path = binding_path()
    supplied = os.environ.get('ZF_CONTROLLER_WORKSPACE_CONFIG', '')
    system = SYSTEM_CONFIG.exists()
    if system:
        if supplied and Path(supplied).resolve() != SYSTEM_CONFIG.resolve():
            raise ManagedDenied('launcher configuration conflicts with the protected cloud binding')
        supplied = str(SYSTEM_CONFIG)
    if not supplied and not path.exists():
        # Cloud machine identity must never be mistaken for a standalone launch.
        if os.environ.get('ZF_CONTROLLER_CLIENT_ID') or os.environ.get('ZFC_WORKSPACE_ID'):
            raise ManagedDenied('cloud launcher has not bound a workspace configuration')
        return None
    pin = json.loads(private_read(path)) if path.exists() else None
    selected = Path(supplied or pin['config']).resolve()
    config = load_controller_workspace(selected)
    if system:
        pin = json.loads(private_read(Installation(config).path / 'managed.json'))
    if pin and (str(selected) != pin['config'] or config.target() != pin['target']
                or config.gateway_origin != pin['gateway_origin'] or config.hosting != pin['hosting']
                or (str(config.project_root) if config.project_root else None) != pin['project_root']):
        raise ManagedDenied('managed installation binding changed')
    if config.management != 'managed' or not pin:
        raise ManagedDenied('managed installation requires explicit takeover')
    installation = Installation(config)
    if json.loads(private_read(installation.path / 'managed.json')) != pin:
        raise ManagedDenied('managed installation pin is missing or inconsistent')
    return config


def project_binding(root: Path):
    config = managed_config()
    if config is None:
        return None
    from zf.integrations.controller.workspace import project_contexts

    credentials = Installation(config).credentials()
    root = Path(root).resolve()
    # A worker worktree carries the original Project owner through the existing runtime environment.
    owner = Path(os.environ.get('ZF_PROJECT_ROOT') or root).resolve()
    contexts = project_contexts(config, credentials, approved_projects(config))
    for context in contexts:
        if context.project_root == root or context.project_root in root.parents:
            return config, credentials, context
        if owner == context.project_root and same_repository(root, owner):
            return config, credentials, context
    raise ManagedDenied('Project is outside the managed installation scope')


def same_repository(root, owner):
    """An inherited owner pointer alone cannot relabel an unrelated working directory."""
    import subprocess
    try:
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        common = [subprocess.run(['git', '-C', str(path), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
            capture_output=True, text=True, check=True, timeout=5, env=env).stdout.strip() for path in (root, owner)]
        return bool(common[0]) and Path(common[0]).resolve() == Path(common[1]).resolve()
    except (OSError, subprocess.SubprocessError):
        return False


def approved_projects(config):
    policy = json.loads(private_read(Installation(config).path / 'policy.json'))
    projects = policy.get('projects', [])
    return projects if config.hosting == 'cloud' else sorted(set(projects) & set(config.projects))


def retired_projects(config):
    path = Installation(config).path / 'retired-projects.json'
    return json.loads(private_read(path))['projects'] if path.exists() else []


def project_directory(root, workspace, state_dir=None):
    """Constrain managed Web project creation before its first filesystem write."""
    config = managed_config()
    if config is None:
        return workspace
    root = Path(root).expanduser().resolve()
    if config.hosting == 'cloud':
        if config.project_root.resolve() not in root.parents:
            raise ManagedDenied('Project must be inside the private project directory')
        state = Path(state_dir or '.zf')
        state = (state if state.is_absolute() else root / state).resolve()
        if root not in state.parents:
            raise ManagedDenied('cloud Project state must remain inside its Project')
    else:
        project_binding(root)
    if workspace not in {'default', config.workspace}:
        raise ManagedDenied('workspace selector differs from the managed installation')
    return config.workspace
