"""Project-bound path helpers for Workflow request surfaces."""

from __future__ import annotations

from pathlib import Path

from zf.core.safety import PathGuard, PathGuardError


def project_path(root: Path, raw: object, *, field: str) -> Path:
    path = Path(str(raw or "")).expanduser()
    root = Path(root).expanduser().resolve(strict=False)
    if not path.is_absolute():
        path = root / path
    try:
        return PathGuard.assert_under(path, root)
    except PathGuardError as exc:
        raise PathGuardError(f"{field} must stay under the selected project") from exc


def bind_project_paths(
    config_path: Path, intake_path: Path, project_root: Path | None,
) -> tuple[Path, Path, Path | None]:
    config = Path(config_path).expanduser()
    intake = Path(intake_path).expanduser()
    if project_root is None:
        return config, intake, None
    root = Path(project_root).expanduser().resolve(strict=False)
    return (
        project_path(root, config, field="config_ref"),
        project_path(root, intake, field="intake_ref"),
        root,
    )
