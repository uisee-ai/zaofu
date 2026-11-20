"""Materialize and validate plan artifact package ports."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_ports import (
    adapt_issue_requirement_port,
    canonical_plan_port_name,
    extract_inline_plan_ports,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


_PORT_REF_KEYS = {
    "requirement_spec": (
        "requirement_spec_ref",
        "product_spec_ref",
        "prd_ref",
        "objective_ref",
    ),
    "issue_spec": ("issue_spec_ref", "requirement_spec_ref", "issue_ref"),
    "task_map": ("task_map_ref",),
    "planning_result": ("planning_result_ref", "plan_ref"),
    "source_inventory": ("source_inventory_ref",),
    "capability_matrix": ("capability_matrix_ref",),
    "acceptance_matrix": ("acceptance_matrix_ref",),
    "test_matrix": ("test_matrix_ref", "regression_test_matrix_ref"),
    "real_e2e_matrix": ("real_e2e_matrix_ref",),
    "source_index": ("source_index_ref",),
    "plan_critique": ("plan_critique_ref", "critic_ref"),
    "project_adapter": ("project_adapter_ref",),
    "accepted_plan": ("accepted_plan_ref",),
    "product_acceptance_spec": ("product_acceptance_spec_ref",),
}
_ENRICHED_MATRIX_PORTS = frozenset({
    "source_inventory",
    "capability_matrix",
    "acceptance_matrix",
    "test_matrix",
    "task_map",
    "real_e2e_matrix",
})


class PlanArtifactPackageError(ValueError):
    """A package cannot be trusted or selected as current."""


def bind_product_acceptance_identity(
    body: Mapping[str, Any], *, workflow_run_id: str, flow_kind: str,
    plan_revision: str, task_map_generation: str,
) -> dict[str, Any]:
    """Apply the same kernel-owned identity at admission and exact replay."""
    from zf.runtime.product_acceptance import bind_product_acceptance_spec_identity

    if not isinstance(body, Mapping):
        raise PlanArtifactPackageError("product_acceptance_spec must contain a JSON object")
    return bind_product_acceptance_spec_identity(
        body, workflow_run_id=workflow_run_id, flow_kind=flow_kind,
        plan_revision=plan_revision, task_map_generation=task_map_generation,
    )


def materialize_explicit_plan_ports(
    *,
    state_dir: Path,
    project_root: Path,
    payload: Mapping[str, Any],
    flow_kind: str,
    required_ports: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    ports: dict[str, dict[str, Any]] = {}
    inline_ports, inline_names = extract_inline_plan_ports(payload)
    logical_names = list(dict.fromkeys([
        *_PORT_REF_KEYS,
        *(
            canonical_plan_port_name(str(name or ""))
            for name in required_ports
            if str(name or "").strip()
        ),
    ]))
    for logical_name in logical_names:
        if logical_name in inline_names:
            continue
        keys = _PORT_REF_KEYS.get(logical_name, (f"{logical_name}_ref",))
        ref = next(
            (
                str(payload.get(key) or "").strip()
                for key in keys
                if str(payload.get(key) or "").strip()
            ),
            "",
        )
        if not ref:
            continue
        source = materialize_attempt_source_ref(
            state_dir=state_dir,
            project_root=project_root,
            ref=ref,
            source_id=f"plan-port:{logical_name}",
            kind=logical_name,
        )
        if not source:
            raise PlanArtifactPackageError(
                f"plan artifact port {logical_name!r} cannot resolve ref {ref!r}"
            )
        ports[logical_name] = {
            "logical_name": logical_name,
            "artifact_kind": logical_name,
            "schema_version": "",
            "producer_stage_id": str(
                payload.get("stage_id") or payload.get("producer_stage_id") or ""
            ),
            "ref": str(source.get("ref") or ""),
            "sha256": str(source.get("sha256") or ""),
        }
    adapt_issue_requirement_port(ports, flow_kind=flow_kind)
    for item in inline_ports:
        if not isinstance(item, Mapping):
            raise PlanArtifactPackageError("plan_ports entries must be objects")
        logical_name = canonical_plan_port_name(str(
            item.get("logical_name")
            or item.get("artifact_kind")
            or item.get("kind")
            or ""
        ))
        if not logical_name:
            raise PlanArtifactPackageError("plan_ports entry requires logical_name")
        body = item.get("body")
        if isinstance(body, Mapping):
            descriptor = write_immutable_json_sidecar(
                state_dir,
                dict(body),
                root="plan-ports",
                kind=logical_name,
                schema_version=str(
                    item.get("schema_version")
                    or body.get("schema_version")
                    or ""
                ),
                created_by="plan-synth",
                source_event_id=str(payload.get("source_event_id") or ""),
            )
        else:
            descriptor = {
                "ref": str(item.get("ref") or ""),
                "sha256": str(item.get("sha256") or item.get("digest") or ""),
            }
            if not descriptor["ref"] or not descriptor["sha256"]:
                raise PlanArtifactPackageError(
                    f"plan port {logical_name!r} requires body or ref/sha256"
                )
            hydrated = hydrate_sidecar_ref(state_dir, descriptor)
            if not hydrated.ok:
                raise PlanArtifactPackageError(
                    f"plan port {logical_name!r} cannot hydrate its descriptor"
                )
        ports[logical_name] = {
            "logical_name": logical_name,
            "artifact_kind": str(item.get("artifact_kind") or logical_name),
            "schema_version": str(
                item.get("schema_version")
                or (
                    body.get("schema_version")
                    if isinstance(body, Mapping)
                    else ""
                )
                or ""
            ),
            "producer_stage_id": str(
                item.get("producer_stage_id")
                or payload.get("stage_id")
                or payload.get("producer_stage_id")
                or ""
            ),
            "ref": str(descriptor.get("ref") or ""),
            "sha256": str(descriptor.get("sha256") or ""),
        }
    if (
        flow_kind == "issue"
        and "issue_spec" not in ports
        and "requirement_spec" in ports
    ):
        ports["issue_spec"] = {
            **ports.pop("requirement_spec"),
            "logical_name": "issue_spec",
            "source_logical_name": "requirement_spec",
            "adapter_version": "plan-artifact-port-adapter.v1",
        }
    return ports


def validate_required_matrix_readiness(
    *,
    state_dir: Path,
    ports: Iterable[Mapping[str, Any]],
    required_ports: Iterable[str],
) -> None:
    required = {
        canonical_plan_port_name(name)
        for name in required_ports
        if canonical_plan_port_name(name) in _ENRICHED_MATRIX_PORTS
    }
    if not required:
        return
    by_name = {
        canonical_plan_port_name(str(port.get("logical_name") or "")): port
        for port in ports
        if isinstance(port, Mapping)
    }
    for name in sorted(required):
        port = by_name.get(name)
        if port is None:
            continue
        hydrated = hydrate_sidecar_ref(
            state_dir,
            {
                "ref": str(port.get("ref") or ""),
                "sha256": str(port.get("sha256") or ""),
            },
        )
        body = hydrated.payload
        if not isinstance(body, Mapping):
            raise PlanArtifactPackageError(
                f"required matrix port {name!r} must contain a JSON object"
            )
        metadata = body.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        contract = metadata.get("enrichment_contract")
        if not isinstance(contract, Mapping):
            continue
        status = str(body.get("status") or "").strip().lower()
        enrichment_status = str(contract.get("status") or "").strip().lower()
        if status != "ready" or enrichment_status != "fulfilled":
            raise PlanArtifactPackageError(
                f"required matrix port {name!r} is not ready: "
                f"status={status or 'missing'}, "
                f"enrichment={enrichment_status or 'missing'}"
            )


__all__ = [
    "PlanArtifactPackageError",
    "materialize_explicit_plan_ports",
    "validate_required_matrix_readiness",
]
