from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.schema import OpenClawRemoteBindingConfig
from zf.core.workspace.providers import WorkspaceProviderRegistry, providers_path


def test_workspace_provider_registry_stores_openclaw_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))

    registry = WorkspaceProviderRegistry()
    openclaw = registry.upsert_openclaw_binding(
        OpenClawRemoteBindingConfig(
            id="remote",
            base_url="http://127.0.0.1:18789",
            token_env="OPENCLAW_GATEWAY_TOKEN",
            timeout_seconds=3.0,
        ),
        default=True,
    )

    assert registry.path == providers_path()
    assert openclaw.default_binding == "remote"
    assert openclaw.bindings["remote"].base_url == "http://127.0.0.1:18789"
    assert openclaw.bindings["remote"].token_env == "OPENCLAW_GATEWAY_TOKEN"
    raw = json.loads(registry.path.read_text(encoding="utf-8"))
    assert raw["providers"]["openclaw"]["bindings"]["remote"]["token_env"] == "OPENCLAW_GATEWAY_TOKEN"
    assert "token" not in raw["providers"]["openclaw"]["bindings"]["remote"]


def test_workspace_provider_registry_rejects_inline_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace-home"))
    path = providers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "version": 1,
            "workspace_id": "default",
            "providers": {
                "openclaw": {
                    "bindings": {
                        "remote": {
                            "base_url": "http://127.0.0.1:18789",
                            "token": "secret-value",
                        },
                    },
                },
            },
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="inline secret key"):
        WorkspaceProviderRegistry().read()
