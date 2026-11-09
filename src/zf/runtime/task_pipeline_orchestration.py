"""Compile admitted OA delegation into bounded Task Pipeline selection inputs."""

from __future__ import annotations

from typing import Any, Mapping

from zf.runtime.orchestrator_agent_contracts import OrchestratorAgentContractError
from zf.runtime.orchestrator_agent_run_plan import (
    EXECUTION_PLAN_SCHEMA,
    current_run_plan_admission,
    _role_capabilities,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


_IDENTITY = (
    "workflow_run_id",
    "task_map_generation",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
)


def admitted_pipeline_delegations(
    runtime: Any,
    *,
    generation_contexts: Mapping[str, Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Read same-generation plans; never change tasks or an active assignment.

    Role preferences are soft. Capabilities and stage pool membership are hard.
    A retained task from a partial replan keeps its own admitted generation.
    """
    events = runtime.event_log.read_all()
    plans: dict[tuple[str, ...], tuple[dict, dict]] = {}
    result: dict[str, dict[str, dict[str, Any]]] = {}
    roles = list(getattr(runtime.config, "roles", []) or [])
    for task_id, context in generation_contexts.items():
        identity = {key: str(context.get(key) or "") for key in _IDENTITY}
        if not all(identity.values()):
            continue
        key = tuple(identity.values())
        if key not in plans:
            admission = current_run_plan_admission(events, **identity)
            if not admission:
                plans[key] = ({}, {})
            else:
                descriptor = admission.get("execution_plan_ref")
                if not isinstance(descriptor, Mapping):
                    raise OrchestratorAgentContractError(
                        "OA execution plan ref missing"
                    )
                plan = hydrate_sidecar_ref(runtime.state_dir, dict(descriptor)).payload
                if (
                    not isinstance(plan, Mapping)
                    or plan.get("schema_version") != EXECUTION_PLAN_SCHEMA
                ):
                    raise OrchestratorAgentContractError("OA execution plan invalid")
                if any(
                    str((plan.get("identity") or {}).get(k) or "") != v
                    for k, v in identity.items()
                ):
                    raise OrchestratorAgentContractError(
                        "OA execution plan identity mismatch"
                    )
                plans[key] = (dict(plan), dict(descriptor))
        plan, descriptor = plans[key]
        units = {row["work_unit_id"]: row for row in plan.get("work_units", [])}
        for delegation in plan.get("delegation", []):
            unit_id = str(delegation["work_unit_id"])
            unit = units[unit_id]
            task_ids = unit.get("task_ids") or []
            stage_ids = unit.get("stage_ids") or []
            if task_ids and task_id not in task_ids:
                continue
            if not task_ids and unit_id != task_id and not stage_ids:
                continue
            preferred = []
            for ref in delegation.get("preferred_role_refs", []):
                matches = [
                    role for role in roles if ref in {role.name, role.instance_id}
                ]
                if len(matches) != 1:
                    raise OrchestratorAgentContractError(
                        f"OA delegation role missing or ambiguous: {ref}"
                    )
                preferred.append(matches[0].instance_id)
            required = set(delegation.get("capability_refs") or [])
            for stage, pool in (policy.get("pools") or {}).items():
                # Exact pipeline stage names, or the configured flow's generated stage id.
                aliases = {stage, f"{context.get('flow_kind', '')}-lanes-{stage}"}
                if stage == "impl":
                    aliases.add("implementation")
                if stage_ids and not aliases.intersection(stage_ids):
                    continue
                pool_roles = set(pool.get("role_instances") or [])
                stage_preferred = [ref for ref in preferred if ref in pool_roles]
                if not stage_preferred and not stage_ids:
                    continue
                profiles = {
                    row["role"]: set(row.get("capabilities") or [])
                    for row in pool.get("worker_profiles", [])
                }
                eligible = sorted(
                    role.instance_id
                    for role in roles
                    if role.instance_id in pool_roles
                    and required
                    <= (
                        _role_capabilities(role)
                        | profiles.get(
                            role.instance_id, set(pool.get("capabilities") or [])
                        )
                    )
                )
                row = {
                    "preferred_roles": stage_preferred,
                    "eligible_roles": eligible,
                    "execution_plan_ref": descriptor,
                    "work_unit_id": unit_id,
                }
                if "dispatch_rank" in delegation:
                    row["dispatch_rank"] = delegation["dispatch_rank"]
                previous = result.setdefault(task_id, {}).get(stage)
                if previous is not None and previous != row:
                    raise OrchestratorAgentContractError(
                        f"overlapping OA delegations: {task_id}/{stage}"
                    )
                result[task_id][stage] = row
    return result
