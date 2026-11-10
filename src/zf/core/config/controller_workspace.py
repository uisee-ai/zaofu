"""Explicit deployment-level zf.yaml; a Workspace is not a synthetic Project."""

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml


@dataclass(frozen=True)
class ControllerWorkspaceConfig:
    path: Path
    workspace: str
    state_dir: Path
    endpoint: str
    issuer: str
    workspace_id: str
    projects: tuple[str, ...]
    mode: str = "self_managed_cli"
    development: bool = False
    interval_seconds: int = 15
    batch_size: int = 16
    max_pending_records: int = 100000
    max_pending_bytes: int = 67108864

    def target(self) -> dict:
        return {"endpoint": self.endpoint, "issuer": self.issuer,
                "workspace_id": self.workspace_id, "workspace": self.workspace, "mode": self.mode}


def _mapping(value: object, allowed: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("invalid WorkspaceConfig fields")
    return value


def load_controller_workspace(path: Path) -> ControllerWorkspaceConfig:
    path = path.expanduser().absolute()
    if path.name != "zf.yaml":
        raise ValueError("WorkspaceConfig must be stored in zf.yaml")
    raw = _mapping(yaml.safe_load(path.read_text()), {"version", "kind", "workspace"})
    if raw.get("version") != "1.0" or raw.get("kind") != "WorkspaceConfig":
        raise ValueError("expected version 1.0 and kind WorkspaceConfig")
    workspace = _mapping(raw.get("workspace"), {"name", "state_dir", "controller"})
    name, state = workspace.get("name"), workspace.get("state_dir")
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", name) or name in {".", ".."}:
        raise ValueError("invalid local workspace name")
    if not isinstance(state, str) or not state.strip():
        raise ValueError("workspace.state_dir is required")
    cloud = _mapping(workspace.get("controller"), {
        "management", "endpoint", "issuer", "workspace_id", "projects", "mode", "development",
        "interval_seconds", "batch_size", "max_pending_records", "max_pending_bytes",
    })
    # Until every execution/access caller is wired, never acknowledge full management.
    if cloud.get("management") != "observed":
        raise ValueError("this Connector supports explicit observed management only")
    development = cloud.get("development", False)
    if type(development) is not bool:
        raise ValueError("development must be boolean")
    for key in ("endpoint", "issuer"):
        value = cloud.get(key)
        if not isinstance(value, str) or not value or re.search(r"\s|\\", value):
            raise ValueError("invalid Controller URL")
        parsed = urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or value.endswith("/") or (key == "endpoint" and parsed.path)):
            raise ValueError("invalid Controller URL")
        if parsed.scheme != "https" and not (development and parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("HTTPS required; development HTTP is loopback-only")
        _ = parsed.port  # Reject malformed ports before any filesystem/network side effects.
    identifier = lambda value: isinstance(value, str) and re.fullmatch(r"[\w.:/@+-]{1,160}", value)
    if not identifier(cloud.get("workspace_id")):
        raise ValueError("invalid workspace_id")
    projects = cloud.get("projects")
    if (not isinstance(projects, list) or len(projects) > 1000
            or not all(identifier(project) for project in projects) or len(set(projects)) != len(projects)):
        raise ValueError("projects must be an explicit unique list of local registry IDs")
    mode = cloud.get("mode", "self_managed_cli")
    if mode not in {"self_managed_cli", "self_managed_api", "managed_cpa", "unknown"}:
        raise ValueError("invalid model access mode")
    limits = {}
    for key, default, low, high in (
        ("interval_seconds", 15, 1, 3600), ("batch_size", 16, 1, 32),
        ("max_pending_records", 100000, 32, 1000000),
        ("max_pending_bytes", 67108864, 65536, 1073741824),
    ):
        value = cloud.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError("invalid Connector capacity or interval")
        limits[key] = value
    state_path = Path(state).expanduser()
    if not state_path.is_absolute():
        state_path = path.parent / state_path
    if state_path == path.parent:
        raise ValueError("workspace.state_dir must be a separate runtime directory")
    return ControllerWorkspaceConfig(path, name, state_path, cloud["endpoint"], cloud["issuer"],
                                     cloud["workspace_id"], tuple(projects), mode, development, **limits)
