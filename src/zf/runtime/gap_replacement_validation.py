"""Preserve explicit and approved verification metadata across gap adapters."""

from copy import deepcopy
from typing import Any


def inherit_unchanged_smoke_marker(
    validation: dict[str, Any], parent_validation: Any,
    parent_id: str, replacement_id: str,
) -> dict[str, Any]:
    result = deepcopy(validation)
    if not isinstance(parent_validation, dict):
        return result
    commands = result.get("commands")
    parents = parent_validation.get("commands")
    if not isinstance(commands, list) or not isinstance(parents, list):
        return result
    ignored = {"rolling_smoke", "producer_task_id"}
    for command in commands:
        if not isinstance(command, dict) or "rolling_smoke" in command:
            continue
        if command.get("producer_task_id") != replacement_id:
            continue
        for old in parents:
            if (isinstance(old, dict) and old.get("rolling_smoke") is True
                    and old.get("producer_task_id") == parent_id
                    and {k: v for k, v in old.items() if k not in ignored}
                    == {k: v for k, v in command.items() if k not in ignored}):
                command["rolling_smoke"] = True
                break
    return result


def with_structured_gap_verification(raw: dict[str, Any]) -> dict[str, Any]:
    """Adapt existing structured command aliases without losing their identities."""
    item = dict(raw)
    structured_verification = item.get("verification")
    catalogs = []
    if isinstance(structured_verification, dict):
        catalogs.append(structured_verification.get("commands"))
    catalogs.extend(
        value for key in ("verification", "verify_commands")
        if isinstance(value := item.get(key), list)
        and any(isinstance(row, dict) for row in value)
    )
    for commands in catalogs:
        if not isinstance(commands, list):
            continue
        validation = item.get("validation")
        if isinstance(validation, dict) and validation.get("commands") not in (None, commands):
            raise ValueError(f"{item.get('task_id') or item.get('id') or ''}: conflicting structured verification commands")
        item["validation"] = {
            **(validation if isinstance(validation, dict) else {}),
            "commands": deepcopy(commands),
        }
    return item
