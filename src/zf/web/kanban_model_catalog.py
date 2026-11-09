"""Discover the Codex models and efforts advertised by the installed provider."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.web.headless_agent import CodexHeadlessBackend, HeadlessThreadStore, _CodexRpcClient


def codex_model_catalog(
    project_root: Path, *, state_dir: Path | None = None, thread_key: str = "", task_id: str = "",
) -> dict[str, Any]:
    backend = CodexHeadlessBackend()
    if not backend.available():
        raise RuntimeError("Codex provider unavailable")
    client = _CodexRpcClient(
        backend.build_args(), cwd=project_root, timeout_s=10, tool_timeout_s=10,
    )
    rows: list[dict[str, Any]] = []
    try:
        client.start()
        client.request("initialize", {
            "clientInfo": {"name": "zaofu-model-catalog", "title": "ZaoFu", "version": "1"},
            "capabilities": {"experimentalApi": True},
        })
        client.notify("initialized")
        defaults: dict[str, Any] = {"model": None, "effort": None, "source": "unknown"}
        try:
            config = client.request("config/read", {"cwd": str(project_root), "includeLayers": False}).get("config") or {}
            if isinstance(config, dict):
                defaults = {"model": _public_value(config.get("model")),
                            "effort": _public_value(config.get("model_reasoning_effort")), "source": "provider_config"}
        except RuntimeError:
            pass
        if state_dir is not None and thread_key:
            thread = HeadlessThreadStore(state_dir=state_dir, project_root=project_root).load(thread_key=thread_key, task_id=task_id)
            providers = thread.get("providers")
            provider = providers.get("codex-headless", {}) if isinstance(providers, dict) else {}
            if isinstance(provider, dict):
                baseline = provider.get("one_turn_model_restore")
                if isinstance(baseline, dict) and baseline.get("model") and baseline.get("thinking_level"):
                    defaults = {"model": _public_value(baseline.get("model")),
                                "effort": _public_value(baseline.get("thinking_level")), "source": "session_baseline"}
                elif provider.get("provider_session_id"):
                    defaults = {"model": None, "effort": None, "source": "unknown"}
                    try:
                        read = client.request("thread/read", {"threadId": str(provider["provider_session_id"]), "includeTurns": False})
                        current = read.get("thread") or {}
                        if isinstance(current, dict):
                            defaults = {"model": _public_value(current.get("model")),
                                        "effort": _public_value(current.get("reasoningEffort")), "source": "provider_session"}
                    except RuntimeError:
                        pass
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(10):
            page = client.request("model/list", {"limit": 100, "cursor": cursor})
            for item in page.get("data", []):
                if not isinstance(item, dict) or item.get("hidden"):
                    continue
                model = str(item.get("model") or "").strip()
                if not model or len(model) > 128:
                    continue
                efforts = [
                    str(option.get("reasoningEffort") or "")
                    for option in item.get("supportedReasoningEfforts", [])
                    if isinstance(option, dict) and option.get("reasoningEffort")
                ]
                rows.append({
                    "model": model,
                    "label": str(item.get("displayName") or model),
                    "default": bool(item.get("isDefault")),
                    "default_effort": str(item.get("defaultReasoningEffort") or ""),
                    "efforts": efforts,
                })
            next_cursor = str(page.get("nextCursor") or "")
            if not next_cursor:
                break
            if next_cursor in seen:
                raise RuntimeError("provider repeated model cursor")
            seen.add(next_cursor)
            cursor = next_cursor
        else:
            raise RuntimeError("provider model catalog exceeds page limit")
    finally:
        client.close()
    return {"backend": "codex-headless", "models": rows, "session_defaults": defaults}


def _public_value(value: Any) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 128 and value.strip() == value else None
