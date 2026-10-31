"""Bounded model edits preserve each compiled role's existing behavior."""
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from zf.core.config.schema import RoleConfig, ZfConfig


def role_review_inputs(config: ZfConfig) -> dict[str, dict[str, Any]]:
    result = {}
    for role in config.roles:
        defaults = asdict(RoleConfig(name=role.name))
        values = asdict(role)
        # Flow expansion skips a handwritten role, so preserve its effective
        # skills, triggers, lifecycle and topology when materializing an override.
        base = {key: value for key, value in values.items() if value != defaults[key]}
        base["name"] = role.name
        base["permission_mode"] = role.permission_mode
        if role.backends:
            base.pop("backend", None)
            base["backends"] = list(role.backends)
        else:
            base.pop("backends", None)
            base["backend"] = role.backend
        result[role.name] = {"base": base, "model": role.model,
                             "model_reasoning_effort": role.model_reasoning_effort}
    return result


def role_review_fields(index: int, roles: dict[str, dict[str, Any]]) -> dict:
    return {f"config.{index}.roles.{name}.{key}": {
                "value": role[key], "editable": True,
                "reason": "仅修改该角色的模型或推理强度；保留其技能、权限和拓扑，需重新审核配置。",
            }
            for name, role in roles.items()
            for key in ("model", "model_reasoning_effort")}


def apply_role_field(spec: dict, keys: list[str], value: str, roles: dict) -> None:
    if len(keys) != 3 or keys[0] != "roles" or keys[2] not in {"model", "model_reasoning_effort"}:
        raise ValueError("only an existing role's model and reasoning effort are editable")
    name, key = keys[1:]
    if name not in roles:
        raise ValueError("review role does not exist in the compiled configuration")
    entries = spec.setdefault("roles", [])
    target = next((role for role in entries if role.get("name") == name), None)
    if target is None:
        target = deepcopy(roles[name]["base"])
        entries.append(target)
    target[key] = value
