"""Workspace project registry.

The registry is local metadata for finding projects. It deliberately stores
only descriptors and stale-prone hints; every runtime read must re-resolve the
project's ``zf.yaml``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.config.project_context import ProjectContext
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


_ALLOWED_PROJECT_KEYS = {
    "project_id",
    "aliases",
    "name",
    "root",
    "config_path",
    "state_dir_hint",
    "last_opened_at",
}
_TRUTH_KEYS = {
    "tasks",
    "task",
    "kanban",
    "events",
    "roles",
    "workflow",
    "feature_list",
    "session",
    "role_sessions",
}


@dataclass(frozen=True)
class WorkspaceProject:
    project_id: str
    name: str
    root: str
    config_path: str
    state_dir_hint: str
    last_opened_at: str
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkspaceProject":
        extra = set(raw) - _ALLOWED_PROJECT_KEYS
        truth = extra & _TRUTH_KEYS
        if truth:
            raise ValueError(
                "workspace registry must not store project truth: "
                + ", ".join(sorted(truth))
            )
        return cls(
            project_id=str(raw.get("project_id") or ""),
            aliases=tuple(
                str(item)
                for item in raw.get("aliases", [])
                if str(item or "").strip()
            ),
            name=str(raw.get("name") or ""),
            root=str(raw.get("root") or ""),
            config_path=str(raw.get("config_path") or ""),
            state_dir_hint=str(raw.get("state_dir_hint") or ""),
            last_opened_at=str(raw.get("last_opened_at") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        return data


class WorkspaceRegistry:
    """Atomic JSON registry for one local workspace."""

    def __init__(
        self,
        *,
        workspace: str = "default",
        home: Path | None = None,
        path: Path | None = None,
    ) -> None:
        self.workspace = _safe_segment(workspace)
        self.path = path or registry_path(workspace=self.workspace, home=home)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "workspace_id": self.workspace, "projects": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid workspace registry JSON: {self.path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"invalid workspace registry shape: {self.path}")
        projects = data.get("projects")
        if not isinstance(projects, list):
            data["projects"] = []
        return data

    def list_projects(self) -> list[WorkspaceProject]:
        return [
            WorkspaceProject.from_dict(item)
            for item in self.read().get("projects", [])
            if isinstance(item, dict)
        ]

    def get(self, project_id: str) -> WorkspaceProject | None:
        requested = str(project_id or "")
        for project in self.list_projects():
            if project.project_id == requested or requested in project.aliases:
                return project
        return None

    def upsert(self, project: WorkspaceProject) -> WorkspaceProject:
        _validate_project(project)

        def update() -> WorkspaceProject:
            data = self.read()
            projects = [
                WorkspaceProject.from_dict(item)
                for item in data.get("projects", [])
                if isinstance(item, dict)
            ]
            identity_matches = [
                item for item in projects
                if _same_identity(item, project)
            ]
            by_id = {item.project_id: item for item in projects}
            previous = by_id.get(project.project_id) or (
                identity_matches[0] if identity_matches else None
            )
            for item in identity_matches:
                by_id.pop(item.project_id, None)
            if previous is not None:
                aliases = _merge_aliases(
                    project.project_id,
                    [*previous.aliases, previous.project_id, *project.aliases],
                )
                merged = {
                    **previous.to_dict(),
                    **project.to_dict(),
                    "aliases": aliases,
                    "last_opened_at": project.last_opened_at or _now(),
                }
                by_id[project.project_id] = WorkspaceProject.from_dict(merged)
            else:
                by_id[project.project_id] = project
            next_projects = sorted(
                (item.to_dict() for item in by_id.values()),
                key=lambda item: (item.get("name", ""), item.get("root", "")),
            )
            payload = {
                "version": int(data.get("version") or 1),
                "workspace_id": str(data.get("workspace_id") or self.workspace),
                "projects": next_projects,
            }
            atomic_write_text(
                self.path,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            return by_id[project.project_id]

        with locked_path(self.path):
            return update()

    def upsert_context(
        self,
        context: ProjectContext,
        *,
        display_name: str = "",
    ) -> WorkspaceProject:
        name = (
            context.config.project.name
            if context.config is not None and context.config.project.name
            else context.project_root.name
        )
        project_name = str(display_name or name).strip() or name
        project_id = stable_project_id(name=name, root=context.project_root)
        legacy_id = legacy_project_id(name=name, root=context.project_root)
        aliases = [legacy_id] if legacy_id != project_id else []
        project = WorkspaceProject(
            project_id=project_id,
            aliases=tuple(aliases),
            name=project_name,
            root=str(context.project_root.resolve()),
            config_path=str(context.config_path.resolve()),
            state_dir_hint=str(context.state_dir.resolve()),
            last_opened_at=_now(),
        )
        return self.upsert(project)

    def touch(self, project_id: str) -> WorkspaceProject | None:
        with locked_path(self.path):
            data = self.read()
            projects = [
                WorkspaceProject.from_dict(item)
                for item in data.get("projects", [])
                if isinstance(item, dict)
            ]
            touched: WorkspaceProject | None = None
            next_projects = []
            for project in projects:
                if project.project_id == project_id or project_id in project.aliases:
                    touched = WorkspaceProject.from_dict({
                        **project.to_dict(),
                        "last_opened_at": _now(),
                    })
                    next_projects.append(touched)
                else:
                    next_projects.append(project)
            if touched is None:
                return None
            data["projects"] = [project.to_dict() for project in next_projects]
            atomic_write_text(
                self.path,
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            return touched

    def remove(self, project_id: str) -> bool:
        requested = str(project_id or "")
        with locked_path(self.path):
            data = self.read()
            projects = [
                item for item in data.get("projects", [])
                if isinstance(item, dict)
                and str(item.get("project_id") or "") != requested
                and requested not in {
                    str(alias) for alias in item.get("aliases", [])
                    if str(alias or "").strip()
                }
            ]
            changed = len(projects) != len(data.get("projects", []))
            if changed:
                data["projects"] = projects
                atomic_write_text(
                    self.path,
                    json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                )
            return changed


def registry_path(*, workspace: str = "default", home: Path | None = None) -> Path:
    env_home = os.environ.get("ZF_WORKSPACE_HOME", "").strip()
    root = Path(env_home).expanduser() if env_home else (home or Path.home()) / ".zaofu"
    return root / "workspaces" / _safe_segment(workspace) / "projects.json"


def stable_project_id(*, name: str, root: Path) -> str:
    del name
    label = _safe_segment(root.resolve().name or "project").lower()
    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{label}-{digest}"


def legacy_project_id(*, name: str, root: Path) -> str:
    label = _safe_segment(name or root.name).lower()
    digest = hashlib.sha1(str(root.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{label}-{digest}"


def should_skip_default_register(project_root: Path) -> bool:
    resolved = project_root.resolve()
    try:
        relative = resolved.relative_to(Path("/tmp"))
    except ValueError:
        return False
    return bool(relative.parts and relative.parts[0].startswith("zf-"))


def _validate_project(project: WorkspaceProject) -> None:
    if not project.project_id:
        raise ValueError("project_id is required")
    if not project.root:
        raise ValueError("root is required")
    if not project.config_path:
        raise ValueError("config_path is required")


def _same_identity(left: WorkspaceProject, right: WorkspaceProject) -> bool:
    try:
        return (
            str(Path(left.root).expanduser().resolve())
            == str(Path(right.root).expanduser().resolve())
            and str(Path(left.config_path).expanduser().resolve())
            == str(Path(right.config_path).expanduser().resolve())
        )
    except Exception:
        return left.root == right.root and left.config_path == right.config_path


def _merge_aliases(project_id: str, aliases: list[str]) -> list[str]:
    merged: list[str] = []
    for alias in aliases:
        text = str(alias or "").strip()
        if not text or text == project_id or text in merged:
            continue
        merged.append(text)
    return merged


def _safe_segment(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    return text.strip("-") or "default"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
