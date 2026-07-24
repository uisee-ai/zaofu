"""Logical port compatibility for plan-level artifacts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


PLAN_ARTIFACT_PORT_ADAPTER_VERSION = "plan-artifact-port-adapter.v1"

_CANONICAL_PORTS = frozenset({
    "requirement_spec",
    "issue_spec",
    "goal_claim_set",
    "task_map",
    "planning_result",
    "accepted_plan",
    "source_inventory",
    "capability_matrix",
    "acceptance_matrix",
    "test_matrix",
    "real_e2e_matrix",
    "source_index",
    "plan_critique",
    "project_adapter",
})

_LEGACY_ALIASES = {
    "product_spec": "requirement_spec",
    "prd_ref": "requirement_spec",
    "issue_ref": "issue_spec",
}


def canonical_plan_port_name(name: str) -> str:
    normalized = str(name or "").strip()
    return _LEGACY_ALIASES.get(normalized, normalized)


def plan_port_adapter(name: str) -> dict[str, str]:
    source = str(name or "").strip()
    canonical = canonical_plan_port_name(source)
    return {
        "logical_name": canonical,
        "source_logical_name": source,
        "adapter_version": PLAN_ARTIFACT_PORT_ADAPTER_VERSION,
    }


def normalize_plan_port(
    port: Mapping[str, Any],
    *,
    inherited: bool = False,
) -> dict[str, Any]:
    """Normalize one explicit descriptor without discovering files."""

    normalized = dict(port)
    source_name = str(
        normalized.get("logical_name")
        or normalized.get("artifact_kind")
        or normalized.get("kind")
        or ""
    ).strip()
    adapter = plan_port_adapter(source_name)
    normalized["logical_name"] = adapter["logical_name"]
    if adapter["logical_name"] != source_name:
        normalized["source_logical_name"] = source_name
        normalized["adapter_version"] = adapter["adapter_version"]
    normalized["artifact_kind"] = str(
        normalized.get("artifact_kind") or normalized.get("kind") or source_name
    )
    normalized["schema_version"] = str(normalized.get("schema_version") or "")
    normalized["ref"] = str(normalized.get("ref") or "")
    normalized["sha256"] = str(normalized.get("sha256") or "")
    if inherited:
        normalized["source_package_ref"] = str(
            normalized.get("source_package_ref") or ""
        )
        normalized["source_package_digest"] = str(
            normalized.get("source_package_digest") or ""
        )
    return normalized


def normalize_plan_ports(
    ports: Iterable[Mapping[str, Any]],
    *,
    inherited: bool = False,
) -> list[dict[str, Any]]:
    normalized = [
        normalize_plan_port(port, inherited=inherited)
        for port in ports
        if isinstance(port, Mapping)
    ]
    names = [str(port.get("logical_name") or "") for port in normalized]
    duplicates = sorted({name for name in names if name and names.count(name) > 1})
    if duplicates:
        raise ValueError(
            "duplicate canonical plan artifact ports: " + ", ".join(duplicates)
        )
    return sorted(normalized, key=lambda item: str(item.get("logical_name") or ""))


def is_known_plan_port(name: str) -> bool:
    return canonical_plan_port_name(name) in _CANONICAL_PORTS
