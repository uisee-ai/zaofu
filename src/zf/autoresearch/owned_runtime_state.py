"""Locate an experiment for teardown without validating business config."""
from pathlib import Path

import yaml


def owned_runtime_state(root: Path) -> Path:
    from zf.core.config.loader import _config_env_map, _expand_env_vars
    from zf.core.config.schema import ProjectConfig

    path = root / 'zf.yaml'
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('experiment config is too large for teardown')
    text = _expand_env_vars(path.read_text(), _config_env_map(path))
    documents = [doc for doc in yaml.safe_load_all(text) if doc is not None]
    if len(documents) == 1 and isinstance(documents[0], dict) and 'kind' not in documents[0]:
        selected = [documents[0]]
    else:
        selected = [doc.get('spec') for doc in documents
                    if isinstance(doc, dict) and doc.get('kind') == 'ZfConfig']
    if len(selected) != 1 or not isinstance(selected[0], dict):
        raise ValueError('experiment requires one unambiguous ZfConfig')
    project = selected[0].get('project')
    if not isinstance(project, dict):
        raise ValueError('experiment project locator is missing')
    if 'state_dir' not in project and '/' in str(selected[0].get('preset') or ''):
        raise ValueError('teardown requires explicit state_dir for a versioned preset')
    name = project.get('state_dir', ProjectConfig().state_dir)
    if not isinstance(name, str) or not name.strip() or '${' in name:
        raise ValueError('experiment state_dir is invalid')
    state = (root / name).resolve()
    state.relative_to(root)
    if state == root:
        raise ValueError('experiment state_dir cannot be the project root')
    return state
