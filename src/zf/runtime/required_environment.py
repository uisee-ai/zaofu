"""Mechanical admission of explicitly required workspace preparation outputs."""
from pathlib import Path

from zf.runtime.environment_resources import relative_resource_paths, register_environment_resources
from zf.runtime.environment_usage import environment_in_use


class RequiredEnvironmentError(RuntimeError):
    pass


def require_workspace_environment(
    worktree: Path, paths: list[str], *, qualified_setup: bool = False,
    bootstrap_uv_dev: bool = False,
) -> None:
    required = relative_resource_paths(paths)
    if not required:
        return
    register_environment_resources(worktree, required)
    with environment_in_use(worktree):
        for name in required:
            target = worktree / name
            if not target.exists():
                raise RequiredEnvironmentError(f'required environment path unavailable: {name}; run project setup')
        if any(Path(p).parts[0] == '.venv' for p in required) and bootstrap_uv_dev and not qualified_setup and (worktree / 'pyproject.toml').exists():
            from zf.runtime.worktree_env import _reuse_provisioned_env

            if _reuse_provisioned_env(worktree, worktree, ['.venv'], True) is None:
                raise RequiredEnvironmentError('required environment .venv has no current successful bootstrap receipt; run project setup')
