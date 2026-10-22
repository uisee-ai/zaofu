"""Mechanical checks for a planner candidate before Plan Critic dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.plan_execution_review import plan_candidate_writer_policy, review_execution

from zf.runtime.plan_artifact_package import (
    hydrate_plan_artifact_package,
    required_plan_ports,
)
from zf.runtime.plan_artifact_ports import (
    canonical_plan_port_name,
    coerce_plan_port_descriptors,
)
from zf.runtime.plan_matrix_admission import plan_matrix_admission_errors
from zf.runtime.task_map import (
    load_task_map,
    resolve_artifact_file,
    validate_source_index_payload,
    validate_task_map_payload,
)
from zf.runtime.writer_fanout_admission import (
    validate_writer_task_items,
    writer_task_map_policy_errors,
    writer_task_items,
)


SCHEMA_VERSION = "plan-candidate-preflight.v1"
_KERNEL_DERIVED_PORTS = frozenset({"goal_claim_set", "planning_result"})


def evaluate_plan_candidate_preflight(
    *,
    state_dir: Path,
    project_root: Path,
    reports: list[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    writer_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return structured, deterministic readiness facts for one plan candidate."""

    trigger = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), Mapping)
        else {}
    )
    candidates = [
        dict(row.get("report") or {})
        for row in reports
        if isinstance(row, Mapping) and isinstance(row.get("report"), Mapping)
    ]
    errors: list[dict[str, str]] = []
    port_bodies = _plan_port_bodies(
        candidates,
        trigger=trigger,
        state_dir=state_dir,
        project_root=project_root,
        errors=errors,
    )
    task_map, producer = _task_map_candidate(
        candidates,
        state_dir=state_dir,
        project_root=project_root,
        port_bodies=port_bodies,
        errors=errors,
    )
    flow_kind = str(
        trigger.get("flow_kind")
        or (metadata or {}).get("flow_kind")
        or ""
    ).strip().lower()
    required = required_plan_ports(
        flow_kind=flow_kind,
        metadata=metadata,
        declared=(
            task_map.get("required_plan_ports", [])
            if isinstance(task_map, Mapping)
            else []
        ),
    )
    available = _available_ports(candidates, trigger, has_task_map=bool(task_map))
    required_with_source = list(dict.fromkeys([*required, "source_index"]))
    for name in required_with_source:
        canonical = canonical_plan_port_name(name)
        if canonical in _KERNEL_DERIVED_PORTS:
            continue
        if canonical not in available:
            _error(
                errors,
                "required_plan_port_missing",
                f"plan_ports.{canonical}",
                f"required plan artifact port {canonical!r} has no producer",
            )

    if task_map:
        from zf.runtime.task_scoped_plan_inputs import task_input_paths

        for name, body in port_bodies.items():
            raw_tasks = task_map.get("tasks")
            for task in raw_tasks if isinstance(raw_tasks, list) else []:
                if not isinstance(task, Mapping):
                    continue
                try:
                    task_input_paths(body, task_id=str(task.get("task_id") or task.get("id") or ""))
                except ValueError as exc:
                    _error(errors, "task_scoped_input_invalid", f"plan_ports.{name}", str(exc))
                    break
        prior_task_map = _canonical_prior_task_map(
            trigger=trigger,
            state_dir=state_dir,
            project_root=project_root,
            errors=errors,
        )
        if prior_task_map:
            errors.extend(_mandatory_goal_claim_contract_errors(
                previous=prior_task_map,
                current=task_map,
            ))
        validation = validate_task_map_payload(
            dict(task_map),
            require_task_verification=True,
        )
        for message in validation.errors:
            _error(errors, "task_map_invalid", "task_map", message)
        for diagnostic in validation.summary.get("goal_coverage", {}).get(
            "diagnostics", []
        ):
            _error(
                errors,
                str(diagnostic.get("code") or "goal_claim_uncovered"),
                "task_map.goal_claims",
                str(diagnostic.get("goal_claim_id") or "mandatory claim is uncovered"),
            )
        errors.extend(_claim_acceptance_command_errors(task_map))
        errors.extend(_rolling_smoke_command_errors(task_map, metadata=metadata))
        if validation.passed:
            task_items: list[dict[str, Any]] = []
            try:
                task_items = writer_task_items(dict(task_map))
                validate_writer_task_items(task_items)
            except (RuntimeError, ValueError) as exc:
                _error(
                    errors,
                    "writer_fanout_task_map_invalid",
                    "task_map.tasks",
                    str(exc),
                )
            if task_items:
                policy = writer_policy or {}
                for message in writer_task_map_policy_errors(
                    task_items,
                    candidate_quality_source=str(
                        policy.get("candidate_quality_source") or "auto"
                    ),
                    work_units_config=policy.get("work_units"),
                ):
                    _error(
                        errors,
                        "writer_fanout_task_map_policy_failed",
                        "task_map.tasks",
                        message,
                    )

    source_index = _source_index_candidate(
        candidates,
        state_dir=state_dir,
        project_root=project_root,
        port_bodies=port_bodies,
        errors=errors,
    )
    if source_index and task_map:
        source_validation = validate_source_index_payload(
            source_index,
            task_map=dict(task_map),
            require_canonical=True,
        )
        for message in source_validation.errors:
            _error(errors, "source_index_invalid", "source_index", message)

    if task_map:
        errors.extend(plan_matrix_admission_errors(
            task_map=task_map,
            source_index=source_index,
            ports=port_bodies,
            required_ports=required_with_source,
        ))

    product_acceptance = port_bodies.get("product_acceptance_spec")
    if isinstance(product_acceptance, Mapping):
        errors.extend(_product_acceptance_spec_errors(
            product_acceptance,
            task_map=task_map,
            manifest=manifest,
            flow_kind=flow_kind,
        ))

    if not _has_plan_markdown(
        candidates,
        state_dir=state_dir,
        project_root=project_root,
    ):
        _error(
            errors,
            "plan_markdown_producer_missing",
            "plan_artifact_ref",
            "human-readable plan Markdown has no resolvable producer",
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "execution_review": review_execution(
            task_map or {}, ports=port_bodies,
            policy=(writer_policy or {}).get("execution", {}), metadata=metadata or {},
        ),
        "status": "passed" if not errors else "failed",
        "stage_id": str(manifest.get("stage_id") or ""),
        "fanout_id": str(manifest.get("fanout_id") or ""),
        "flow_kind": flow_kind,
        "task_map_producer": producer,
        "required_ports": required_with_source,
        "available_ports": sorted(available),
        "errors": errors,
        "summary": {
            "error_count": len(errors),
            "task_count": len(task_map.get("tasks", [])) if task_map else 0,
        },
    }


def _product_acceptance_spec_errors(
    raw_spec: Mapping[str, Any],
    *,
    task_map: Mapping[str, Any],
    manifest: Mapping[str, Any],
    flow_kind: str,
) -> list[dict[str, str]]:
    """Run the same structural Product Acceptance checks before submit.

    Package admission pins the final run/revision/generation identity.  The
    producer preflight mirrors that normalization so this check focuses on the
    complete semantic body instead of requiring the producer to predict a
    projected task-map ref.
    """

    from zf.runtime.product_acceptance import (
        PRODUCT_ACCEPTANCE_SPEC_SCHEMA,
        ProductAcceptanceError,
        validate_product_acceptance_spec,
    )

    trigger = (
        manifest.get("trigger_payload")
        if isinstance(manifest.get("trigger_payload"), Mapping)
        else {}
    )
    metadata = (
        task_map.get("metadata")
        if isinstance(task_map.get("metadata"), Mapping)
        else {}
    )
    workflow_run_id = str(
        manifest.get("workflow_run_id")
        or manifest.get("trace_id")
        or trigger.get("workflow_run_id")
        or trigger.get("run_id")
        or manifest.get("fanout_id")
        or "plan-preflight"
    )
    plan_revision = str(
        task_map.get("plan_revision")
        or metadata.get("plan_revision")
        or trigger.get("plan_revision")
        or "plan-preflight"
    )
    generation = str(
        task_map.get("task_map_generation")
        or metadata.get("task_map_generation")
        or trigger.get("task_map_generation")
        or "task-map-preflight"
    )
    normalized = {
        **dict(raw_spec),
        "schema_version": PRODUCT_ACCEPTANCE_SPEC_SCHEMA,
        "workflow_run_id": workflow_run_id,
        "flow_kind": flow_kind,
        "plan_revision": plan_revision,
        "task_map_generation": generation,
    }
    try:
        validate_product_acceptance_spec(
            normalized,
            expected={
                "workflow_run_id": workflow_run_id,
                "flow_kind": flow_kind,
                "plan_revision": plan_revision,
                "task_map_generation": generation,
            },
        )
    except ProductAcceptanceError as exc:
        rows: list[dict[str, str]] = []
        _error(
            rows,
            "product_acceptance_spec_invalid",
            "plan_ports.product_acceptance_spec",
            str(exc),
        )
        return rows
    return []


def _canonical_prior_task_map(
    *,
    trigger: Mapping[str, Any],
    state_dir: Path,
    project_root: Path,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """Load the canonical predecessor bound to a replan, when one exists."""

    package_ref = str(trigger.get("plan_artifact_package_ref") or "").strip()
    package_digest = str(
        trigger.get("plan_artifact_package_digest") or ""
    ).strip()
    if package_ref or package_digest:
        if not package_ref or not package_digest:
            _error(
                errors,
                "canonical_plan_package_binding_incomplete",
                "trigger_payload.plan_artifact_package_ref",
                "canonical Plan Package requires both ref and digest",
            )
            return {}
        try:
            package = hydrate_plan_artifact_package(
                state_dir,
                {"ref": package_ref, "sha256": package_digest},
            )
            descriptor = next(
                (
                    dict(port)
                    for port in [
                        *package.get("produced", []),
                        *package.get("inherited", []),
                    ]
                    if isinstance(port, Mapping)
                    and str(port.get("logical_name") or "") == "task_map"
                ),
                None,
            )
            if descriptor is None:
                raise ValueError("canonical Plan Package has no task_map port")
            return _load_bound_task_map(
                descriptor,
                state_dir=state_dir,
                project_root=project_root,
            )
        except Exception as exc:
            _error(
                errors,
                "canonical_plan_package_unreadable",
                "trigger_payload.plan_artifact_package_ref",
                str(exc),
            )
            return {}

    descriptors = trigger.get("previous_plan_candidate_refs")
    descriptors = descriptors if isinstance(descriptors, list) else []
    for descriptor in reversed(descriptors):
        if not isinstance(descriptor, Mapping):
            continue
        if str(descriptor.get("kind") or "") != "plan_candidate_task_map":
            continue
        try:
            return _load_bound_task_map(
                descriptor,
                state_dir=state_dir,
                project_root=project_root,
            )
        except Exception as exc:
            _error(
                errors,
                "previous_plan_task_map_unreadable",
                "trigger_payload.previous_plan_candidate_refs",
                str(exc),
            )
            return {}
    return {}


def _load_bound_task_map(
    descriptor: Mapping[str, Any],
    *,
    state_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    ref = str(descriptor.get("ref") or "").strip()
    expected = str(
        descriptor.get("sha256") or descriptor.get("digest") or ""
    ).strip()
    if not ref or not expected:
        raise ValueError("task-map descriptor requires ref and sha256")
    path = resolve_artifact_file(
        ref,
        project_root=project_root,
        state_dir=state_dir,
    )
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError(
            f"task-map digest mismatch for {ref!r}: "
            f"expected {expected}, got {actual}"
        )
    return load_task_map(path)


def _mandatory_goal_claim_contract_errors(
    *,
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Reject silent weakening of an inherited mandatory Goal contract."""

    previous_claims = _goal_claims_by_id(previous, mandatory_only=True)
    current_claims = _goal_claims_by_id(current, mandatory_only=False)
    errors: list[dict[str, str]] = []
    for claim_id, before in previous_claims.items():
        after = current_claims.get(claim_id)
        if after is None:
            _error(
                errors,
                "mandatory_goal_claim_contract_removed",
                f"task_map.goal_claims[{claim_id}]",
                "canonical mandatory Goal claim is missing from the replan",
            )
            continue
        before_contract = _goal_claim_contract(before)
        after_contract = _goal_claim_contract(after)
        changed = [
            field
            for field, expected in before_contract.items()
            if after_contract.get(field) != expected
        ]
        if changed:
            _error(
                errors,
                "mandatory_goal_claim_contract_rewritten",
                f"task_map.goal_claims[{claim_id}]",
                "canonical mandatory Goal claim changed fields: "
                + ", ".join(changed),
            )
    return errors


def _goal_claims_by_id(
    task_map: Mapping[str, Any],
    *,
    mandatory_only: bool,
) -> dict[str, Mapping[str, Any]]:
    claims: dict[str, Mapping[str, Any]] = {}
    for item in task_map.get("goal_claims", []):
        if not isinstance(item, Mapping):
            continue
        claim_id = str(item.get("goal_claim_id") or item.get("id") or "").strip()
        if not claim_id or (mandatory_only and not bool(item.get("mandatory", True))):
            continue
        claims[claim_id] = item
    return claims


def _goal_claim_contract(claim: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "statement": str(
            claim.get("statement")
            or claim.get("text")
            or claim.get("claim")
            or ""
        ).strip(),
        "mandatory": bool(claim.get("mandatory", True)),
        "acceptance_ids": sorted({
            str(value).strip()
            for value in claim.get("acceptance_ids", [])
            if str(value).strip()
        }),
        "verification_command_ids": sorted({
            str(value).strip()
            for value in claim.get("verification_command_ids", [])
            if str(value).strip()
        }),
        "verification_owner": str(
            claim.get("verification_owner") or ""
        ).strip(),
        "verification_tier": str(
            claim.get("verification_tier") or ""
        ).strip(),
    }


def _task_map_candidate(
    candidates: list[dict[str, Any]],
    *,
    state_dir: Path,
    project_root: Path,
    port_bodies: Mapping[str, Mapping[str, Any]],
    errors: list[dict[str, str]],
) -> tuple[dict[str, Any], str]:
    found: list[tuple[dict[str, Any], str]] = []
    for index, candidate in enumerate(candidates):
        inline = candidate.get("task_map")
        if isinstance(inline, Mapping) and inline:
            found.append((dict(inline), f"report[{index}].task_map"))
        ref = str(candidate.get("task_map_ref") or "").strip()
        if not ref:
            continue
        try:
            found.append((load_task_map(resolve_artifact_file(
                ref,
                project_root=project_root,
                state_dir=state_dir,
            )), f"report[{index}].task_map_ref"))
        except Exception as exc:
            _error(errors, "task_map_unreadable", f"report[{index}].task_map_ref", str(exc))
    port_body = port_bodies.get("task_map")
    if isinstance(port_body, Mapping) and port_body:
        found.append((dict(port_body), "plan_ports.task_map"))
    if not found:
        _error(
            errors,
            "task_map_producer_missing",
            "task_map",
            "machine-readable task map has no resolvable producer",
        )
        return {}, ""
    producer_digests = [
        (
            label,
            hashlib.sha256(json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest(),
        )
        for body, label in found
    ]
    if len({digest for _, digest in producer_digests}) > 1:
        _error(
            errors,
            "task_map_producer_mismatch",
            "task_map",
            "task map producers disagree: " + ", ".join(
                f"{label}={digest}"
                for label, digest in producer_digests
            ),
        )
    return found[0]


def _available_ports(
    candidates: list[dict[str, Any]],
    trigger: Mapping[str, Any],
    *,
    has_task_map: bool,
) -> set[str]:
    available = set(_KERNEL_DERIVED_PORTS)
    if has_task_map:
        available.add("task_map")
    sources = [dict(trigger), *candidates]
    for source in sources:
        for port in coerce_plan_port_descriptors(source.get("plan_ports")):
            name = canonical_plan_port_name(str(
                port.get("logical_name")
                or port.get("artifact_kind")
                or port.get("kind")
                or ""
            ))
            if name and (isinstance(port.get("body"), Mapping) or (
                str(port.get("ref") or "").strip()
                and str(port.get("sha256") or port.get("digest") or "").strip()
            )):
                available.add(name)
        for name, keys in {
            "requirement_spec": ("requirement_spec_ref", "prd_ref", "objective_ref"),
            "issue_spec": ("issue_spec_ref", "issue_ref", "requirement_spec_ref"),
            "source_index": ("source_index_ref",),
            "accepted_plan": ("accepted_plan_ref",),
            "plan_critique": ("plan_critique_ref", "critic_ref"),
            "project_adapter": ("project_adapter_ref",),
            "source_inventory": ("source_inventory_ref",),
            "capability_matrix": ("capability_matrix_ref",),
            "acceptance_matrix": ("acceptance_matrix_ref",),
            "test_matrix": ("test_matrix_ref", "regression_test_matrix_ref"),
            "real_e2e_matrix": ("real_e2e_matrix_ref",),
        }.items():
            if (
                isinstance(source.get(name), Mapping)
                and bool(source.get(name))
            ) or any(str(source.get(key) or "").strip() for key in keys):
                available.add(name)
    return available


def _source_index_candidate(
    candidates: list[dict[str, Any]],
    *,
    state_dir: Path,
    project_root: Path,
    port_bodies: Mapping[str, Mapping[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    for index, candidate in enumerate(candidates):
        inline = candidate.get("source_index")
        if isinstance(inline, Mapping) and inline:
            return dict(inline)
        for port in coerce_plan_port_descriptors(candidate.get("plan_ports")):
            if canonical_plan_port_name(str(port.get("logical_name") or "")) != "source_index":
                continue
            body = port.get("body")
            if isinstance(body, Mapping):
                return dict(body)
        ref = str(candidate.get("source_index_ref") or "").strip()
        if not ref:
            continue
        try:
            path = resolve_artifact_file(
                ref,
                project_root=project_root,
                state_dir=state_dir,
            )
            body = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(body, dict):
                return body
            raise ValueError("source index must contain a JSON object")
        except Exception as exc:
            _error(errors, "source_index_unreadable", f"report[{index}].source_index_ref", str(exc))
            return {}
    body = port_bodies.get("source_index")
    if isinstance(body, Mapping):
        return dict(body)
    return {}


def _plan_port_bodies(
    candidates: list[dict[str, Any]],
    *,
    trigger: Mapping[str, Any],
    state_dir: Path,
    project_root: Path,
    errors: list[dict[str, str]],
) -> dict[str, Mapping[str, Any]]:
    selected: dict[str, tuple[dict[str, Any], str]] = {}
    sources = [(dict(trigger), "trigger_payload"), *(
        (candidate, f"report[{index}]")
        for index, candidate in enumerate(candidates)
    )]
    direct_refs = {
        "source_inventory": ("source_inventory_ref",),
        "capability_matrix": ("capability_matrix_ref",),
        "acceptance_matrix": ("acceptance_matrix_ref",),
        "test_matrix": ("test_matrix_ref", "regression_test_matrix_ref"),
        "real_e2e_matrix": ("real_e2e_matrix_ref",),
        "source_index": ("source_index_ref",),
    }
    for source, label in sources:
        for index, descriptor in enumerate(
            coerce_plan_port_descriptors(source.get("plan_ports"))
        ):
            name = canonical_plan_port_name(str(
                descriptor.get("logical_name")
                or descriptor.get("artifact_kind")
                or descriptor.get("kind")
                or ""
            ))
            if name:
                selected[name] = (descriptor, f"{label}.plan_ports[{index}]")
        for name, keys in direct_refs.items():
            if name in selected and label == "trigger_payload":
                continue
            inline = source.get(name)
            if isinstance(inline, Mapping) and inline:
                selected[name] = (
                    {"logical_name": name, "body": dict(inline)},
                    f"{label}.{name}",
                )
                continue
            ref = next(
                (str(source.get(key) or "").strip() for key in keys if str(source.get(key) or "").strip()),
                "",
            )
            if ref and not any(
                canonical_plan_port_name(str(item.get("logical_name") or "")) == name
                for item in coerce_plan_port_descriptors(source.get("plan_ports"))
            ):
                selected[name] = ({"logical_name": name, "ref": ref}, f"{label}.{keys[0]}")

    bodies: dict[str, Mapping[str, Any]] = {}
    for name, (descriptor, field) in selected.items():
        body = descriptor.get("body")
        if isinstance(body, Mapping):
            bodies[name] = dict(body)
            continue
        ref = str(descriptor.get("ref") or "").strip()
        if not ref:
            continue
        try:
            path = resolve_artifact_file(
                ref,
                project_root=project_root,
                state_dir=state_dir,
            )
            raw = path.read_bytes()
            expected = str(
                descriptor.get("sha256") or descriptor.get("digest") or ""
            ).strip()
            actual = hashlib.sha256(raw).hexdigest()
            if expected and expected != actual:
                raise ValueError(
                    f"sha256 mismatch: expected {expected}, got {actual}"
                )
            value = json.loads(raw)
            if not isinstance(value, Mapping):
                raise ValueError("plan port must contain a JSON object")
            bodies[name] = dict(value)
        except Exception as exc:
            _error(errors, "plan_port_unreadable", field, str(exc))
    return bodies


def _has_plan_markdown(
    candidates: list[dict[str, Any]],
    *,
    state_dir: Path,
    project_root: Path,
) -> bool:
    for candidate in candidates:
        if str(candidate.get("plan_md") or candidate.get("refactor_plan_md") or "").strip():
            return True
        ref = str(candidate.get("plan_artifact_ref") or candidate.get("plan_ref") or "").strip()
        if not ref:
            continue
        try:
            path = resolve_artifact_file(
                ref,
                project_root=project_root,
                state_dir=state_dir,
            )
            if path.suffix.lower() == ".md" and path.read_text(encoding="utf-8").strip():
                return True
        except Exception:
            continue
    return False


def _claim_acceptance_command_errors(task_map: Mapping[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    claims = {
        str(item.get("goal_claim_id") or item.get("id") or "").strip()
        for item in task_map.get("goal_claims", [])
        if isinstance(item, Mapping) and bool(item.get("mandatory", True))
    }
    command_ids = {
        str(row.get("id") or row.get("command_id") or "").strip(): row
        for task in task_map.get("tasks", [])
        if isinstance(task, Mapping)
        for row in _command_rows(task)
        if str(row.get("id") or row.get("command_id") or "").strip()
    }
    for task in task_map.get("tasks", []):
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("task_id") or task.get("id") or "<unknown>")
        task_claims = {
            str(value).strip() for value in task.get("goal_claim_ids", [])
            if str(value).strip()
        }
        criteria = task.get("acceptance_criteria")
        criteria = criteria if isinstance(criteria, list) else []
        if (not claims or task_claims & claims) and not criteria:
            _error(errors, "claim_acceptance_missing", task_id, "claim-owning task has no acceptance criteria")
            continue
        commands = _command_rows(task)
        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, Mapping) or not bool(criterion.get("mandatory", True)):
                continue
            path = f"{task_id}.acceptance_criteria[{index}]"
            for field in ("id", "verification_owner", "verification_tier"):
                if not str(criterion.get(field) or "").strip():
                    _error(errors, f"acceptance_{field}_missing", f"{path}.{field}", f"mandatory acceptance criterion requires {field}")
            refs = [
                str(value).strip()
                for value in criterion.get("verification_command_ids", [])
                if str(value).strip()
            ]
            immutable_baseline = (
                str(criterion.get("evidence_mode") or "")
                == "immutable_baseline_only"
            )
            if immutable_baseline:
                if refs:
                    _error(
                        errors,
                        "immutable_baseline_command_refs_present",
                        f"{path}.verification_command_ids",
                        "immutable E2E baseline must be command-free; keep downstream "
                        "runtime audits outside this criterion's command ids",
                    )
                if not _is_pinned_immutable_baseline(criterion):
                    _error(
                        errors,
                        "immutable_baseline_evidence_unpinned",
                        f"{path}.evidence_refs",
                        "immutable E2E baseline requires tier e2e, a pinned git commit, "
                        "and retained artifact evidence",
                    )
            elif not refs:
                _error(errors, "acceptance_command_missing", f"{path}.verification_command_ids", "mandatory acceptance criterion requires command ids")
            for command_id in refs:
                if command_id not in command_ids:
                    _error(errors, "acceptance_command_unknown", f"{path}.verification_command_ids", f"unknown verification command {command_id!r}")
        for command in commands:
            command_id = str(
                command.get("id") or command.get("command_id") or ""
            ).strip()
            if not command_id:
                continue
            for field in ("owner", "tier"):
                if not str(command.get(field) or "").strip():
                    _error(errors, f"command_{field}_missing", f"{task_id}.validation.commands[{command_id}].{field}", f"verification command requires {field}")
    return errors


def _is_pinned_immutable_baseline(criterion: Mapping[str, Any]) -> bool:
    """Allow command-free E2E claims only when immutable evidence is pinned."""

    if (
        str(criterion.get("evidence_mode") or "")
        != "immutable_baseline_only"
        or str(criterion.get("verification_tier") or "") != "e2e"
    ):
        return False
    evidence_refs = {
        str(value).strip()
        for value in criterion.get("evidence_refs", [])
        if str(value).strip()
    }
    pinned_commits = [
        value.removeprefix("git:")
        for value in evidence_refs
        if value.startswith("git:")
    ]
    has_pinned_commit = any(
        len(value) in {40, 64}
        and all(character in "0123456789abcdef" for character in value.lower())
        for value in pinned_commits
    )
    has_artifact_evidence = any(
        value.startswith("artifacts/") for value in evidence_refs
    )
    return has_pinned_commit and has_artifact_evidence


def _command_rows(task: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    validation = task.get("validation")
    values = [
        validation.get("commands") if isinstance(validation, Mapping) else None,
        task.get("verification_commands"),
        task.get("verify_commands"),
    ]
    return [
        row
        for value in values
        if isinstance(value, list)
        for row in value
        if isinstance(row, Mapping)
    ]


def _rolling_smoke_command_errors(
    task_map: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    task_pipeline = (
        metadata.get("task_pipeline")
        if isinstance(metadata, Mapping)
        and isinstance(metadata.get("task_pipeline"), Mapping)
        else {}
    )
    candidate = (
        task_pipeline.get("candidate")
        if isinstance(task_pipeline.get("candidate"), Mapping)
        else {}
    )
    if str(candidate.get("rolling_smoke") or "") != "required":
        return []

    errors: list[dict[str, str]] = []
    for task in task_map.get("tasks", []):
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("task_id") or task.get("id") or "<unknown>")
        marked = [
            command
            for command in _command_rows(task)
            if command.get("rolling_smoke") is True
        ]
        if not marked:
            _error(
                errors,
                "rolling_smoke_command_missing",
                f"{task_id}.validation.commands",
                "v4 Task Pipeline requires an explicitly marked rolling-smoke command",
            )
            continue
        for command in marked:
            command_id = str(
                command.get("id")
                or command.get("command_id")
                or "<unknown>"
            )
            field = f"{task_id}.validation.commands[{command_id}]"
            if command.get("deterministic") is not True:
                _error(
                    errors,
                    "rolling_smoke_not_deterministic",
                    f"{field}.deterministic",
                    "rolling-smoke command must be deterministic",
                )
            if command.get("reusable") is not True:
                _error(
                    errors,
                    "rolling_smoke_not_reusable",
                    f"{field}.reusable",
                    "rolling-smoke command must be reusable",
                )
            tier = str(command.get("tier") or "")
            if tier not in {"static", "runtime"}:
                _error(
                    errors,
                    "rolling_smoke_tier_invalid",
                    f"{field}.tier",
                    "rolling-smoke command tier must be static or runtime",
                )
    return errors


def rolling_smoke_command_errors(
    task_map: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Expose the candidate rolling-smoke policy for revised task maps."""

    return _rolling_smoke_command_errors(task_map, metadata=metadata)


def _error(errors: list[dict[str, str]], code: str, field: str, message: str) -> None:
    errors.append({"code": code, "field": field, "message": message})


__all__ = [
    "SCHEMA_VERSION",
    "evaluate_plan_candidate_preflight",
    "rolling_smoke_command_errors",
]
