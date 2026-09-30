"""Settings projections and narrow, compare-and-swap edits of existing zf.yaml."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from zf.core.config.loader import _build_self_issue, _config_env_map, _expand_env_vars
from zf.core.config.schema import SelfIssueConfig
from zf.core.security.capture_urls import validate_capture_urls
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.self_issue_source_settings import _spec, _field, _insert

USER_FIELDS = {"enabled", "automatic_detection_enabled", "browser_capture_enabled", "browser_capture_denied_urls"}
DEV_FIELDS = {"ingress.enabled", "ingress.poll_interval_seconds"}


def _node_spec(text: str):
    nodes = [n for n in yaml.compose_all(text) if n is not None]
    configs = [n for n in nodes if isinstance(n, yaml.MappingNode) and _field(n, "kind") is not None and _field(n, "kind").value == "ZfConfig"]
    if len(configs) == 1:
        return _field(configs[0], "spec")
    if len(nodes) == 1 and isinstance(nodes[0], yaml.MappingNode) and _field(nodes[0], "kind") is None:
        return nodes[0]
    raise ValueError("zf.yaml must contain exactly one ZfConfig.")


def _read_raw(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    return text, _spec(list(yaml.safe_load_all(text))).get("self_issue") or {}


def current_issue_policy(project_root: Path, policy: SelfIssueConfig | None) -> SelfIssueConfig:
    """Refresh only user-owned settings; source/session bindings remain pinned.

    The loader records the policy file so a session bound to a different source
    checkout does not accidentally read that checkout's settings. An in-flight
    intake pass keeps its original policy; the poller refreshes between passes.
    """
    current = policy or SelfIssueConfig()
    path = Path(current.settings_config_path) if current.settings_config_path else Path(project_root) / "zf.yaml"
    if not path.is_file():
        return current
    _, raw = _read_raw(path)
    fields: dict[str, Any] = {}
    for key in USER_FIELDS:
        if key not in raw:
            continue
        value = raw[key]
        if key == "browser_capture_denied_urls":
            fields[key] = validate_capture_urls(value)
        else:
            if type(value) is not bool:
                raise ValueError(f"self_issue.{key} must be boolean.")
            fields[key] = value
    ingress = raw.get("ingress") or {}
    if ingress:
        queue_fields = {}
        if "enabled" in ingress:
            if type(ingress["enabled"]) is not bool:
                raise ValueError("self_issue.ingress.enabled must be boolean.")
            queue_fields["enabled"] = ingress["enabled"]
        if "poll_interval_seconds" in ingress:
            interval = ingress["poll_interval_seconds"]
            if type(interval) is not int or not 60 <= interval <= 604800:
                raise ValueError("Invalid Issue polling interval.")
            queue_fields["poll_interval_seconds"] = interval
        fields["ingress"] = replace(current.ingress, **queue_fields)
    return replace(current, **fields, settings_config_path=str(path))


def policy_projection(path: Path, policy: SelfIssueConfig, *, developer: bool, inherited: bool) -> dict[str, Any]:
    text, _ = _read_raw(path)
    parsed = _spec(list(yaml.safe_load_all(_expand_env_vars(text, _config_env_map(path))))).get("self_issue") or {}
    loaded = _build_self_issue(parsed)
    # Inherited values absent from this file remain those of the effective policy.
    visible = {key: getattr(loaded if key in parsed else policy, key) for key in USER_FIELDS}
    if developer:
        visible.update({"ingress.enabled": loaded.ingress.enabled if "ingress" in parsed else policy.ingress.enabled,
                        "ingress.poll_interval_seconds": loaded.ingress.poll_interval_seconds if "ingress" in parsed else policy.ingress.poll_interval_seconds})
    return {"ok": True, "values": visible, "config_digest": hashlib.sha256(text.encode()).hexdigest(),
            "config_path": str(path), "scope": "workspace" if inherited else "project",
            "inherited": inherited, "writable": not path.is_symlink(),
            "queue_requires_restart": False, "user_changes_apply_to": "next_operation"}


def _patch(text: str, parts: list[str], value: Any) -> str:
    node = _node_spec(text)
    for index, part in enumerate(parts):
        child = _field(node, part)
        if child is None:
            payload = value
            for nested in reversed(parts[index + 1:]):
                payload = {nested: payload}
            return _insert(text, node, {part: payload})
        if index == len(parts) - 1:
            if isinstance(child, yaml.ScalarNode):
                return _insert(text, node, {part: value})
            if isinstance(child, yaml.SequenceNode) and isinstance(value, list):
                start, end = child.start_mark.index, child.end_mark.index
                # Block sequences include their terminating newline in the span.
                tail = "\n" + " " * child.end_mark.column if not child.flow_style else ""
                return text[:start] + json.dumps(value, ensure_ascii=False) + tail + text[end:]
            raise ValueError("Unexpected setting value shape.")
        node = child
    return text


def save_issue_policy(path: Path, payload: dict[str, Any], *, developer: bool) -> dict[str, Any]:
    changes = payload.get("changes")
    if not isinstance(changes, dict) or not changes or len(changes) > 6:
        raise ValueError("Supply a nonempty set of settings changes.")
    allowed = USER_FIELDS | (DEV_FIELDS if developer else set())
    if not changes.keys() <= allowed:
        raise PermissionError("This setting is not writable with the current capability.")
    clean = dict(changes)
    for key, value in changes.items():
        if key == "browser_capture_denied_urls":
            clean[key] = validate_capture_urls(value)
        elif key == "ingress.poll_interval_seconds":
            if type(value) is not int or not 60 <= value <= 604800:
                raise ValueError("GitHub sync interval must be an integer from 60 to 604800 seconds.")
        elif type(value) is not bool:
            raise ValueError(f"{key} must be boolean.")
    if not path.is_file() or path.is_symlink():
        raise ValueError("A regular project zf.yaml is required.")
    with locked_path(path):
        original = path.read_text(encoding="utf-8")
        if payload.get("config_digest") != hashlib.sha256(original.encode()).hexdigest():
            return {"ok": False, "status": "config_conflict", "reason": "Configuration changed. Reload before saving."}
        updated = original
        for key, value in clean.items():
            updated = _patch(updated, ["self_issue", *key.split(".")], value)
        expected = list(yaml.safe_load_all(original))
        policy = _spec(expected).setdefault("self_issue", {})
        for key, value in clean.items():
            target = policy
            parts = key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        if list(yaml.safe_load_all(updated)) != expected:
            raise ValueError("Unrelated configuration would be changed; refusing save.")
        # Apply existing policy semantics (provider/target/permission constraints).
        _build_self_issue(_spec(list(yaml.safe_load_all(_expand_env_vars(updated, _config_env_map(path))))).get("self_issue"))
        atomic_write_text(path, updated)
    return {"ok": True, "status": "saved", "changed_fields": sorted(clean),
            "restart_required": False}
