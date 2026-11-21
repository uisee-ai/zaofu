"""Logical port compatibility for plan-level artifacts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


PLAN_ARTIFACT_PORT_ADAPTER_VERSION = "plan-artifact-port-adapter.v1"

# Planner-authored JSON drafts. Requirements, approvals and claims retain
# their own authorities and are never editable exports.
PLAN_DRAFT_PORTS = (
    "task_map", "test_matrix", "source_index", "source_inventory",
    "acceptance_matrix", "real_e2e_matrix", "capability_matrix", "coverage_ledger",
    "execution_spec", "task_execution_input", "product_acceptance_spec",
)

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
    "product_acceptance_spec",
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


def coerce_plan_port_descriptors(value: Any) -> list[dict[str, Any]]:
    """Accept the canonical descriptor list or the name-to-body shorthand."""

    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []

    descriptors: list[dict[str, Any]] = []
    for raw_name, raw_body in value.items():
        logical_name = str(raw_name or "").strip()
        if not logical_name or not isinstance(raw_body, Mapping):
            continue
        body = dict(raw_body)
        descriptors.append({
            "logical_name": logical_name,
            "schema_version": str(body.get("schema_version") or ""),
            "body": body,
        })
    return descriptors


def is_known_plan_port(name: str) -> bool:
    return canonical_plan_port_name(name) in _CANONICAL_PORTS


def extract_inline_plan_ports(
    payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], set[str]]:
    ports = coerce_plan_port_descriptors(payload.get("plan_ports"))
    synthesis_result = payload.get("plan_synthesis_result")
    if not ports and isinstance(synthesis_result, Mapping):
        ports = coerce_plan_port_descriptors(
            synthesis_result.get("plan_ports")
        )
    names = {
        canonical_plan_port_name(str(
            item.get("logical_name")
            or item.get("artifact_kind")
            or item.get("kind")
            or ""
        ))
        for item in ports
        if isinstance(item, Mapping)
    }
    return ports, names


def adapt_issue_requirement_port(
    ports: dict[str, dict[str, Any]],
    *,
    flow_kind: str,
) -> None:
    if str(flow_kind or "").strip().lower() != "issue":
        return
    requirement = ports.get("requirement_spec")
    issue = ports.get("issue_spec")
    if not requirement or (
        issue
        and (
            issue.get("ref"),
            issue.get("sha256"),
        ) != (
            requirement.get("ref"),
            requirement.get("sha256"),
        )
    ):
        return
    ports["issue_spec"] = {
        **requirement,
        "logical_name": "issue_spec",
        "artifact_kind": "issue_spec",
        "source_logical_name": "requirement_spec",
        "adapter_version": "issue-spec-requirement-adapter.v1",
    }
