"""Path safety checks for runtime cleanup and future workdirs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


WORKDIR_OWNER_MARKER = ".zf-workdir-owner.json"
TRUTH_FILES = {
    "events.jsonl",
    "kanban.json",
    "session.yaml",
    "feature_list.json",
}


class PathGuardError(ValueError):
    pass


@dataclass(frozen=True)
class WorkdirOwnerMarker:
    project_name: str
    instance_id: str
    created_by: str
    created_at: str
    project_root: str


class PathGuard:
    @staticmethod
    def assert_under(path: Path, root: Path) -> Path:
        resolved_path = path.resolve(strict=False)
        resolved_root = root.resolve(strict=False)
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError as exc:
            raise PathGuardError(
                f"{resolved_path} is outside {resolved_root}"
            ) from exc
        return resolved_path

    @staticmethod
    def assert_not_truth_file(path: Path) -> None:
        if path.name in TRUTH_FILES:
            raise PathGuardError(f"refusing to delete truth file {path}")

    @staticmethod
    def assert_safe_to_delete(path: Path) -> None:
        resolved = path.resolve(strict=False)
        if resolved == resolved.parent:
            raise PathGuardError("refusing to delete filesystem root")
        if resolved == Path.home().resolve(strict=False):
            raise PathGuardError("refusing to delete user home")
        if resolved == Path.cwd().resolve(strict=False):
            raise PathGuardError("refusing to delete project root")
        if path.name == ".git" or resolved.name == ".git":
            raise PathGuardError("refusing to delete .git")
        if path.is_symlink():
            raise PathGuardError(f"refusing to delete symlink {path}")
        # r-next E: refuse to recursively delete a directory that contains
        # a .git/ subdirectory at its root — that's a git working tree
        # root, and `rm -rf` would destroy version control history.
        # cangjie-mono r-next had its main .git mysteriously vanish during
        # cleanup; this guard makes the same accident impossible going
        # forward, regardless of which code path called us.
        if path.is_dir() and (path / ".git").exists():
            raise PathGuardError(
                f"refusing to delete {path}: contains .git/ (looks like a "
                "git working tree root)"
            )
        PathGuard.assert_not_truth_file(path)

    @staticmethod
    def assert_safe_symlink(link: Path, target: Path) -> None:
        root = link.parent
        PathGuard.assert_under(target, root)


def write_workdir_owner_marker(
    workdir: Path,
    *,
    project_name: str,
    instance_id: str,
    project_root: Path,
    created_by: str = "zf",
) -> WorkdirOwnerMarker:
    workdir.mkdir(parents=True, exist_ok=True)
    marker = WorkdirOwnerMarker(
        project_name=project_name,
        instance_id=instance_id,
        created_by=created_by,
        created_at=datetime.now(timezone.utc).isoformat(),
        project_root=str(project_root.resolve(strict=False)),
    )
    (workdir / WORKDIR_OWNER_MARKER).write_text(
        json.dumps(asdict(marker), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return marker


def assert_owned_workdir(
    workdir: Path,
    *,
    state_dir: Path,
) -> WorkdirOwnerMarker:
    PathGuard.assert_under(workdir, state_dir / "workdirs")
    PathGuard.assert_safe_to_delete(workdir)
    marker_path = workdir / WORKDIR_OWNER_MARKER
    if not marker_path.is_file():
        raise PathGuardError(f"missing workdir owner marker: {marker_path}")
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        marker = WorkdirOwnerMarker(**data)
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise PathGuardError(f"invalid workdir owner marker: {marker_path}") from exc
    if not marker.instance_id:
        raise PathGuardError(f"invalid workdir owner marker: {marker_path}")
    return marker
