"""Mechanical checks for a planner candidate before Plan Critic dispatch."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.runtime.plan_execution_review import plan_candidate_writer_policy, review_execution
from zf.runtime.execution_usage_facts import execution_usage_facts

from zf.runtime.plan_artifact_package import (
    hydrate_plan_artifact_package,
    required_plan_ports,
)
from zf.runtime.plan_artifact_ports import (
    canonical_plan_port_name,
    coerce_plan_port_descriptors,
)
from zf.runtime.plan_matrix_admission import plan_matrix_admission_errors
from zf.runtime.call_result_envelope import canonical_json_bytes
from zf.runtime.plan_candidate_ports import (
    PlanPortTooLarge, check_plan_port_size, read_plan_port_bytes,
    plan_port_bodies as _plan_port_bodies,
    prepared_plan_port_path_errors,
)
from zf.runtime.task_map import (
    resolve_artifact_file,
    validate_source_index_payload,
    validate_task_map_payload,
)
from zf.runtime.writer_fanout_admission import (
    select_writer_task_items,
    validate_writer_task_items,
    writer_fanout_dispatch_base_commit,
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
    review_input_limits: Mapping[str, Any] | None = None,
    ports_materialized: bool = False,
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
    from zf.runtime.plan_candidate_owner_review import owner_review_preflight

    draft_review = owner_review_preflight(
        candidates, state_dir=state_dir, project_root=project_root,
    )
    if draft_review is not None:
        return draft_review
    errors: list[dict[str, str]] = []
    if ports_materialized:
        errors.extend(prepared_plan_port_path_errors(candidates, state_dir=state_dir))
    from zf.runtime.workflow_read_result import workflow_read_finding_issues

    for index, candidate in enumerate(candidates):
        errors.extend(workflow_read_finding_issues(
            candidate.get("findings", []), field=f"report[{index}].findings",
        ))
    port_bodies = _plan_port_bodies(
        candidates,
        trigger=trigger,
        state_dir=state_dir,
        project_root=project_root,
        errors=errors,
        ports_materialized=ports_materialized,
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
        from zf.runtime.plan_candidate_input_lineage import validate_input_body_partitions

        raw_tasks = task_map.get("tasks")
        tasks = [task for task in raw_tasks if isinstance(task, Mapping)] if isinstance(raw_tasks, list) else []
        selected_ids = None
        try:
            selected, _, _, _ = select_writer_task_items(
                dict(task_map), writer_task_items(dict(task_map)), trigger,
            )
            selected_ids = {item["task_id"] for item in selected}
            tasks = [task for task in tasks
                     if str(task.get("task_id") or task.get("id") or "") in selected_ids]
        except (RuntimeError, ValueError) as exc:
            _error(errors, "writer_fanout_task_selection_invalid", "task_map.tasks", str(exc))
        for name, body in port_bodies.items():
            try:
                validate_input_body_partitions(
                    state_dir=state_dir, task_map=task_map, tasks=tasks,
                    name=name, body=body, required_ports=required_with_source, trigger=trigger,
                )
            except ValueError as exc:
                _error(errors, "task_scoped_input_invalid", f"plan_ports.{name}", str(exc))
        prior_task_map = _canonical_prior_task_map(
            current=task_map, trigger=trigger,
            state_dir=state_dir,
            project_root=project_root,
            errors=errors,
        )
        if prior_task_map:
            from zf.runtime.plan_preserved_tasks_preflight import preserved_task_errors

            if selected_ids is not None:
                errors.extend(preserved_task_errors(state_dir=state_dir, previous=prior_task_map,
                    current=task_map, trigger=trigger))
            from zf.runtime.plan_requirement_amendment import amended_task_map_baseline
            try:
                prior_task_map = amended_task_map_baseline(state_dir,
                    str(trigger.get("workflow_run_id") or trigger.get("trace_id") or ""), prior_task_map)
            except (OSError, ValueError) as exc:
                _error(errors, "owner_amendment_invalid", "requirement_amendment_ref", str(exc))
            errors.extend(_mandatory_goal_claim_contract_errors(
                previous=prior_task_map,
                current=task_map,
            ))
        validation = validate_task_map_payload(
            dict(task_map),
            require_task_verification=True,
        )
        from zf.runtime.task_map_required_ports import plan_input_declaration_errors, selected_input_body_errors

        binding_errors = plan_input_declaration_errors(
            dict(task_map), available_ports=available | _KERNEL_DERIVED_PORTS,
            require_selection=(metadata or {}).get("artifact_package", {}).get("task_input_policy") == "task-scoped-v1",
        )
        binding_errors.extend(selected_input_body_errors(dict(task_map), port_bodies))
        for message in dict.fromkeys([*validation.errors, *binding_errors]):
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
        errors.extend(_claim_acceptance_command_errors(
            task_map, host_commands=(writer_policy or {}).get("host_verification_commands", ()),
        ))
        from zf.runtime.frozen_audit_scope import FROZEN_CANDIDATE_IDS

        for task in tasks:
            evidence = task.get("evidence_contract")
            if (isinstance(evidence, Mapping) and FROZEN_CANDIDATE_IDS in evidence
                    and (writer_policy or {}).get("frozen_candidate_reader") is not True):
                _error(errors, "frozen_candidate_consumer_missing", f"task_map.tasks[{task.get('task_id')}].evidence_contract",
                       "The pinned workflow must have a downstream Candidate Verify stage for deferred audit criteria.")
        errors.extend(_rolling_smoke_command_errors(task_map, metadata=metadata))
        if validation.passed:
            task_items: list[dict[str, Any]] = []
            try:
                task_items = writer_task_items(dict(task_map))
            except (RuntimeError, ValueError) as exc:
                _error(
                    errors,
                    "writer_fanout_task_map_invalid",
                    "task_map.tasks",
                    str(exc),
                )
            if task_items:
                try:
                    selected, _, _, _ = select_writer_task_items(dict(task_map), task_items, trigger)
                except (RuntimeError, ValueError) as exc:
                    _error(errors, "writer_fanout_task_selection_invalid", "task_map.tasks", str(exc))
                else:
                    try:
                        validate_writer_task_items(selected)
                    except (RuntimeError, ValueError) as exc:
                        _error(errors, "writer_fanout_task_map_invalid", "task_map.tasks", str(exc))
                    try:
                        from zf.runtime.writer_fanout_admission import writer_task_map_dispatch_target

                        writer_fanout_dispatch_base_commit(
                            selected, fallback=writer_task_map_dispatch_target(task_map, trigger),
                            independent_task_bases=(
                                (metadata or {}).get("task_pipeline", {}).get("mode")
                                == "blocking"
                            ),
                        )
                    except ValueError as exc:
                        _error(errors, "writer_fanout_base_conflict", "task_map.tasks", str(exc))
                policy = writer_policy or {}
                from zf.runtime.plan_candidate_role_preflight import writer_role_errors

                errors.extend(writer_role_errors(
                    task_items, flow_kind=flow_kind, policy=policy,
                ))
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
        from zf.runtime.source_index_gate import evaluate_source_index_gate

        # Mirror writer admission before spending a Critic attempt. This is
        # anchor presence only; semantic coverage remains the Critic's job.
        source_gate = evaluate_source_index_gate(
            task_items=[{**task, "task_id": task.get("task_id") or task.get("id")}
                        for task in task_map.get("tasks", []) if isinstance(task, Mapping)],
            source_index=source_index, findings=None,
            harness_profile=str((writer_policy or {}).get("harness_profile") or ""),
        )
        if not source_gate.passed:
            _error(errors, "source_index_gap", "source_index",
                   f"{source_gate.note}: {', '.join(source_gate.missing_anchor_task_ids)}")
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

    from zf.runtime.plan_candidate_input_cost import candidate_input_cost

    return {
        "schema_version": SCHEMA_VERSION,
        "input_cost": candidate_input_cost(task_map or {}, port_bodies, review_input_limits),
        "execution_review": review_execution(
            task_map or {}, ports=port_bodies,
            policy=(writer_policy or {}).get("execution", {}), metadata=metadata or {},
            identity=trigger,
            usage_facts=execution_usage_facts(
                state_dir, workflow_run_id=str(trigger.get("workflow_run_id") or ""),
            ),
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
        if "candidate_reader_assignments" in normalized:
            from zf.runtime.candidate_reader_assignments import validate_plan_assignment_coverage

            validate_plan_assignment_coverage(normalized, task_map)
    except ValueError as exc:
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
    current: Mapping[str, Any],
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
            workflow_run_id = str(trigger.get("workflow_run_id") or trigger.get("trace_id") or "")
            if workflow_run_id and package.get("workflow_run_id") != workflow_run_id:
                raise ValueError("canonical Plan Package belongs to another workflow run")
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
            prior_map = _load_bound_task_map(
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
        from zf.runtime.plan_claim_continuity import candidate_claim_errors

        try:
            errors.extend(candidate_claim_errors(state_dir, project_root, package, current, trigger))
        except (OSError, ValueError) as exc:
            _error(errors, "goal_claim_set_invalid", "task_map.goal_claims", str(exc))
        return prior_map

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
    raw = read_plan_port_bytes(path, "task_map")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        raise ValueError(
            f"task-map digest mismatch for {ref!r}: "
            f"expected {expected}, got {actual}"
        )
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("task-map must be a JSON object")
    return body


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
            try:
                check_plan_port_size("task_map", len(canonical_json_bytes(inline)))
                found.append((dict(inline), f"report[{index}].task_map"))
            except PlanPortTooLarge as exc:
                _error(errors, "plan_port_too_large", f"report[{index}].task_map", str(exc))
        ref = str(candidate.get("task_map_ref") or "").strip()
        if not ref:
            continue
        try:
            path = resolve_artifact_file(ref, project_root=project_root, state_dir=state_dir)
            body = json.loads(read_plan_port_bytes(path, "task_map"))
            if not isinstance(body, dict):
                raise ValueError("task-map must be a JSON object")
            found.append((body, f"report[{index}].task_map_ref"))
        except PlanPortTooLarge as exc:
            _error(errors, "plan_port_too_large", f"report[{index}].task_map_ref", str(exc))
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
            body = json.loads(read_plan_port_bytes(path, "source_index"))
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


from zf.runtime.plan_candidate_commands import (
    _claim_acceptance_command_errors, _rolling_smoke_command_errors, rolling_smoke_command_errors,
)


def _error(errors: list[dict[str, str]], code: str, field: str, message: str) -> None:
    errors.append({"code": code, "field": field, "message": message})


__all__ = [
    "SCHEMA_VERSION",
    "evaluate_plan_candidate_preflight",
    "rolling_smoke_command_errors",
]
