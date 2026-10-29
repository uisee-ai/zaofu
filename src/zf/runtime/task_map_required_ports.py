"""Mechanical validation for Task Map plan artifact port declarations."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from zf.runtime.plan_artifact_ports import canonical_plan_port_name


_PLAN_PORT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def required_plan_port_errors(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return ["required_plan_ports must be a list when present"]
    if not value:
        return ["required_plan_ports must not be empty when present"]

    errors: list[str] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(
                f"required_plan_ports[{index}] must be a non-empty string"
            )
            continue
        normalized = canonical_plan_port_name(item)
        if not _PLAN_PORT_NAME.fullmatch(normalized):
            errors.append(
                f"required_plan_ports[{index}] has an invalid logical name"
            )
            continue
        if normalized in seen:
            duplicates.add(normalized)
        seen.add(normalized)
    if duplicates:
        errors.append(
            "required_plan_ports contains duplicate canonical names: "
            + ", ".join(sorted(duplicates))
        )
    return errors


def plan_input_declaration_errors(
    payload: dict[str, Any], *, available_ports: Iterable[str] | None = None,
) -> list[str]:
    """Validate optional task selections before admitting a Plan."""
    declared = payload.get("required_plan_ports")
    errors = required_plan_port_errors(declared)
    available = set(declared) if isinstance(declared, list) and all(isinstance(x, str) for x in declared) else set()
    bound = set(available_ports) if available_ports is not None else None
    for task in payload.get("tasks", []) if isinstance(payload.get("tasks"), list) else []:
        if not isinstance(task, dict) or not isinstance(task.get("evidence_contract"), dict):
            continue
        task_id = str(task.get("task_id") or task.get("id") or "")
        local = task["evidence_contract"].get("required_plan_ports")
        errors.extend(
            f"{task_id}.evidence_contract.{error}"
            for error in (required_plan_port_errors(local) if local not in (None, "", []) else [])
        )
        local_ports = {canonical_plan_port_name(x) for x in local} if isinstance(local, list) and all(isinstance(x, str) for x in local) else set()
        if bound is not None and (missing := local_ports - bound):
            errors.append(f"{task_id}.evidence_contract.required_plan_ports contains unbound ports: " + ", ".join(sorted(missing)))
        selectable = available | local_ports
        policy = task["evidence_contract"].get("plan_input_selection")
        if policy is None:
            continue
        prefix = f"{task_id}.evidence_contract.plan_input_selection"
        if not isinstance(policy, dict) or set(policy) != {"schema_version", "implementation", "task-verify"} or policy.get("schema_version") != "task-plan-input-selection.v1":
            errors.append(f"{prefix} has invalid schema")
            continue
        full = {"execution_spec", "task_execution_input", "acceptance_matrix", "test_matrix", "real_e2e_matrix"}
        if not full.issubset(available):
            errors.append(f"{prefix} requires complete plan matrix ports")
        for stage in ("implementation", "task-verify"):
            values = policy.get(stage)
            if (not isinstance(values, list) or not values or
                    any(not isinstance(x, str) or not x.strip() or x != x.strip() for x in values)):
                errors.append(f"{prefix}.{stage} must be a non-empty port list")
            elif len(values) != len(set(values)) or not set(values).issubset(selectable) or "task_execution_input" not in values:
                errors.append(f"{prefix}.{stage} contains duplicate, unbound or missing scoped input")
    return errors


__all__ = ["plan_input_declaration_errors", "required_plan_port_errors"]
