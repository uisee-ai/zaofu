"""Immutable current plan artifact packages for Product Flow runs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.goal_claim_set import (
    canonical_task_map_generation,
    mandatory_claim_continuity,
    pin_goal_claim_set_from_task_map,
)
from zf.runtime.plan_artifact_ports import (
    canonical_plan_port_name,
    normalize_plan_ports,
)
from zf.runtime.plan_artifact_package_ports import (
    PlanArtifactPackageError,
    materialize_explicit_plan_ports,
    validate_required_matrix_readiness,
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
    package_mode: str = "",
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
    normalized_mode = str(package_mode or "").strip().lower()
    if normalized_mode:
        if normalized_mode not in PLAN_ARTIFACT_PACKAGE_MODES:
            raise PlanArtifactPackageError(
                f"unsupported artifact package mode: {normalized_mode}"
            )
        body["artifact_package_mode"] = normalized_mode
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
    package_mode = str(package.get("artifact_package_mode") or "").strip()
    if package_mode and package_mode not in PLAN_ARTIFACT_PACKAGE_MODES:
        raise PlanArtifactPackageError(
            f"unsupported artifact package mode: {package_mode}"
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
    event_list = list(events)
    aborted_admission_sources = {
        str(payload.get("trigger_event_id") or "").strip()
        for event in event_list
        if (
            (event_type := _event_parts(event)[0]) == "fanout.cancelled"
            and isinstance((payload := _event_parts(event)[2]), Mapping)
            and str(payload.get("failure_scope") or "") == "plan_admission"
            and str(payload.get("trigger_event_id") or "").strip()
        )
    }
    current: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    revisions: dict[str, tuple[str, str, str]] = {}
    generations: dict[str, tuple[str, str]] = {}
    for event in event_list:
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
        source_event_id = str(payload.get("source_event_id") or "").strip()
        if source_event_id and source_event_id in aborted_admission_sources:
            rejected.append({
                **row,
                "status": "rejected",
                "reason": "aborted_plan_admission",
            })
            continue
        revision = str(payload.get("plan_revision") or "")
        digest = str(payload.get("package_digest") or payload.get("package_sha256") or "")
        generation = str(payload.get("task_map_generation") or "")
        existing = revisions.get(revision)
        if revision and existing:
            existing_digest, existing_generation, existing_event_id = existing
            if existing_digest == digest:
                continue
            if generation and generation == existing_generation:
                rejected.append({
                    **row,
                    "status": "rejected",
                    "reason": "immutable_generation_replay_conflict",
                    "canonical_package_digest": existing_digest,
                    "canonical_event_id": existing_event_id,
                })
                continue
            raise PlanArtifactPackageError(
                f"conflicting admitted package for plan revision {revision}"
            )
        existing_generation = generations.get(generation)
        if generation and existing_generation:
            existing_digest, existing_event_id = existing_generation
            if existing_digest == digest:
                continue
            rejected.append({
                **row,
                "status": "rejected",
                "reason": "immutable_generation_replay_conflict",
                "canonical_package_digest": existing_digest,
                "canonical_event_id": existing_event_id,
            })
            continue
        if revision:
            revisions[revision] = (digest, generation, event_id)
        if generation:
            generations[generation] = (digest, event_id)
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


def admitted_plan_artifact_package_for_generation(
    projection: Mapping[str, Any],
    *,
    task_map_generation: str,
) -> dict[str, Any]:
    """Select the immutable admitted package bound to one Task generation."""

    generation = str(task_map_generation or "").strip()
    if not generation:
        return {}
    current = projection.get("current")
    history = projection.get("history")
    candidates = [
        row
        for row in [
            current if isinstance(current, Mapping) else None,
            *(
                reversed(history)
                if isinstance(history, list)
                else []
            ),
        ]
        if isinstance(row, Mapping)
        and str(row.get("task_map_generation") or "") == generation
    ]
    if not candidates:
        return {}
    digests = {
        str(row.get("package_digest") or row.get("package_sha256") or "")
        for row in candidates
    }
    if len(digests) != 1:
        raise PlanArtifactPackageError(
            "conflicting admitted packages for task-map generation " + generation
        )
    return dict(candidates[0])


def plan_artifact_package_binding(
    events: Iterable[ZfEvent | Mapping[str, Any]],
    package: Mapping[str, Any],
) -> dict[str, str]:
    """Return one admitted package and its generation-pinned Claim Set."""

    binding = {
        "task_map_generation": str(package.get("task_map_generation") or ""),
        "plan_artifact_package_id": str(package.get("package_id") or ""),
        "plan_artifact_package_ref": str(package.get("package_ref") or ""),
        "plan_artifact_package_digest": str(package.get("package_digest") or ""),
    }
    claim_ref = str(package.get("goal_claim_set_ref") or "")
    claim_digest = str(package.get("goal_claim_set_digest") or "")
    if not claim_ref or not claim_digest:
        run_id = str(package.get("workflow_run_id") or "")
        generation = str(package.get("task_map_generation") or "")
        for event in reversed(list(events)):
            event_type, _event_id, payload = _event_parts(event)
            if event_type != "goal.claim_set.pinned":
                continue
            if str(payload.get("workflow_run_id") or "") != run_id:
                continue
            if str(payload.get("task_map_generation") or "") != generation:
                continue
            claim_ref = str(payload.get("goal_claim_set_ref") or "")
            claim_digest = str(payload.get("goal_claim_set_digest") or "")
            if claim_ref and claim_digest:
                break
    if claim_ref and claim_digest:
        binding.update({
            "goal_claim_set_ref": claim_ref,
            "goal_claim_set_digest": claim_digest,
        })
    return binding


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

    event_list = list(events)
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
    required_ports = required_plan_ports(
        flow_kind=flow_kind,
        metadata=metadata,
        declared=(
            task_map.get("required_plan_ports")
            if "required_plan_ports" in task_map
            else ()
        ),
    )
    generation = canonical_task_map_generation(
        task_map_generation=payload.get("task_map_generation"),
        task_map_digest=payload.get("task_map_digest"),
        task_map_ref=task_map_ref,
    )
    objective_ref = str(
        payload.get("requirement_spec_ref")
        or payload.get("objective_ref")
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
    explicit_ports = materialize_explicit_plan_ports(
        state_dir=state_dir,
        project_root=project_root,
        # Candidate rework events can carry only immutable artifact refs and
        # rely on this caller to identify the producing stage.  Bind that
        # identity before materializing ports so ref-based and inline ports
        # have the same producer contract.
        payload={
            **payload,
            "stage_id": str(payload.get("stage_id") or producer_stage_id),
        },
        flow_kind=flow_kind,
        required_ports=required_ports,
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
        event_list,
        workflow_run_id=workflow_run_id,
    )
    previous = reduced.get("current")
    previous = previous if isinstance(previous, Mapping) else {}
    inherited: list[dict[str, Any]] = []
    continuity = mandatory_claim_continuity(None, claim_set)
    if previous and previous.get("package_ref") and previous.get("package_digest"):
        prior_body = hydrate_plan_artifact_package(
            state_dir,
            {
                "ref": previous["package_ref"],
                "sha256": previous["package_digest"],
            },
        )
        prior_claim_port = next(
            (
                port
                for port in [*prior_body["produced"], *prior_body["inherited"]]
                if str(port.get("logical_name") or "") == "goal_claim_set"
            ),
            None,
        )
        previous_claim_set: Mapping[str, Any] | None = None
        if isinstance(prior_claim_port, Mapping):
            hydrated_claim = hydrate_sidecar_ref(
                state_dir,
                {
                    "ref": str(prior_claim_port.get("ref") or ""),
                    "sha256": str(prior_claim_port.get("sha256") or ""),
                },
            )
            if isinstance(hydrated_claim.payload, Mapping):
                previous_claim_set = hydrated_claim.payload
        continuity = mandatory_claim_continuity(
            previous_claim_set,
            claim_set,
            claim_waivers=_goal_claim_waivers(payload),
            active_waiver_refs=_active_goal_claim_waiver_refs(
                event_list,
                goal_id=goal_id,
            ),
        )
        if continuity["status"] != "passed":
            rejected_ids = ", ".join(
                str(item.get("goal_claim_id") or "")
                for item in continuity["findings"]
            )
            raise PlanArtifactPackageError(
                "mandatory goal claim continuity failed: " + rejected_ids
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
    plan_revision = str(
        payload.get("plan_revision")
        or payload.get("revision")
        or generation
    )
    validate_required_matrix_readiness(
        state_dir=state_dir,
        ports=[*explicit_ports.values(), *inherited],
        required_ports=required_ports,
    )
    from zf.runtime.plan_acceptance_rework import rebind_inherited_gap_acceptance

    rebind_inherited_gap_acceptance(
        state_dir=state_dir, project_root=project_root, payload=payload,
        workflow_run_id=workflow_run_id, flow_kind=flow_kind,
        explicit_ports=explicit_ports, inherited=inherited,
    )
    product_acceptance_port = next(
        (
            port
            for port in [*explicit_ports.values(), *inherited]
            if str(port.get("logical_name") or "")
            == "product_acceptance_spec"
        ),
        None,
    )
    if isinstance(product_acceptance_port, Mapping):
        from zf.runtime.product_acceptance import (
            PRODUCT_ACCEPTANCE_SPEC_SCHEMA,
            validate_product_acceptance_spec,
        )

        hydrated = hydrate_sidecar_ref(
            state_dir,
            {
                "ref": str(product_acceptance_port.get("ref") or ""),
                "sha256": str(product_acceptance_port.get("sha256") or ""),
            },
        )
        if not isinstance(hydrated.payload, Mapping):
            raise PlanArtifactPackageError(
                "product_acceptance_spec must contain a JSON object"
            )
        product_acceptance_spec = dict(hydrated.payload)
        if "product_acceptance_spec" in explicit_ports:
            product_acceptance_spec.update({
                "schema_version": PRODUCT_ACCEPTANCE_SPEC_SCHEMA,
                "workflow_run_id": workflow_run_id,
                "flow_kind": flow_kind,
                "plan_revision": plan_revision,
                "task_map_generation": generation,
            })
            descriptor = write_immutable_json_sidecar(
                state_dir,
                product_acceptance_spec,
                root="product-acceptance/specs",
                kind="product_acceptance_spec",
                schema_version=PRODUCT_ACCEPTANCE_SPEC_SCHEMA,
                created_by="plan-artifact-package",
                source_event_id=source_event_id,
            )
            explicit_ports["product_acceptance_spec"] = {
                **explicit_ports["product_acceptance_spec"],
                "schema_version": PRODUCT_ACCEPTANCE_SPEC_SCHEMA,
                "ref": str(descriptor.get("ref") or ""),
                "sha256": str(descriptor.get("sha256") or ""),
            }
        validate_product_acceptance_spec(
            product_acceptance_spec,
            expected={
                "workflow_run_id": workflow_run_id,
                "flow_kind": flow_kind,
                "plan_revision": plan_revision,
                "task_map_generation": generation,
            },
        )
    package = build_plan_artifact_package(
        workflow_run_id=workflow_run_id,
        request_id=str(payload.get("request_id") or ""),
        flow_kind=flow_kind,
        producer_stage_id=producer_stage_id,
        run_contract=run_contract,
        plan_revision=plan_revision,
        task_map_generation=generation,
        produced=explicit_ports.values(),
        inherited=inherited,
        required_ports=required_ports,
        package_mode=mode,
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
        "goal_claim_continuity": continuity,
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
        "artifact_package_mode": str(
            package.get("artifact_package_mode") or ""
        ),
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
    admitted_rows = [
        row
        for row in [
            *(reduced_before.get("history") or []),
            current_before,
        ]
        if isinstance(row, Mapping)
    ]
    reusable = next(
        (
            row
            for row in admitted_rows
            if incoming_generation
            and str(row.get("task_map_generation") or "")
            == incoming_generation
        ),
        None,
    )
    if reusable:
        current_body = hydrate_plan_artifact_package(
            state_dir,
            {
                "ref": reusable["package_ref"],
                "sha256": reusable["package_digest"],
            },
        )
        current_mode = str(
            current_body.get("artifact_package_mode") or "shadow"
        )
        current_ports = {
            str(port.get("logical_name") or ""): port
            for port in [*current_body["produced"], *current_body["inherited"]]
        }
        claim_port = current_ports.get("goal_claim_set") or {}
        return {
            "artifact_package_mode": current_mode,
            "artifact_package_status": "admitted",
            "plan_artifact_package_id": str(reusable.get("package_id") or ""),
            "plan_artifact_package_ref": str(reusable.get("package_ref") or ""),
            "plan_artifact_package_digest": str(
                reusable.get("package_digest") or ""
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
        admitted_payload.update({
            "goal_claim_set_ref": str(claim.get("goal_claim_set_ref") or ""),
            "goal_claim_set_digest": str(
                claim.get("goal_claim_set_digest") or ""
            ),
        })
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


def _goal_claim_waivers(payload: Mapping[str, Any]) -> dict[str, str]:
    raw = payload.get("goal_claim_waivers")
    rows = raw if isinstance(raw, list) else []
    return {
        str(item.get("goal_claim_id") or "").strip(): str(
            item.get("waiver_ref") or ""
        ).strip()
        for item in rows
        if isinstance(item, Mapping)
        and str(item.get("goal_claim_id") or "").strip()
        and str(item.get("waiver_ref") or "").strip()
    }


def _active_goal_claim_waiver_refs(
    events: list[ZfEvent | Mapping[str, Any]],
    *,
    goal_id: str,
) -> set[str]:
    from zf.runtime.waivers import active_waivers

    active = active_waivers(
        [event for event in events if isinstance(event, ZfEvent)],
        goal_id,
    )
    return {
        str(value)
        for waiver in active
        for value in (waiver.get("signature"), waiver.get("event_id"))
        if str(value).strip()
    }


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
    "admitted_plan_artifact_package_for_generation",
    "admit_plan_artifact_package_for_payload",
    "artifact_package_mode",
    "build_plan_artifact_package",
    "hydrate_plan_artifact_package",
    "package_event_payload",
    "plan_artifact_package_binding",
    "prepare_plan_artifact_package",
    "reduce_plan_artifact_packages",
    "required_plan_ports",
    "validate_plan_artifact_package_shape",
    "write_plan_artifact_package",
]
