"""Kernel-only credential storage boundary.

The local provider intentionally exposes opaque subjects to callers. Token
values may only be opened inside the Kernel -> external provider call.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


def forge_credential_store_path(state_dir: Path) -> Path:
    """Resolve the shared Web workspace credential store when configured."""

    workspace_home = os.environ.get("ZF_WORKSPACE_HOME", "").strip()
    if workspace_home:
        return Path(workspace_home).expanduser().resolve() / "secrets" / "forge-credentials.json"
    return Path(state_dir) / "secrets" / "forge-credentials.json"


def forge_credential_workspace_id(project_root: Path) -> str:
    """Return one credential scope for every project in a Web workspace."""

    workspace_home = os.environ.get("ZF_WORKSPACE_HOME", "").strip()
    root = (
        Path(workspace_home).expanduser().resolve()
        if workspace_home else Path(project_root).resolve()
    )
    return str(root)


@dataclass(frozen=True)
class SecretKey:
    user_id: str
    workspace_id: str
    provider: str
    authorization_domain: str

    @property
    def subject(self) -> str:
        material = json.dumps({
            "authorization_domain": self.authorization_domain,
            "provider": self.provider,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
        }, sort_keys=True, separators=(",", ":")).encode()
        return "cred-" + hashlib.sha256(material).hexdigest()


class SecretProvider(Protocol):
    def put(self, key: SecretKey, value: dict[str, str]) -> None: ...
    def reveal(self, key: SecretKey) -> dict[str, str] | None: ...
    def delete(self, key: SecretKey) -> bool: ...


class LocalSecretProvider:
    """Dedicated local secret backend with owner-only file permissions.

    Deployments needing encryption at rest should replace this provider; the
    domain/runtime receives only the protocol and never the storage path.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        mode = self.path.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError("secret store permissions must be owner-only")
        data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        return data if isinstance(data, dict) else {}

    def _save(self, values: dict[str, dict[str, str]]) -> None:
        atomic_write_text(
            self.path,
            json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        os.chmod(self.path, 0o600)

    def put(self, key: SecretKey, value: dict[str, str]) -> None:
        with locked_path(self.path):
            values = self._load()
            values[key.subject] = {str(k): str(v) for k, v in value.items()}
            self._save(values)

    def reveal(self, key: SecretKey) -> dict[str, str] | None:
        value = self._load().get(key.subject)
        return dict(value) if value is not None else None

    def delete(self, key: SecretKey) -> bool:
        with locked_path(self.path):
            values = self._load()
            existed = values.pop(key.subject, None) is not None
            if existed:
                self._save(values)
            return existed
