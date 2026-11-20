"""Deterministic admission inspection for Add/Open Project entry points."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from zf.core.config.loader import load_config
from zf.core.profile.detector import detect
from zf.core.profile.schema import ProjectProfile
from zf.core.workspace.registry import WorkspaceRegistry


ProjectAdmissionAction = Literal[
    "open",
    "register",
    "initialize_state",
    "initialize_project",
    "blocked",
]

_ACTION_LABELS: dict[ProjectAdmissionAction, str] = {
    "open": "Open Project",
    "register": "Add & Open",
    "initialize_state": "Initialize & Open",
    "initialize_project": "Create Project",
    "blocked": "Blocked",
}
_TRUTH_FILES = ("kanban.json", "events.jsonl")
_PROJECT_NAME_MAX_LENGTH = 80
_PROJECT_DESCRIPTION_MAX_LENGTH = 2000


def inspect_project_admission(
    raw_root: str | Path,
    *,
    workspace: str = "default",
    requested_state_dir: str = "",
) -> dict[str, Any]:
    """Classify one path without mutating project or workspace state."""

    root_text = str(raw_root).strip()
    if not root_text:
        return _empty_root_result()

    root = Path(root_text).expanduser()
    resolved_root = root.resolve(strict=False)
    exists = root.exists()
    diagnostics: list[dict[str, Any]] = []
    project_profile = _empty_project_profile()

    creation_base = _nearest_existing_parent(resolved_root)
    parent_exists = root.parent.exists()
    parent_writable = bool(
        creation_base is not None and os.access(creation_base, os.W_OK)
    )
    if creation_base is None:
        _stop(
            diagnostics,
            "parent_missing",
            f"no existing parent found for: {resolved_root}",
        )
    elif not parent_writable:
        _stop(
            diagnostics,
            "parent_not_writable",
            f"parent is not writable: {creation_base}",
        )
    if exists and not root.is_dir():
        _stop(
            diagnostics,
            "root_not_directory",
            f"root is not a directory: {resolved_root}",
        )
    elif exists:
        try:
            project_profile = detect(resolved_root).to_dict()
        except OSError as exc:
            diagnostics.append(
                {
                    "severity": "WARN",
                    "kind": "project_profile_unreadable",
                    "message": f"project stack cannot be inspected: {exc}",
                }
            )

    config_path = resolved_root / "zf.yaml"
    has_config = config_path.exists()
    config_loadable = False
    config = None
    if has_config:
        if not config_path.is_file():
            _stop(
                diagnostics,
                "config_not_file",
                f"zf.yaml is not a file: {config_path}",
            )
        else:
            try:
                config = load_config(config_path)
                config_loadable = True
            except Exception as exc:
                _stop(
                    diagnostics,
                    "config_invalid",
                    f"zf.yaml is invalid: {exc}",
                )

    state_dir_value = (
        str(config.project.state_dir or ".zf")
        if config is not None
        else (requested_state_dir.strip() or ".zf")
    )
    state_dir = Path(state_dir_value).expanduser()
    if not state_dir.is_absolute():
        state_dir = resolved_root / state_dir
    resolved_state_dir = state_dir.resolve(strict=False)

    if config is None:
        try:
            resolved_state_dir.relative_to(resolved_root)
        except ValueError:
            _stop(
                diagnostics,
                "state_dir_outside_root",
                f"state_dir is outside root: {resolved_state_dir}",
            )

    state_dir_exists = resolved_state_dir.exists()
    state_dir_non_empty = False
    if state_dir_exists and not resolved_state_dir.is_dir():
        _stop(
            diagnostics,
            "state_dir_not_directory",
            f"state_dir is not a directory: {resolved_state_dir}",
        )
    elif resolved_state_dir.is_dir():
        try:
            state_dir_non_empty = any(resolved_state_dir.iterdir())
        except OSError as exc:
            _stop(
                diagnostics,
                "state_dir_unreadable",
                f"state_dir cannot be inspected: {exc}",
            )

    missing_truth_files = [
        name for name in _TRUTH_FILES
        if not (resolved_state_dir / name).is_file()
    ]
    state_ready = (
        config_loadable
        and resolved_state_dir.is_dir()
        and not missing_truth_files
    )
    if state_dir_non_empty:
        if missing_truth_files:
            from zf.core.config.project_context import ProjectContext
            from zf.integrations.controller.project_admission import pending_metadata_only

            pending = config_loadable and pending_metadata_only(ProjectContext(
                resolved_root, config_path, config, resolved_state_dir))
            if not pending:
                _stop(
                    diagnostics,
                    "state_dir_partial",
                    "non-empty state_dir is missing canonical truth files: "
                    + ", ".join(missing_truth_files),
                    missing_truth_files=missing_truth_files,
                )
        elif not config_loadable:
            _stop(
                diagnostics,
                "state_dir_without_config",
                "non-empty state_dir cannot be adopted without a valid zf.yaml",
            )

    registered_conflicts: list[dict[str, str]] = []
    try:
        for project in WorkspaceRegistry(workspace=workspace).list_projects():
            project_root = Path(project.root).expanduser().resolve(strict=False)
            if project_root == resolved_root:
                registered_conflicts.append(
                    {
                        "project_id": project.project_id,
                        "name": project.name,
                        "root": str(project_root),
                    }
                )
    except Exception as exc:
        _stop(
            diagnostics,
            "workspace_registry_invalid",
            f"workspace registry cannot be read: {exc}",
        )

    if registered_conflicts:
        diagnostics.append(
            {
                "severity": "INFO",
                "kind": "root_already_registered",
                "message": "root is already registered in workspace",
                "project_ids": [
                    item["project_id"] for item in registered_conflicts
                ],
            }
        )

    action, reason = _classify(
        diagnostics=diagnostics,
        has_config=has_config,
        config_loadable=config_loadable,
        state_ready=state_ready,
        registered=bool(registered_conflicts),
    )
    project_id = (
        registered_conflicts[0]["project_id"]
        if registered_conflicts else ""
    )
    action_label = _ACTION_LABELS[action]
    if action == "initialize_project" and exists:
        action_label = "Initialize & Open"
    blocked = action == "blocked"
    return {
        "schema_version": "workspace.project-admission.v1",
        "ok": not blocked,
        "status": "invalid" if blocked else ("valid" if exists else "missing"),
        "root": str(resolved_root),
        "root_resolved": str(resolved_root),
        "root_exists": exists,
        "config_path": str(config_path),
        "has_config": has_config,
        "config_loadable": config_loadable,
        "state_dir": str(resolved_state_dir),
        "state_dir_resolved": str(resolved_state_dir),
        "state_dir_exists": state_dir_exists,
        "state_dir_non_empty": state_dir_non_empty,
        "state_ready": state_ready,
        "missing_truth_files": missing_truth_files,
        "parent_exists": parent_exists,
        "parent_writable": parent_writable,
        "can_create": action == "initialize_project" and not exists,
        "can_register": action == "register",
        "registered_conflicts": registered_conflicts,
        "diagnostics": diagnostics,
        "project_profile": project_profile,
        "admission": {
            "action": action,
            "label": action_label,
            "reason": reason,
            "project_id": project_id,
        },
    }


def normalize_new_project_metadata(
    *,
    root: Path,
    name: object = "",
    description: object = "",
) -> tuple[str, str]:
    """Validate the durable metadata written for a newly admitted Project."""

    if name is not None and not isinstance(name, str):
        raise ValueError("project name must be a string")
    project_name = str(name or "").strip() or root.expanduser().name or "zaofu-project"
    if project_name in {".", ".."} or any(
        character in project_name for character in ("/", "\\", "\n", "\r", "\0")
    ):
        raise ValueError("project name must be a single path-safe label")
    if len(project_name) > _PROJECT_NAME_MAX_LENGTH:
        raise ValueError(
            f"project name must be at most {_PROJECT_NAME_MAX_LENGTH} characters"
        )

    if description is not None and not isinstance(description, str):
        raise ValueError("project description must be a string")
    project_description = str(description or "").strip()
    if "\0" in project_description:
        raise ValueError("project description must not contain NUL characters")
    if len(project_description) > _PROJECT_DESCRIPTION_MAX_LENGTH:
        raise ValueError(
            "project description must be at most "
            f"{_PROJECT_DESCRIPTION_MAX_LENGTH} characters"
        )
    return project_name, project_description


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path if path.exists() else path.parent
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.exists() and current.is_dir() else None


def _classify(
    *,
    diagnostics: list[dict[str, Any]],
    has_config: bool,
    config_loadable: bool,
    state_ready: bool,
    registered: bool,
) -> tuple[ProjectAdmissionAction, str]:
    stop = next(
        (
            item for item in diagnostics
            if str(item.get("severity") or "").upper() == "STOP"
        ),
        None,
    )
    if stop is not None:
        return "blocked", str(stop.get("kind") or "unsafe_project_state")
    if config_loadable and state_ready:
        if registered:
            return "open", "registered_project_ready"
        return "register", "project_ready_not_registered"
    if config_loadable:
        return "initialize_state", "config_ready_state_missing"
    if not has_config:
        return "initialize_project", "project_config_missing"
    return "blocked", "config_invalid"


def _stop(
    diagnostics: list[dict[str, Any]],
    kind: str,
    message: str,
    **extra: Any,
) -> None:
    diagnostics.append(
        {
            "severity": "STOP",
            "kind": kind,
            "message": message,
            **extra,
        }
    )


def _empty_root_result() -> dict[str, Any]:
    diagnostic = {
        "severity": "STOP",
        "kind": "root_required",
        "message": "root is required",
    }
    return {
        "schema_version": "workspace.project-admission.v1",
        "ok": False,
        "status": "invalid",
        "reason": "root is required",
        "diagnostics": [diagnostic],
        "project_profile": _empty_project_profile(),
        "admission": {
            "action": "blocked",
            "label": _ACTION_LABELS["blocked"],
            "reason": "root_required",
            "project_id": "",
        },
    }


def _empty_project_profile() -> dict[str, Any]:
    return ProjectProfile().to_dict()
