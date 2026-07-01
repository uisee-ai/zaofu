"""Workspace-local provider binding registry.

The registry stores operator-local endpoint metadata, not Project truth.
Project membership events should reference provider binding ids only.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from zf.core.config.loader import ConfigError, build_openclaw_provider_config
from zf.core.config.schema import (
    OpenClawProviderConfig,
    OpenClawRemoteBindingConfig,
)
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


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
_SECRET_KEYS = {
    "api_key",
    "authorization",
    "bearer",
    "password",
    "secret",
    "token",
}


class WorkspaceProviderRegistry:
    """Atomic JSON registry for workspace-scoped provider bindings."""

    def __init__(
        self,
        *,
        workspace: str = "default",
        home: Path | None = None,
        path: Path | None = None,
    ) -> None:
        self.workspace = _safe_segment(workspace)
        self.path = path or providers_path(workspace=self.workspace, home=home)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "workspace_id": self.workspace, "providers": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid workspace provider registry JSON: {self.path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"invalid workspace provider registry shape: {self.path}")
        truth = set(data) & _TRUTH_KEYS
        if truth:
            raise ValueError(
                "workspace provider registry must not store project truth: "
                + ", ".join(sorted(truth))
            )
        _reject_inline_secrets(data)
        providers = data.get("providers")
        if not isinstance(providers, dict):
            data["providers"] = {}
        data.setdefault("version", 1)
        data.setdefault("workspace_id", self.workspace)
        return data

    def openclaw(self) -> OpenClawProviderConfig:
        providers = self.read().get("providers", {})
        raw = providers.get("openclaw") if isinstance(providers, dict) else None
        try:
            return build_openclaw_provider_config(raw)
        except ConfigError as exc:
            raise ValueError(f"invalid workspace openclaw provider registry: {exc}") from exc

    def upsert_openclaw_binding(
        self,
        binding: OpenClawRemoteBindingConfig,
        *,
        default: bool = False,
    ) -> OpenClawProviderConfig:
        """Store an OpenClaw binding without storing secret values."""
        if not binding.id:
            raise ValueError("binding.id is required")
        binding_payload = asdict(binding)
        binding_payload.pop("id", None)
        _reject_inline_secrets(binding_payload)

        def update() -> OpenClawProviderConfig:
            data = self.read()
            providers = data.get("providers")
            if not isinstance(providers, dict):
                providers = {}
            raw_openclaw = providers.get("openclaw")
            openclaw = raw_openclaw if isinstance(raw_openclaw, dict) else {}
            raw_bindings = openclaw.get("bindings")
            bindings = raw_bindings if isinstance(raw_bindings, dict) else {}
            bindings[binding.id] = binding_payload
            openclaw["bindings"] = bindings
            if default or not str(openclaw.get("default_binding") or "").strip():
                openclaw["default_binding"] = binding.id
            providers["openclaw"] = openclaw
            data["providers"] = providers
            atomic_write_text(
                self.path,
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
            )
            return self.openclaw()

        with locked_path(self.path):
            return update()


def providers_path(*, workspace: str = "default", home: Path | None = None) -> Path:
    env_home = os.environ.get("ZF_WORKSPACE_HOME", "").strip()
    root = Path(env_home).expanduser() if env_home else (home or Path.home()) / ".zaofu"
    return root / "workspaces" / _safe_segment(workspace) / "providers.json"


def _safe_segment(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    return text.strip("-") or "default"


def _reject_inline_secrets(value: Any, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}" if path else key_text
            if key_text != "token_env" and key_text.lower() in _SECRET_KEYS:
                raise ValueError(
                    "workspace provider registry must reference secret env vars; "
                    f"inline secret key is not allowed: {next_path}"
                )
            _reject_inline_secrets(child, path=next_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_inline_secrets(child, path=f"{path}[{index}]")


__all__ = [
    "WorkspaceProviderRegistry",
    "providers_path",
]
