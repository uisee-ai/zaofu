"""Capability/acceptance/test closure checks for generated products."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .manifest import ManifestError


def validate_matrix(
    *,
    capabilities: Sequence[Mapping[str, Any]],
    acceptances: Sequence[Mapping[str, Any]],
    commands: Sequence[Mapping[str, Any]],
) -> None:
    """Require every blocking capability to reach an acceptance and command."""

    def ids(rows: Sequence[Mapping[str, Any]], label: str) -> set[str]:
        result: set[str] = set()
        for index, row in enumerate(rows):
            value = row.get("id")
            if not isinstance(value, str) or not value.strip():
                raise ManifestError(f"{label}[{index}].id must be non-empty")
            if value in result:
                raise ManifestError(f"duplicate {label} id: {value}")
            result.add(value)
        return result

    capability_ids = ids(capabilities, "capability")
    acceptance_ids = ids(acceptances, "acceptance")
    command_ids = ids(commands, "command")
    accepted: set[str] = set()
    tested: set[str] = set()
    for row in acceptances:
        refs = row.get("capability_ids", [])
        if not isinstance(refs, list) or any(ref not in capability_ids for ref in refs):
            raise ManifestError(f"acceptance {row['id']} references unknown capability")
        accepted.update(refs)
    for row in commands:
        refs = row.get("acceptance_ids", [])
        if not isinstance(refs, list) or any(ref not in acceptance_ids for ref in refs):
            raise ManifestError(f"command {row['id']} references unknown acceptance")
        tested.update(refs)
    blocking = {
        row["id"] for row in capabilities if row.get("blocking", True) is True
    }
    missing_acceptance = sorted(blocking - accepted)
    if missing_acceptance:
        raise ManifestError(f"blocking capabilities lack acceptance: {missing_acceptance}")
    missing_tests = sorted(acceptance_ids - tested)
    if missing_tests:
        raise ManifestError(f"acceptances lack test command: {missing_tests}")

