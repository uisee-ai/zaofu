"""Explicit deployment-level zf.yaml; a Workspace is not a synthetic Project."""

from dataclasses import dataclass, field
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
    management: str = 'observed'
    gateway_origin: str = ''
    request_tokens: int = 32768
    request_money: str = '0'
    price_version: str = 'unknown'
    lease_seconds: int = 30
    receipt_retention_seconds: int = 604800
    request_seconds: int = 3600
    hosting: str = 'external'
    project_root: Path | None = None
    allowed_modes: tuple[str, ...] = ()
    runtime_mode: str | None = None
    mode_epoch: float = 0
    provider_modes: dict[str, str] = field(default_factory=dict)
    cpa_request_namespace: str = ''

    def mode_for(self, backend):
        return provider_mode(self.provider_modes, backend, self.effective_mode)

    @property
    def effective_mode(self):
        return self.runtime_mode or self.mode

    def target(self) -> dict:
        return {"endpoint": self.endpoint, "issuer": self.issuer,
                "workspace_id": self.workspace_id, "workspace": self.workspace, "mode": self.mode,
                **({'provider_modes': self.provider_modes} if self.provider_modes else {}),
                **({'cpa_request_namespace': self.cpa_request_namespace} if self.cpa_request_namespace else {})}


def provider_mode(modes, backend, default):
    from zf.core.config.backend_identity import catalog_backend_id

    family = catalog_backend_id(backend)
    backend = {'openai': 'codex', 'anthropic': 'claude-code', 'claude': 'claude-code'}.get(family, family)
    return modes.get(backend, default)


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
        "gateway_origin", "request_tokens", "request_money", "price_version", "lease_seconds",
        "receipt_retention_seconds",
        "request_seconds",
        "hosting", "project_root",
        "allowed_modes", "provider_modes", "cpa_request_namespace",
    })
    management = cloud.get('management')
    if management not in {'observed', 'managed'}:
        raise ValueError('explicit observed or managed management is required')
    development = cloud.get("development", False)
    if type(development) is not bool:
        raise ValueError("development must be boolean")
    for key in ('endpoint', 'issuer', *(['gateway_origin'] if management == 'managed' else [])):
        value = cloud.get(key)
        if not isinstance(value, str) or not value or re.search(r"\s|\\", value):
            raise ValueError("invalid Controller URL")
        parsed = urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or value.endswith("/") or (key != "issuer" and parsed.path)):
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
    allowed_modes = cloud.get('allowed_modes', [mode])
    if (not isinstance(allowed_modes, list) or not allowed_modes or mode not in allowed_modes
            or len(set(allowed_modes)) != len(allowed_modes)
            or any(m not in {'self_managed_cli', 'self_managed_api', 'managed_cpa', 'unknown'} for m in allowed_modes)):
        raise ValueError('invalid locally consented model access modes')
    provider_modes = _mapping(cloud.get('provider_modes', {}), {'codex', 'claude-code'})
    if any(value not in allowed_modes or value == 'unknown' for value in provider_modes.values()):
        raise ValueError('provider mode requires explicit locally allowed mode')
    namespace = cloud.get('cpa_request_namespace', '')
    if namespace != '' and (not identifier(namespace) or 'managed_cpa' not in allowed_modes):
        raise ValueError('invalid CPA request namespace or access mode')
    hosting = cloud.get('hosting', 'external')
    if hosting not in {'external', 'cloud'} or (hosting == 'cloud' and management != 'managed'):
        raise ValueError('invalid hosting or management combination')
    project_root = cloud.get('project_root')
    if hosting == 'cloud' and (not isinstance(project_root, str) or not Path(project_root).is_absolute()):
        raise ValueError('cloud hosting requires an absolute private project_root')
    limits = {}
    for key, default, low, high in (
        ("interval_seconds", 15, 1, 3600), ("batch_size", 16, 1, 32),
        ("max_pending_records", 100000, 32, 1000000),
        ("max_pending_bytes", 67108864, 65536, 1073741824),
        ('request_tokens', 32768, 1, 1000000000), ('lease_seconds', 30, 10, 300),
        ('receipt_retention_seconds', 604800, 86400, 31536000),
        ('request_seconds', 3600, 10, 86400),
    ):
        value = cloud.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError("invalid Connector capacity or interval")
        limits[key] = value
    from decimal import Decimal, InvalidOperation
    money, price = cloud.get('request_money', '0'), cloud.get('price_version', 'unknown')
    try:
        if not isinstance(money, str) or not Decimal(money).is_finite() or not 0 <= Decimal(money) <= 1000000:
            raise ValueError('invalid request_money')
    except InvalidOperation as exc:
        raise ValueError('invalid request_money') from exc
    if not identifier(price) or (management == 'managed' and mode == 'unknown'):
        raise ValueError('managed execution requires a known accounting mode and price version')
    state_path = Path(state).expanduser()
    if not state_path.is_absolute():
        state_path = path.parent / state_path
    if state_path == path.parent:
        raise ValueError("workspace.state_dir must be a separate runtime directory")
    return ControllerWorkspaceConfig(path, name, state_path, cloud["endpoint"], cloud["issuer"],
                                     cloud["workspace_id"], tuple(projects), mode, development,
                                     management=management, gateway_origin=cloud.get('gateway_origin', ''),
                                     request_money=money, price_version=price, hosting=hosting,
                                     allowed_modes=tuple(allowed_modes), provider_modes=provider_modes, cpa_request_namespace=namespace,
                                     project_root=Path(project_root) if project_root else None, **limits)
