"""Project-local developer execution settings, persisted only in zf.yaml."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
import yaml

from zf.core.config.schema import ZfConfig
from zf.core.config.loader import ConfigError
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.issue_settings import _patch, _read_raw
from zf.runtime.self_issue_source_settings import _spec
from zf.runtime.issue_execution import execution_context, project_identity


def execution_settings(root: Path, config: ZfConfig, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    path = root / "zf.yaml"
    text, raw = _read_raw(path)
    value = raw.get("execution_project_root", config.self_issue.execution_project_root)
    if payload is not None:
        value = payload.get("execution_project_root", "")
    if not isinstance(value, str) or len(value) > 4096 or "\x00" in value:
        raise ValueError("执行项目目录必须是有效路径文本。")
    value = value.strip()
    candidate = Path(value).expanduser() if value else root
    candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
    result = {"ok": True, "configured_root": value, "project_root": str(root),
              "execution_project_root": str(candidate), "config_path": str(path),
              "config_digest": hashlib.sha256(text.encode()).hexdigest(),
              "applies_to": "new_issues_only", "writable": not path.is_symlink()}
    try:
        context = execution_context(candidate, config)
        result.update(project_identity(context.project_root, context.config))
        result.update(valid=True, state_dir=str(context.state_dir))
    except (OSError, ValueError, ConfigError) as exc:
        result.update(valid=False, reason=str(exc))
    return result


def save_execution_settings(root: Path, config: ZfConfig, payload: dict[str, Any]) -> dict[str, Any]:
    path = root / "zf.yaml"
    if path.is_symlink() or not path.is_file():
        raise ValueError("需要当前项目的常规 zf.yaml 文件。")
    if set(payload) - {"execution_project_root", "config_digest"}:
        raise ValueError("Only execution_project_root may be changed here")
    with locked_path(path):
        text = path.read_text()
        if payload.get("config_digest") != hashlib.sha256(text.encode()).hexdigest():
            return {"ok": False, "status": "config_conflict", "reason": "配置已变更，请重新读取后保存。"}
        inspected = execution_settings(root, config, payload)
        if not inspected["valid"]:
            raise ValueError(inspected["reason"])
        value = inspected["configured_root"]
        updated = _patch(text, ["self_issue", "execution_project_root"], value)
        expected = list(yaml.safe_load_all(text))
        _spec(expected).setdefault("self_issue", {})["execution_project_root"] = value
        if list(yaml.safe_load_all(updated)) != expected:
            raise ValueError("Refusing unrelated configuration changes")
        atomic_write_text(path, updated)
    return {**execution_settings(root, config), "changed_fields": ["execution_project_root"], "restart_required": False}
