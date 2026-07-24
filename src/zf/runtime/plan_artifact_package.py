"""Immutable current plan artifact packages for Product Flow runs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.goal_claim_set import (
    canonical_task_map_generation,
    pin_goal_claim_set_from_task_map,
)
from zf.runtime.plan_artifact_ports import (
    canonical_plan_port_name,
    normalize_plan_ports,
)
from zf.runtime.run_contract import (
    load_run_contract,
    load_run_contract_snapshot,
    write_run_contract_snapshot,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


PLAN_ARTIFACT_PACKAGE_SCHEMA = "plan-artifact-package.v1"
PLAN_ARTIFACT_PACKAGE_SLOT = "execution_plan"
PLAN_ARTIFACT_PACKAGE_MODES = frozenset({"off", "shadow", "blocking"})

_PORT_REF_KEYS = {
    "requirement_spec": (
        "requirement_spec_ref",
        "product_spec_ref",
        "prd_ref",
        "objective_ref",
    ),
    "issue_spec": ("issue_spec_ref", "issue_ref"),
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


def artifact_package_mode(metadata: Mapping[str, Any] | None) -> str:
    raw = metadata or {}
    policy = raw.get("artifact_package")
    policy = policy if isinstance(policy, Mapping) else {}
    mode = str(
        policy.get("mode")
        or raw.get("artifact_package_mode")
        or "shadow"
    ).strip().lower()
    return mode if mode in PLAN_ARTIFACT_PACKAGE_MODES else "shadow"


def required_plan_ports(
    *,
    flow_kind: str,
    metadata: Mapping[str, Any] | None = None,
    declared: Iterable[object] = (),
) -> list[str]:
    task_map_ports = _normalize_required_port_names(declared)
    raw = metadata or {}
    policy = raw.get("artifact_package")
    policy = policy if isinstance(policy, Mapping) else {}
    explicit = _strings(policy.get("required_ports"))
    if explicit:
        profile_ports = _normalize_required_port_names(explicit)
    else:
        requirement = "issue_spec" if flow_kind == "issue" else "requirement_spec"
        profile_ports = [
            requirement,
            "goal_claim_set",
            "task_map",
            "planning_result",
        ]
    return list(dict.fromkeys([*profile_ports, *task_map_ports]))


def build_plan_artifact_package(
    *,
    workflow_run_id: str,
    flow_kind: str,
    producer_stage_id: str,
    run_contract: Mapping[str, Any],
    plan_revision: str,
    task_map_generation: str,
    produced: Iterable[Mapping[str, Any]],
    inherited: Iterable[Mapping[str, Any]] = (),
    required_ports: Iterable[str] = (),
    request_id: str = "",
    package_slot: str = PLAN_ARTIFACT_PACKAGE_SLOT,
    supersedes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_produced = normalize_plan_ports(produced)
    normalized_inherited = normalize_plan_ports(inherited, inherited=True)
    all_names = [
        str(item.get("logical_name") or "")
        for item in [*normalized_produced, *normalized_inherited]
    ]
    duplicates = sorted({name for name in all_names if name and all_names.count(name) > 1})
    if duplicates:
        raise PlanArtifactPackageError(
            "duplicate plan artifact ports: " + ", ".join(duplicates)
        )
    required = list(dict.fromkeys(_strings(required_ports)))
    missing = sorted(set(required) - set(all_names))
    if missing:
        raise PlanArtifactPackageError(
            "missing required plan artifact ports: " + ", ".join(missing)
        )
    if not workflow_run_id or not flow_kind or not producer_stage_id:
        raise PlanArtifactPackageError(
            "workflow_run_id, flow_kind, and producer_stage_id are required"
        )
    run_contract_ref = str(run_contract.get("ref") or "")
    run_contract_sha = str(run_contract.get("sha256") or "")
    contract_digest = str(run_contract.get("contract_digest") or "")
    if not run_contract_ref or not run_contract_sha or not contract_digest:
        raise PlanArtifactPackageError(
            "immutable run contract ref, sha256, and contract_digest are required"
        )
    body: dict[str, Any] = {
        "schema_version": PLAN_ARTIFACT_PACKAGE_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "flow_kind": flow_kind,
        "package_slot": package_slot,
        "producer_stage_id": producer_stage_id,
        "run_contract_ref": run_contract_ref,
        "run_contract_sha256": run_contract_sha,
        "run_contract_digest": contract_digest,
        "plan_revision": plan_revision,
        "task_map_generation": task_map_generation,
        "required_ports": required,
        "produced": normalized_produced,
        "inherited": normalized_inherited,
    }
    if request_id:
        body["request_id"] = request_id
    prior = supersedes or {}
    if prior:
        body["supersedes_package_ref"] = str(
            prior.get("package_ref") or prior.get("ref") or ""
        )
        body["supersedes_package_digest"] = str(
            prior.get("package_digest") or prior.get("digest") or prior.get("sha256") or ""
        )
    validate_plan_artifact_package_shape(body)
    return body


def validate_plan_artifact_package_shape(package: Mapping[str, Any]) -> None:
    if package.get("schema_version") != PLAN_ARTIFACT_PACKAGE_SCHEMA:
        raise PlanArtifactPackageError("unsupported plan artifact package schema")
    forbidden = {
        "status",
        "package_id",
        "created_at",
        "created_event_id",
        "admitted_event_id",
    }
    present = sorted(forbidden.intersection(package))
    if present:
        raise PlanArtifactPackageError(
            "mutable or self-derived package fields are forbidden: " + ", ".join(present)
        )
    for key in (
        "workflow_run_id",
        "flow_kind",
        "package_slot",
        "producer_stage_id",
        "run_contract_ref",
        "run_contract_sha256",
        "run_contract_digest",
        "plan_revision",
        "task_map_generation",
    ):
        if not str(package.get(key) or ""):
            raise PlanArtifactPackageError(f"{key} is required")
    ports = [
        item
        for group in ("produced", "inherited")
        for item in package.get(group, [])
        if isinstance(item, Mapping)
    ]
    names: list[str] = []
    for port in ports:
        name = str(port.get("logical_name") or "")
        if not name or not str(port.get("ref") or "") or not str(port.get("sha256") or ""):
            raise PlanArtifactPackageError(
                "each plan port requires logical_name, ref, and sha256"
            )
        names.append(name)
        if port in package.get("inherited", []):
            if not str(port.get("source_package_ref") or "") or not str(
                port.get("source_package_digest") or ""
            ):
                raise PlanArtifactPackageError(
                    f"inherited port {name!r} is missing source package identity"
                )
    if len(names) != len(set(names)):
        raise PlanArtifactPackageError("plan artifact logical names must be unique")


def write_plan_artifact_package(
    state_dir: Path,
    package: Mapping[str, Any],
    *,
    source_event_id: str = "",
) -> dict[str, Any]:
    validate_plan_artifact_package_shape(package)
    descriptor = write_immutable_json_sidecar(
        state_dir,
        package,
        root="plan-packages",
        kind="plan_artifact_package",
        schema_version=PLAN_ARTIFACT_PACKAGE_SCHEMA,
        created_by="plan-artifact-package",
        source_event_id=source_event_id,
    )
    return {
        **descriptor,
        "package_id": f"planpkg-{descriptor['sha256']}",
        "package_digest": descriptor["sha256"],
    }


def hydrate_plan_artifact_package(
    state_dir: Path,
    descriptor: Mapping[str, Any],
    *,
    validate_ports: bool = True,
) -> dict[str, Any]:
    hydrated = hydrate_sidecar_ref(state_dir, dict(descriptor))
    if not hydrated.ok or not isinstance(hydrated.payload, dict):
        raise PlanArtifactPackageError("plan artifact package is not a JSON object")
    package = dict(hydrated.payload)
    validate_plan_artifact_package_shape(package)
    load_run_contract_snapshot(
        state_dir,
        {
            "ref": package["run_contract_ref"],
            "sha256": package["run_contract_sha256"],
        },
    )
    if validate_ports:
        for port in [*package["produced"], *package["inherited"]]:
            hydrate_sidecar_ref(
                state_dir,
                {"ref": port["ref"], "sha256": port["sha256"]},
            )
        for port in package["inherited"]:
            source = hydrate_sidecar_ref(
                state_dir,
                {
                    "ref": port["source_package_ref"],
                    "sha256": port["source_package_digest"],
                },
            )
            if not source.ok:
                raise PlanArtifactPackageError("inherited source package cannot be hydrated")
    return package


def reduce_plan_artifact_packages(
    events: Iterable[ZfEvent | Mapping[str, Any]],
    *,
    workflow_run_id: str,
    package_slot: str = PLAN_ARTIFACT_PACKAGE_SLOT,
) -> dict[str, Any]:
    current: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    revisions: dict[str, str] = {}
    for event in events:
        event_type, event_id, payload = _event_parts(event)
        if event_type not in {
            "plan.artifact_package.admitted",
            "plan.artifact_package.rejected",
            "plan.artifact_package.superseded",
        }:
            continue
        if str(payload.get("workflow_run_id") or "") != workflow_run_id:
            continue
        if str(payload.get("package_slot") or PLAN_ARTIFACT_PACKAGE_SLOT) != package_slot:
            continue
        row = {
            **payload,
            "event_id": event_id,
            "event_type": event_type,
        }
        if event_type == "plan.artifact_package.rejected":
            rejected.append(row)
            continue
        if event_type == "plan.artifact_package.superseded":
            continue
        revision = str(payload.get("plan_revision") or "")
        digest = str(payload.get("package_digest") or payload.get("package_sha256") or "")
        existing = revisions.get(revision)
        if revision and existing and existing != digest:
            raise PlanArtifactPackageError(
                f"conflicting admitted package for plan revision {revision}"
            )
        if revision:
            revisions[revision] = digest
        if current and str(current.get("package_digest") or "") != digest:
            history.append({**current, "status": "superseded"})
        current = {**row, "status": "current"}
    return {
        "schema_version": "plan-artifact-package-reducer.v1",
        "workflow_run_id": workflow_run_id,
        "package_slot": package_slot,
        "current": current,
        "history": history,
        "rejected": rejected,
        "freshness": {
            "last_event_id": str((current or {}).get("event_id") or ""),
            "admitted_count": len(history) + (1 if current else 0),
        },
    }


def prepare_plan_artifact_package(
    *,
    state_dir: Path,
    project_root: Path,
    events: Iterable[ZfEvent | Mapping[str, Any]],
    payload: Mapping[str, Any],
    workflow_run_id: str,
    flow_kind: str,
    producer_stage_id: str,
    goal_id: str,
    metadata: Mapping[str, Any] | None = None,
    source_event_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build Claim Set and Package from explicit caller refs only."""

    mode = artifact_package_mode(metadata)
    if mode == "off":
        return {}, {}, {"mode": mode, "status": "off"}
    task_map_ref = str(payload.get("task_map_ref") or "")
    if not task_map_ref:
        raise PlanArtifactPackageError("task_map_ref is required")
    task_map = _load_package_task_map(
        state_dir=state_dir,
        project_root=project_root,
        task_map_ref=task_map_ref,
    )
    generation = canonical_task_map_generation(
        task_map_generation=payload.get("task_map_generation"),
        task_map_digest=payload.get("task_map_digest"),
        task_map_ref=task_map_ref,
    )
    objective_ref = str(
        payload.get("objective_ref")
        or payload.get("prd_ref")
        or payload.get("product_spec_ref")
        or payload.get("issue_ref")
        or ""
    )
    claim_set, claim_descriptor = pin_goal_claim_set_from_task_map(
        state_dir=state_dir,
        project_root=project_root,
        task_map_ref=task_map_ref,
        workflow_run_id=workflow_run_id,
        goal_id=goal_id,
        task_map_generation=generation,
        objective_ref=objective_ref,
        source_event_id=source_event_id,
    )
    explicit_ports = _materialize_explicit_ports(
        state_dir=state_dir,
        project_root=project_root,
        payload=payload,
        flow_kind=flow_kind,
    )
    explicit_ports["goal_claim_set"] = {
        "logical_name": "goal_claim_set",
        "artifact_kind": "goal_claim_set",
        "schema_version": str(claim_set.get("schema_version") or ""),
        "producer_stage_id": producer_stage_id,
        "ref": str(claim_descriptor.get("ref") or ""),
        "sha256": str(claim_descriptor.get("sha256") or ""),
    }
    if "planning_result" not in explicit_ports and "task_map" in explicit_ports:
        explicit_ports["planning_result"] = {
            **explicit_ports["task_map"],
            "logical_name": "planning_result",
            "source_logical_name": "task_map",
            "adapter_version": "planning-result-task-map-adapter.v1",
        }
    reduced = reduce_plan_artifact_packages(
        events,
        workflow_run_id=workflow_run_id,
    )
    previous = reduced.get("current")
    previous = previous if isinstance(previous, Mapping) else {}
    inherited: list[dict[str, Any]] = []
    if previous and previous.get("package_ref") and previous.get("package_digest"):
        prior_body = hydrate_plan_artifact_package(
            state_dir,
            {
                "ref": previous["package_ref"],
                "sha256": previous["package_digest"],
            },
        )
        for port in [*prior_body["produced"], *prior_body["inherited"]]:
            name = str(port.get("logical_name") or "")
            if name in explicit_ports:
                continue
            inherited.append({
                **port,
                "source_package_ref": str(previous["package_ref"]),
                "source_package_digest": str(previous["package_digest"]),
            })
    active_contract = load_run_contract(state_dir)
    if not active_contract:
        raise PlanArtifactPackageError("active run contract is missing")
    run_contract = write_run_contract_snapshot(
        state_dir,
        active_contract,
        source_event_id=source_event_id,
    )
    required_ports = required_plan_ports(
        flow_kind=flow_kind,
        metadata=metadata,
        declared=(
            task_map.get("required_plan_ports")
            if "required_plan_ports" in task_map
            else ()
        ),
    )
    _validate_required_matrix_readiness(
        state_dir=state_dir,
        ports=[*explicit_ports.values(), *inherited],
        required_ports=required_ports,
    )
    package = build_plan_artifact_package(
        workflow_run_id=workflow_run_id,
        request_id=str(payload.get("request_id") or ""),
        flow_kind=flow_kind,
        producer_stage_id=producer_stage_id,
        run_contract=run_contract,
        plan_revision=str(
            payload.get("plan_revision")
            or payload.get("revision")
            or generation
        ),
        task_map_generation=generation,
        produced=explicit_ports.values(),
        inherited=inherited,
        required_ports=required_ports,
        supersedes=previous,
    )
    descriptor = write_plan_artifact_package(
        state_dir,
        package,
        source_event_id=source_event_id,
    )
    return package, descriptor, {
        "mode": mode,
        "status": "ready",
        "goal_claim_set_ref": str(claim_descriptor.get("ref") or ""),
        "goal_claim_set_digest": str(claim_descriptor.get("sha256") or ""),
        "goal_claim_set_content_digest": str(claim_set.get("claim_set_digest") or ""),
    }


def package_event_payload(
    package: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    *,
    status: str,
    reason: str = "",
) -> dict[str, Any]:
    payload = {
        "status": status,
        "workflow_run_id": str(package.get("workflow_run_id") or ""),
        "flow_kind": str(package.get("flow_kind") or ""),
        "package_slot": str(package.get("package_slot") or PLAN_ARTIFACT_PACKAGE_SLOT),
        "producer_stage_id": str(package.get("producer_stage_id") or ""),
        "plan_revision": str(package.get("plan_revision") or ""),
        "task_map_generation": str(package.get("task_map_generation") or ""),
        "package_id": str(descriptor.get("package_id") or ""),
        "package_ref": str(descriptor.get("ref") or ""),
        "package_digest": str(descriptor.get("package_digest") or descriptor.get("sha256") or ""),
        "run_contract_ref": str(package.get("run_contract_ref") or ""),
        "run_contract_digest": str(package.get("run_contract_digest") or ""),
        "supersedes_package_ref": str(package.get("supersedes_package_ref") or ""),
        "supersedes_package_digest": str(package.get("supersedes_package_digest") or ""),
    }
    if reason:
        payload["reason"] = reason
    return payload


def admit_plan_artifact_package_for_payload(
    *,
    state_dir: Path,
    project_root: Path,
    event_writer: EventWriter,
    events: Iterable[ZfEvent | Mapping[str, Any]],
    payload: Mapping[str, Any],
    workflow_run_id: str,
    flow_kind: str,
    producer_stage_id: str,
    goal_id: str,
    metadata: Mapping[str, Any] | None = None,
    source_event_id: str = "",
    correlation_id: str = "",
) -> dict[str, Any]:
    """Admit one package and return identity fields for downstream payloads.

    Shadow mode records deterministic diagnostics and leaves the legacy path
    available. Blocking mode raises before canonical task materialization.
    """

    mode = artifact_package_mode(metadata)
    if mode == "off":
        return {"artifact_package_mode": "off"}
    event_list = list(events)
    reduced_before = reduce_plan_artifact_packages(
        event_list,
        workflow_run_id=workflow_run_id,
    )
    current_before = reduced_before.get("current")
    current_before = (
        current_before if isinstance(current_before, Mapping) else {}
    )
    incoming_generation = canonical_task_map_generation(
        task_map_generation=payload.get("task_map_generation"),
        task_map_digest=payload.get("task_map_digest"),
        task_map_ref=payload.get("task_map_ref"),
    )
    if (
        current_before
        and incoming_generation
        and str(current_before.get("task_map_generation") or "")
        == incoming_generation
    ):
        current_body = hydrate_plan_artifact_package(
            state_dir,
            {
                "ref": current_before["package_ref"],
                "sha256": current_before["package_digest"],
            },
        )
        current_ports = {
            str(port.get("logical_name") or ""): port
            for port in [*current_body["produced"], *current_body["inherited"]]
        }
        claim_port = current_ports.get("goal_claim_set") or {}
        return {
            "artifact_package_mode": mode,
            "artifact_package_status": "admitted",
            "plan_artifact_package_id": str(current_before.get("package_id") or ""),
            "plan_artifact_package_ref": str(current_before.get("package_ref") or ""),
            "plan_artifact_package_digest": str(
                current_before.get("package_digest") or ""
            ),
            "run_contract_ref": str(current_body.get("run_contract_ref") or ""),
            "run_contract_digest": str(
                current_body.get("run_contract_digest") or ""
            ),
            "goal_claim_set_ref": str(claim_port.get("ref") or ""),
            "goal_claim_set_digest": str(claim_port.get("sha256") or ""),
            "task_map_generation": incoming_generation,
            "plan_revision": str(current_body.get("plan_revision") or ""),
        }
    try:
        package, descriptor, claim = prepare_plan_artifact_package(
            state_dir=state_dir,
            project_root=project_root,
            events=event_list,
            payload=payload,
            workflow_run_id=workflow_run_id,
            flow_kind=flow_kind,
            producer_stage_id=producer_stage_id,
            goal_id=goal_id,
            metadata=metadata,
            source_event_id=source_event_id,
        )
        reduced = reduce_plan_artifact_packages(
            event_list,
            workflow_run_id=workflow_run_id,
            package_slot=str(package.get("package_slot") or PLAN_ARTIFACT_PACKAGE_SLOT),
        )
        current = reduced.get("current")
        current = current if isinstance(current, Mapping) else {}
        if (
            str(current.get("plan_revision") or "")
            == str(package.get("plan_revision") or "")
            and str(current.get("package_digest") or "")
            and str(current.get("package_digest") or "")
            != str(descriptor.get("package_digest") or "")
        ):
            raise PlanArtifactPackageError(
                "same plan revision produced a different package digest"
            )
    except Exception as exc:
        event_writer.append(ZfEvent(
            type="plan.artifact_package.rejected",
            actor="zf-cli",
            causation_id=source_event_id or None,
            correlation_id=correlation_id or workflow_run_id,
            payload={
                "status": "rejected",
                "workflow_run_id": workflow_run_id,
                "flow_kind": flow_kind,
                "package_slot": PLAN_ARTIFACT_PACKAGE_SLOT,
                "producer_stage_id": producer_stage_id,
                "plan_revision": str(payload.get("plan_revision") or ""),
                "task_map_generation": str(
                    payload.get("task_map_generation")
                    or payload.get("task_map_digest")
                    or ""
                ),
                "mode": mode,
                "reason": f"{type(exc).__name__}: {exc}",
                "source_event_id": source_event_id,
            },
        ))
        if mode == "blocking":
            raise PlanArtifactPackageError(str(exc)) from exc
        return {
            "artifact_package_mode": mode,
            "artifact_package_status": "rejected",
            "artifact_package_diagnostic": f"{type(exc).__name__}: {exc}",
        }

    event_list = list(event_list)
    current = reduce_plan_artifact_packages(
        event_list,
        workflow_run_id=workflow_run_id,
    ).get("current")
    current = current if isinstance(current, Mapping) else {}
    digest = str(descriptor.get("package_digest") or "")
    if str(current.get("package_digest") or "") != digest:
        if not any(
            _event_parts(event)[0] == "goal.claim_set.pinned"
            and str(_event_parts(event)[2].get("workflow_run_id") or "") == workflow_run_id
            and str(_event_parts(event)[2].get("task_map_generation") or "")
            == str(package.get("task_map_generation") or "")
            and str(_event_parts(event)[2].get("goal_claim_set_digest") or "")
            == str(claim.get("goal_claim_set_digest") or "")
            for event in event_list
        ):
            event_writer.append(ZfEvent(
                type="goal.claim_set.pinned",
                actor="zf-cli",
                causation_id=source_event_id or None,
                correlation_id=correlation_id or workflow_run_id,
                payload={
                    "workflow_run_id": workflow_run_id,
                    "goal_id": goal_id,
                    "task_map_generation": str(package["task_map_generation"]),
                    "task_map_ref": str(payload.get("task_map_ref") or ""),
                    **claim,
                    "source_event_id": source_event_id,
                },
            ))
        admitted_payload = package_event_payload(
            package,
            descriptor,
            status="admitted",
        )
        event_writer.append(ZfEvent(
            type="plan.artifact_package.admitted",
            actor="zf-cli",
            causation_id=source_event_id or None,
            correlation_id=correlation_id or workflow_run_id,
            payload={
                **admitted_payload,
                "mode": mode,
                "source_event_id": source_event_id,
            },
        ))
        if admitted_payload.get("supersedes_package_ref"):
            event_writer.append(ZfEvent(
                type="plan.artifact_package.superseded",
                actor="zf-cli",
                causation_id=source_event_id or None,
                correlation_id=correlation_id or workflow_run_id,
                payload={
                    "status": "superseded",
                    "workflow_run_id": workflow_run_id,
                    "package_slot": str(package["package_slot"]),
                    "package_id": str(current.get("package_id") or ""),
                    "package_ref": str(current.get("package_ref") or ""),
                    "package_digest": str(current.get("package_digest") or ""),
                    "superseded_by_package_id": str(descriptor["package_id"]),
                    "superseded_by_package_ref": str(descriptor["ref"]),
                    "superseded_by_package_digest": digest,
                },
            ))
    return {
        "artifact_package_mode": mode,
        "artifact_package_status": "admitted",
        "plan_artifact_package_id": str(descriptor.get("package_id") or ""),
        "plan_artifact_package_ref": str(descriptor.get("ref") or ""),
        "plan_artifact_package_digest": digest,
        "run_contract_ref": str(package.get("run_contract_ref") or ""),
        "run_contract_digest": str(package.get("run_contract_digest") or ""),
        "goal_claim_set_ref": str(claim.get("goal_claim_set_ref") or ""),
        "goal_claim_set_digest": str(claim.get("goal_claim_set_digest") or ""),
        "task_map_generation": str(package.get("task_map_generation") or ""),
        "plan_revision": str(package.get("plan_revision") or ""),
    }


def _materialize_explicit_ports(
    *,
    state_dir: Path,
    project_root: Path,
    payload: Mapping[str, Any],
    flow_kind: str,
) -> dict[str, dict[str, Any]]:
    ports: dict[str, dict[str, Any]] = {}
    for logical_name, keys in _PORT_REF_KEYS.items():
        ref = next(
            (str(payload.get(key) or "").strip() for key in keys if str(payload.get(key) or "").strip()),
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
            "producer_stage_id": str(payload.get("stage_id") or payload.get("producer_stage_id") or ""),
            "ref": str(source.get("ref") or ""),
            "sha256": str(source.get("sha256") or ""),
        }
    inline_ports = payload.get("plan_ports")
    synthesis_result = payload.get("plan_synthesis_result")
    if not isinstance(inline_ports, list) and isinstance(synthesis_result, Mapping):
        inline_ports = synthesis_result.get("plan_ports")
    for item in inline_ports if isinstance(inline_ports, list) else []:
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
    if flow_kind == "issue" and "issue_spec" not in ports and "requirement_spec" in ports:
        ports["issue_spec"] = {
            **ports.pop("requirement_spec"),
            "logical_name": "issue_spec",
            "source_logical_name": "requirement_spec",
            "adapter_version": "plan-artifact-port-adapter.v1",
        }
    return ports


def _validate_required_matrix_readiness(
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


def _event_parts(
    event: ZfEvent | Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    if isinstance(event, ZfEvent):
        return event.type, event.id, dict(event.payload or {})
    payload = event.get("payload")
    return (
        str(event.get("type") or ""),
        str(event.get("id") or ""),
        dict(payload) if isinstance(payload, Mapping) else {},
    )


def _strings(value: Any) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else [value] if value else []
    return list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))


_PLAN_PORT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def _normalize_required_port_names(
    values: Iterable[object] | None,
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes, Mapping)):
        raise PlanArtifactPackageError("required_plan_ports must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise PlanArtifactPackageError(
                "required_plan_ports entries must be non-empty strings"
            )
        name = canonical_plan_port_name(value)
        if not _PLAN_PORT_NAME.fullmatch(name):
            raise PlanArtifactPackageError(
                f"invalid required plan artifact port name: {name!r}"
            )
        if name in seen:
            raise PlanArtifactPackageError(
                f"duplicate required plan artifact port: {name!r}"
            )
        seen.add(name)
        normalized.append(name)
    return normalized


def _load_package_task_map(
    *,
    state_dir: Path,
    project_root: Path,
    task_map_ref: str,
) -> dict[str, Any]:
    from zf.runtime.task_map import load_task_map, resolve_artifact_file

    try:
        return load_task_map(resolve_artifact_file(
            task_map_ref,
            project_root=project_root,
            state_dir=state_dir,
        ))
    except Exception as exc:
        raise PlanArtifactPackageError(
            f"task_map required ports cannot be loaded: {exc}"
        ) from exc


__all__ = [
    "PLAN_ARTIFACT_PACKAGE_MODES",
    "PLAN_ARTIFACT_PACKAGE_SCHEMA",
    "PLAN_ARTIFACT_PACKAGE_SLOT",
    "PlanArtifactPackageError",
    "admit_plan_artifact_package_for_payload",
    "artifact_package_mode",
    "build_plan_artifact_package",
    "hydrate_plan_artifact_package",
    "package_event_payload",
    "prepare_plan_artifact_package",
    "reduce_plan_artifact_packages",
    "required_plan_ports",
    "validate_plan_artifact_package_shape",
    "write_plan_artifact_package",
]
