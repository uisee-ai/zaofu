"""Strict adapter from a product ``test-matrix.v1`` to TestKit manifest data."""

from __future__ import annotations

from typing import Any, Mapping

from .manifest import ManifestError, SCHEMA_VERSION

_TIER_MAP = {
    "static": "unit",
    "unit": "unit",
    "runtime": "integration",
    "integration": "integration",
    "e2e": "e2e",
    "security": "security",
    "perf": "perf",
    "release": "release",
}


def audit_test_matrix(matrix: Mapping[str, Any]) -> tuple[str, ...]:
    """Return all structural blockers without inferring product semantics."""
    issues: list[str] = []
    if matrix.get("schema_version") != "test-matrix.v1":
        return ("unsupported test matrix schema",)
    commands = matrix.get("commands")
    if not isinstance(commands, list) or not commands:
        return ("test matrix commands must be a non-empty list",)
    for index, item in enumerate(commands):
        if not isinstance(item, Mapping):
            issues.append(f"commands[{index}] must be an object")
            continue
        if str(item.get("tier", "")) not in _TIER_MAP:
            issues.append(f"commands[{index}] tier cannot be mapped")
        for field in ("id", "command"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                issues.append(f"commands[{index}] requires {field}")
        for field in ("acceptance_ids", "domain_ids"):
            value = item.get(field)
            if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
                issues.append(f"commands[{index}] requires {field}")
    return tuple(issues)


def adapt_test_matrix(
    matrix: Mapping[str, Any], *, target_commit: str, source_root: str = "."
) -> dict[str, Any]:
    """Convert only explicitly complete matrix commands; fail closed otherwise.

    The adapter does not invent acceptance or domain mappings.  Products must
    enrich those fields before the resulting manifest can enter execution.
    """

    issues = audit_test_matrix(matrix)
    if issues:
        raise ManifestError("; ".join(issues))
    commands = matrix.get("commands")
    adapted: list[dict[str, Any]] = []
    acceptance_ids: set[str] = set()
    for index, item in enumerate(commands):
        tier = _TIER_MAP.get(str(item.get("tier", "")))
        cid = item.get("id")
        command = item.get("command")
        refs = item.get("acceptance_ids")
        domains = item.get("domain_ids")
        acceptance_ids.update(refs)
        adapted.append({
            "id": cid, "command": command, "tier": tier,
            "acceptance_ids": list(refs), "domain_ids": list(domains),
            "mandatory": bool(item.get("mandatory", True)),
            "deterministic": bool(item.get("deterministic", True)),
            "timeout_seconds": item.get("timeout_seconds", 300),
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "project_type": str(matrix.get("project_type", "other")),
        "source_root": source_root,
        "target_commit": target_commit,
        "setup": [], "commands": adapted,
        "acceptances": [{"id": value} for value in sorted(acceptance_ids)],
        "entrypoints": [], "capabilities": {}, "budgets": {},
        "not_applicable": {}, "cleanup": [],
    }

