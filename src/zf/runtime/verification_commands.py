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
        normalized = {
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
        }
        producer_paths = _string_list(record.get("producer_paths"))
        if producer_paths:
            normalized["producer_paths"] = producer_paths
        producer_task_id = str(record.get("producer_task_id") or "").strip()
        if producer_task_id:
            normalized["producer_task_id"] = producer_task_id
        if "rolling_smoke" in record:
            marker = record["rolling_smoke"]
            if not isinstance(marker, bool):
                raise VerificationCommandError(
                    f"verification command[{index}].rolling_smoke must be a boolean"
                )
            normalized["rolling_smoke"] = marker
        records.append(normalized)
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


def task_map_command_registry(
    task_map: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return Task Map command definitions keyed by canonical command id.

    Product-flow admission enforces globally unique explicit ids. Legacy task
    maps synthesize task-local ``contract-verification`` ids, so the projection
    keeps their first definition; each legacy task still retains its local
    command before registry inheritance is considered.
    """

    registry: dict[str, dict[str, Any]] = {}
    tasks = task_map.get("tasks")
    for raw in tasks if isinstance(tasks, list) else []:
        if not isinstance(raw, Mapping):
            continue
        for command in task_map_verification_commands(raw):
            command_id = str(command["id"])
            registry.setdefault(command_id, dict(command))
    return registry


def materialize_task_verification_commands(
    raw: Mapping[str, Any],
    *,
    registry: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Project globally referenced definitions into one derived Task contract."""

    commands = task_map_verification_commands(raw)
    command_ids = {str(item["id"]) for item in commands}
    criteria = raw.get("acceptance_criteria")
    if not isinstance(criteria, list):
        criteria = raw.get("acceptance")
    for criterion in criteria if isinstance(criteria, list) else []:
        if not isinstance(criterion, Mapping):
            continue
        for command_id in _string_list(
            criterion.get("verification_command_ids")
            or criterion.get("command_ids")
            or criterion.get("verification_commands")
        ):
            if command_id in command_ids:
                continue
            command = registry.get(command_id)
            if command is None:
                raise VerificationCommandError(
                    f"acceptance criterion references unknown command id {command_id!r}"
                )
            commands.append(dict(command))
            command_ids.add(command_id)
    materialized = dict(raw)
    if not commands:
        return materialized
    validation = raw.get("validation")
    materialized["validation"] = validation_with_commands(
        validation if isinstance(validation, Mapping) else {},
        commands,
    )
    return materialized


def acceptance_required_for_stage(
    criterion: Mapping[str, Any], *, verification_owner: str,
) -> bool:
    """Route immutable AC ownership without waiving later mandatory results."""
    owner = str(criterion.get("verification_owner") or "task_verify")
    if verification_owner in {"impl_self_check", "task_verify"}:
        return owner not in {"candidate_verify", "human"}
    if verification_owner == "candidate_verify":
        return owner != "human"
    return owner == verification_owner


def verification_command_required_for_stage(
    command: Mapping[str, Any],
    *,
    verification_owner: str,
    task_id: str = "",
) -> bool:
    """Select the canonical command subset required by a verification stage."""

    command_owner = str(command.get("owner") or "impl_self_check").strip()
    stage_owner = str(verification_owner or "").strip()
    producer_task_id = str(command.get("producer_task_id") or "").strip()
    if (
        producer_task_id
        and stage_owner in {
            "impl_self_check", "task_verify", "candidate_verify", "candidate_integration",
        }
        and producer_task_id != str(task_id or "").strip()
    ):
        return False
    if stage_owner in {"impl_self_check", "task_verify", "candidate_integration"}:
        # Ownership names the independent acceptance consumer, not permission
        # to claim current local ACs without running their commands in Impl.
        return command_owner not in {"candidate_verify", "human"}
    if stage_owner == "candidate_verify":
        return command_owner != "human"
    return command_owner == stage_owner


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
    "materialize_task_verification_commands",
    "task_map_command_registry",
    "task_map_contract_verification_fields",
    "task_map_verification_command_fields",
    "task_map_verification_commands",
    "task_contract_verification_commands",
    "validation_with_commands",
    "verification_command_required_for_stage",
]
