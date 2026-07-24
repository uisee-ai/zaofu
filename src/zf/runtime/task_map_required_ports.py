"""Mechanical validation for Task Map plan artifact port declarations."""

from __future__ import annotations

import re
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


__all__ = ["required_plan_port_errors"]
