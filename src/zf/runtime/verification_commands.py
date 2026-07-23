"""Mechanical normalization for Task Contract verification commands.

Command selection and sufficiency are Agent/Skill decisions. This module only
preserves stable identities and execution metadata across task-map, snapshot,
Impl, Verify, and Candidate handoffs.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping


class VerificationCommandError(ValueError):
    """A declared verification command is structurally invalid."""


def normalize_verification_commands(
    verification: Any = None,
    *,
    validation: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return independent, identity-preserving command records.

    ``validation.commands`` is canonical. Historical ``verification`` and
    ``validation.command`` values remain adapters and are never joined with
    ``&&``.
    """

    validation_map = dict(validation or {})
    source = validation_map.get("commands")
    if not isinstance(source, list) or not source:
        source = verification
    if source in (None, "", []):
        source = validation_map.get("command")
    raw_items = source if isinstance(source, list) else [source]

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_items):
        if raw in (None, ""):
            continue
        record = dict(raw) if isinstance(raw, Mapping) else {"command": str(raw)}
        command = str(record.get("command") or record.get("cmd") or "").strip()
        if not command:
            raise VerificationCommandError(
                f"verification command[{index}] lacks executable command"
            )
        command_id = str(
            record.get("id") or record.get("command_id") or ""
        ).strip()
        if not command_id:
            command_id = (
                "contract-verification"
                if len(raw_items) == 1
                else f"contract-verification-{index + 1}"
            )
        if command_id in seen_ids:
            raise VerificationCommandError(
                f"duplicate verification command id {command_id!r}"
            )
        seen_ids.add(command_id)
        timeout = _positive_int(record.get("timeout_seconds"), default=900)
        records.append({
            "id": command_id,
            "command": command,
            "command_digest": command_digest(command),
            "acceptance_ids": _string_list(
                record.get("acceptance_ids") or record.get("acceptance_id")
            ),
            "owner": str(record.get("owner") or "impl_self_check").strip(),
            "tier": str(record.get("tier") or "task_non_smoke").strip(),
            "deterministic": bool(record.get("deterministic", True)),
            "reusable": bool(record.get("reusable", True)),
            "timeout_seconds": timeout,
        })
    return records


def task_contract_verification_commands(contract: Any) -> list[dict[str, Any]]:
    validation = getattr(contract, "validation", {})
    return normalize_verification_commands(
        getattr(contract, "verification", ""),
        validation=validation if isinstance(validation, Mapping) else {},
    )


def task_map_verification_commands(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read canonical commands plus historical task-map aliases."""

    validation = raw.get("validation")
    verification = raw.get("verification")
    for key in ("verify_commands", "verification_commands"):
        if verification not in (None, "", []):
            break
        verification = raw.get(key)
    return normalize_verification_commands(
        verification,
        validation=validation if isinstance(validation, Mapping) else {},
    )


def task_map_verification_command_fields(
    raw: Mapping[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    validation = raw.get("validation")
    canonical = bool(
        isinstance(validation, Mapping)
        and isinstance(validation.get("commands"), list)
        and validation.get("commands")
    )
    fallback = (
        "validation.command"
        if isinstance(validation, Mapping) and validation.get("command")
        else "verification"
    )
    return [
        (f"validation.commands[{item['id']}]" if canonical else fallback, item)
        for item in task_map_verification_commands(raw)
    ]


def first_verification_command(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            command = str(
                item.get("command") or item.get("cmd") or ""
                if isinstance(item, Mapping)
                else item or ""
            ).strip()
            if command:
                return command
        return ""
    return str(value or "").strip()


def task_map_contract_verification_fields(
    primary: Mapping[str, Any],
    fallback: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    source = primary if primary else fallback
    commands = task_map_verification_commands(source)
    validation = source.get("validation")
    validation_map = validation if isinstance(validation, Mapping) else {}
    return (
        str(commands[0]["command"] if commands else ""),
        validation_with_commands(validation_map, commands)
        if commands else dict(validation_map),
    )


def validation_with_commands(
    validation: Mapping[str, Any] | None,
    commands: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Preserve unrelated validation metadata and store canonical commands."""

    out = dict(validation or {})
    out.pop("command", None)
    out["commands"] = [
        {
            key: value
            for key, value in dict(command).items()
            if key != "command_digest"
        }
        for command in commands
    ]
    return out


def command_digest(command: str) -> str:
    return hashlib.sha256(str(command).strip().encode("utf-8")).hexdigest()


def _positive_int(value: Any, *, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise VerificationCommandError("timeout_seconds must be an integer") from exc
    if parsed <= 0:
        raise VerificationCommandError("timeout_seconds must be positive")
    return parsed


def _string_list(value: Any) -> list[str]:
    source = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    return list(dict.fromkeys(
        str(item).strip() for item in source if str(item).strip()
    ))


__all__ = [
    "VerificationCommandError",
    "command_digest",
    "first_verification_command",
    "normalize_verification_commands",
    "task_map_contract_verification_fields",
    "task_map_verification_command_fields",
    "task_map_verification_commands",
    "task_contract_verification_commands",
    "validation_with_commands",
]
