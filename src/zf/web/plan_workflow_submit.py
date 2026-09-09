"""Mechanical normalization for Task-bound workflow Plan options."""

from __future__ import annotations

from typing import Any

from zf.runtime.task_workflow_plans import (
    normalize_task_workflow_parameters,
    workflow_route_missing_parameters,
)
from zf.runtime.workflow_route_catalog import (
    resolve_workflow_route,
    workflow_route_catalog,
)


def normalize_task_workflow_submit_payload(
    raw_payload: dict[str, Any],
    *,
    config: Any | None,
    task_binding_digests: dict[str, str],
    workflow_route_eligibility: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Bind one provider-proposed route to current Task and config facts."""

    allowed_keys = {
        "config_digest",
        "objective",
        "parameters",
        "route_id",
        "task_contract_digest",
        "task_id",
    }
    unknown = sorted(set(raw_payload) - allowed_keys)
    if unknown:
        return (
            {},
            {},
            "unsupported submit_payload field(s): " + ", ".join(unknown),
        )
    task_id = str(raw_payload.get("task_id") or "").strip()
    route_id = str(raw_payload.get("route_id") or "").strip()
    objective = str(raw_payload.get("objective") or "").strip()
    if not task_id:
        return {}, {}, "submit_payload.task_id is required"
    if not route_id:
        return {}, {}, "submit_payload.route_id is required"
    if not objective:
        return {}, {}, "submit_payload.objective is required"
    current_task_digest = str(
        task_binding_digests.get(task_id) or ""
    ).strip()
    proposed_task_digest = str(
        raw_payload.get("task_contract_digest") or ""
    ).strip()
    if not current_task_digest:
        return {}, {}, "workflow Task binding is unavailable"
    if (
        proposed_task_digest
        and proposed_task_digest != current_task_digest
    ):
        return {}, {}, "workflow Task binding is stale"
    parameters = raw_payload.get("parameters")
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        return {}, {}, "submit_payload.parameters must be a mapping"
    parameters, parameter_error = normalize_task_workflow_parameters(
        parameters
    )
    catalog = workflow_route_catalog(config)
    config_digest = str(
        raw_payload.get("config_digest")
        or catalog.get("config_digest")
        or ""
    )
    route = resolve_workflow_route(
        config,
        route_id,
        expected_config_digest=config_digest,
    )
    if route is None:
        return {}, {}, f"workflow route {route_id!r} is stale or unavailable"
    eligibility_error = str(
        workflow_route_eligibility.get(task_id, {}).get(route_id) or ""
    )
    if eligibility_error:
        return {}, {}, eligibility_error
    missing = workflow_route_missing_parameters(
        route,
        objective=objective,
        parameters=parameters,
    )
    errors: list[str] = []
    if parameter_error:
        errors.append(parameter_error.replace(
            "unsupported parameter field(s)",
            "unsupported workflow parameter field(s)",
        ))
    if missing:
        errors.append("missing executable parameter(s): " + ", ".join(missing))
    if errors:
        return (
            {
                "task_id": task_id,
                "route_id": route_id,
                "objective": objective,
                "config_digest": config_digest,
                "task_contract_digest": current_task_digest,
                "parameters": parameters,
            },
            {},
            "; ".join(errors),
        )
    payload = {
        "task_id": task_id,
        "route_id": route_id,
        "objective": objective,
        "config_digest": config_digest,
        "task_contract_digest": current_task_digest,
        "parameters": {
            str(key): value
            for key, value in parameters.items()
            if value not in (None, "", [], {})
        },
    }
    details = {
        "route_id": route_id,
        "family": str(route.get("family") or ""),
        "kind": str(route.get("kind") or ""),
        "tier": str(route.get("tier") or ""),
        "topology": str(route.get("topology") or ""),
        "roles": list(route.get("roles") or []),
        "writer_roles": list(route.get("writer_roles") or []),
        "verify_roles": list(route.get("verify_roles") or []),
        "lane_count": int(route.get("lane_count") or 0),
        "output_profile": str(route.get("output_profile") or ""),
        "input_keys": sorted(payload["parameters"]),
        "preflight": "ready",
    }
    return payload, details, ""
