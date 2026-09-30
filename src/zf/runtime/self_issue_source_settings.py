"""Project-scoped Issue source settings; zf.yaml remains the only control plane."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

from zf.core.config.schema import SelfIssueConfig
from zf.core.config.loader import _config_env_map, _expand_env_vars, ConfigError
from zf.core.security.redaction import redact_text
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.self_issue_target import resolve_self_issue_target_root


def _spec(documents):
    configs = [doc for doc in documents if isinstance(doc, dict) and doc.get("kind") == "ZfConfig"]
    if len(configs) == 1:
        return configs[0]["spec"]
    if len(documents) == 1 and isinstance(documents[0], dict) and not documents[0].get("kind"):
        return documents[0]
    raise ValueError("zf.yaml must contain exactly one ZfConfig")


def current_source_policy(project_root: Path, policy: SelfIssueConfig | None) -> SelfIssueConfig:
    """Read only these settings afresh, including on a long-lived Web server."""
    current = policy or SelfIssueConfig()
    path = project_root / "zf.yaml"
    if not path.is_file():
        return current
    try:
        local = _spec(list(yaml.safe_load_all(path.read_text(encoding="utf-8")))).get("self_issue") or {}
        values = {}
        for key in ("source_mode", "source_root"):
            if key in local:
                if local[key] is not None and not isinstance(local[key], str):
                    raise ValueError(f"self_issue.{key} must be text")
                values[key] = _expand_env_vars(str(local[key] or ""), _config_env_map(path)).strip()
        if "source_mode" in values:
            values["source_mode"] = values["source_mode"].lower() or "current_project"
        return replace(current, **values)
    except (yaml.YAMLError, ConfigError) as exc:
        raise ValueError("无法解析 zf.yaml 的 Issue 源码设置") from exc


def _git(root: Path, *args: str, required: bool = False) -> str:
    result = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                            capture_output=True, text=True, timeout=5, check=False)
    if result.returncode:
        if required:
            raise ValueError("无法读取 Git 工作树状态，不能判断是否有未提交修改")
        return ""
    return result.stdout.strip()


def inspect_source(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("源码目录不存在，或不是目录（路径必须位于运行 Agent 的机器上）")
    # Check readability, not writability: investigation need not edit source.
    try:
        next(root.iterdir(), None)
        git_root = _git(root, "rev-parse", "--show-toplevel")
        revision = _git(root, "rev-parse", "HEAD") if git_root else ""
        branch = _git(root, "symbolic-ref", "--short", "-q", "HEAD") if git_root else ""
        remote = _git(root, "remote", "get-url", "origin") if git_root else ""
        dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=normal", required=True)) if git_root else None
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("无法读取源码目录或 Git 信息") from exc
    if "://" in remote:
        parsed = urlsplit(remote)
        remote = urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
    remote = re.sub(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+", "[REDACTED]", redact_text(remote))
    return {"source_root": str(root), "git_root": git_root, "remote": remote,
            "branch": branch or ("detached HEAD" if revision else ""), "revision": revision,
            "dirty": dirty, "is_git": bool(git_root),
            "warning": "该目录不是 Git checkout；无法绑定 commit。" if not git_root else ""}


def source_settings(project_root: Path, policy: SelfIssueConfig | None, payload=None) -> dict[str, Any]:
    current = current_source_policy(project_root, policy)
    candidate = payload or {}
    mode = str(candidate.get("source_mode", current.source_mode))
    raw_root = str(candidate.get("source_root", current.source_root)).strip()
    if candidate and mode not in {"current_project", "explicit_target", "zaofu_self_issue", "issue_repository"}:
        raise ValueError("unsupported Issue source mode")
    if mode == "explicit_target" and not raw_root:
        raise ValueError("请输入源码目录")
    configured = replace(current, source_mode=mode, source_root=raw_root)
    root = resolve_self_issue_target_root(project_root, configured)
    path = project_root / "zf.yaml"
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
    result = {"ok": True, "source_mode": mode, "configured_root": raw_root,
              "config_digest": digest, "config_path": str(path), "project_root": str(project_root),
              "applies_to": "new_sessions_only",
              # Match publication policy precedence; never infer this from origin.
              "publication_targets": [
                  {"provider": name, "project": redact_text(target.project)}
                  for name, target in current.targets.items()
              ] if current.targets else ([{
                  "provider": current.provider, "project": redact_text(current.target_project),
              }] if current.target_project else [])}
    try:
        result["inspection"] = inspect_source(root)
        result["valid"] = True
    except ValueError as exc:
        result.update(valid=False, reason=str(exc), inspection={"source_root": str(root)})
    return result


def _field(node, key):
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("config section must be a mapping")
    pairs = [value for name, value in node.value if name.value == key]
    if len(pairs) > 1:
        raise ValueError("duplicate config field")
    return pairs[0] if pairs else None


def _insert(text: str, node, fields: dict[str, Any]) -> str:
    """Edit only scalar spans; preserve comments, profiles and other documents."""
    edits = []
    missing = {}
    for key, value in fields.items():
        old = _field(node, key)
        if old is None:
            missing[key] = value
        elif isinstance(old, yaml.ScalarNode):
            edits.append((old.start_mark.index, old.end_mark.index, json.dumps(value, ensure_ascii=False)))
        else:
            raise ValueError("source setting must be a scalar")
    if missing:
        entries = [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in missing.items()]
        if node.flow_style:
            pos = node.end_mark.index - 1
            edits.append((pos, pos, (", " if node.value else "") + ", ".join(entries)))
        else:
            pos = node.start_mark.index - node.start_mark.column
            edits.append((pos, pos, "".join(" " * node.start_mark.column + line + "\n" for line in entries)))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def save_source_settings(project_root: Path, policy: SelfIssueConfig | None, payload: dict[str, Any]) -> dict[str, Any]:
    path = project_root / "zf.yaml"
    if not path.is_file() or path.is_symlink():
        raise ValueError("当前项目需要可直接编辑的 zf.yaml")
    with locked_path(path):
        original = path.read_bytes().decode("utf-8")
        digest = hashlib.sha256(original.encode()).hexdigest()
        if payload.get("config_digest") != digest:
            return {"ok": False, "status": "config_conflict", "reason": "zf.yaml 已变化，请重新读取并检查后保存"}
        mode = str(payload.get("source_mode") or "")
        if mode not in {"current_project", "explicit_target"}:
            raise ValueError("Settings supports current_project or explicit_target")
        checked = source_settings(project_root, policy, payload)
        if not checked["valid"]:
            raise ValueError(checked["reason"])
        root = checked["inspection"]["source_root"] if mode == "explicit_target" else ""
        nodes = list(yaml.compose_all(original))
        configs = [n for n in nodes if isinstance(n, yaml.MappingNode)
                   and _field(n, "kind") is not None and _field(n, "kind").value == "ZfConfig"]
        spec = _field(configs[0], "spec") if len(configs) == 1 else (nodes[0] if len(nodes) == 1 and _field(nodes[0], "kind") is None else None)
        if spec is None:
            raise ValueError("zf.yaml must contain exactly one ZfConfig")
        section = _field(spec, "self_issue")
        if section is None:
            updated = _insert(original, spec, {"self_issue": {"source_mode": mode, "source_root": root}})
        else:
            updated = _insert(original, section, {"source_mode": mode, "source_root": root})
        # Validate semantics and ensure the patch changed no unrelated settings.
        before = list(yaml.safe_load_all(original))
        _spec(before).setdefault("self_issue", {}).update(source_mode=mode, source_root=root)
        if list(yaml.safe_load_all(updated)) != before:
            raise ValueError("configuration patch changed unrelated fields")
        atomic_write_text(path, updated)
    return {**source_settings(project_root, policy), "status": "saved"}
