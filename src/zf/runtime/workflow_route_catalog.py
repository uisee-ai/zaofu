"""Read-only catalog of workflow routes shared by every control surface."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from zf.core.workflow.flow_metadata import flow_metadata_for, normalize_flow_kind
from zf.runtime.workflow_route_policy import (
    effective_execution_policy_digest,
    route_execution_policy,
    route_flow_topology,
)
from zf.runtime.research_templates import (
    ADAPTIVE_RESEARCH_TEMPLATE,
    FIXED_RESEARCH_TEMPLATE,
    RESEARCH_TEMPLATES,
    research_stage_contract_error,
)

WORKFLOW_ROUTE_CATALOG_SCHEMA_VERSION = "workflow-route-catalog.v1"
ADAPTIVE_RESEARCH_ROUTE_ID = ADAPTIVE_RESEARCH_TEMPLATE.route_id
ADAPTIVE_RESEARCH_PATTERN_ID = ADAPTIVE_RESEARCH_TEMPLATE.pattern_id
FIXED_RESEARCH_ROUTE_ID = FIXED_RESEARCH_TEMPLATE.route_id
FIXED_RESEARCH_PATTERN_ID = FIXED_RESEARCH_TEMPLATE.pattern_id


def workflow_route_catalog(config: Any | None) -> dict[str, Any]:
    """Project active workflow entries without creating a second config truth."""
    if config is None:
        return {
            "schema_version": WORKFLOW_ROUTE_CATALOG_SCHEMA_VERSION,
            "is_derived_projection": True,
            "config_digest": "",
            "routes": [],
        }

    stages = list(getattr(getattr(config, "workflow", None), "stages", []) or [])
    roles = list(getattr(config, "roles", []) or [])
    stage_by_id = {
        str(getattr(stage, "id", "") or ""): stage
        for stage in stages
        if str(getattr(stage, "id", "") or "")
    }
    role_kind_by_id: dict[str, str] = {}
    for role in roles:
        kind = str(getattr(role, "role_kind", "") or "")
        for identity in (
            str(getattr(role, "instance_id", "") or ""),
            str(getattr(role, "name", "") or ""),
        ):
            if identity:
                role_kind_by_id[identity] = kind

    routes: list[dict[str, Any]] = []
    claimed_entries: set[str] = set()
    kind_routes = dict(
        getattr(getattr(config, "workflow", None), "kind_routes", {}) or {}
    )
    for kind, route in sorted(kind_routes.items()):
        route_kind = str(kind).strip().lower()
        canonical_kind, resolved = _resolve_kind_route(
            kind_routes,
            route_kind,
            route,
        )
        if resolved is None or canonical_kind != route_kind:
            continue
        targets = _route_targets(resolved)
        for tier, pattern_id in targets:
            stage = stage_by_id.get(pattern_id)
            if stage is None:
                continue
            entry_contract = delivery_route_entry_contract(
                config,
                kind=canonical_kind,
                pattern_id=pattern_id,
                stage=stage,
                role_kind_by_id=role_kind_by_id,
            )
            if not entry_contract["ok"]:
                continue
            claimed_entries.add(pattern_id)
            route_stages = _delivery_route_stages(
                stages,
                kind=canonical_kind,
                entry_pattern_id=pattern_id,
            )
            route_roles = _stage_roles(route_stages)
            lane_count = _delivery_lane_count(
                config,
                kind=canonical_kind,
                route_roles=route_roles,
                role_kind_by_id=role_kind_by_id,
            )
            routes.append({
                "route_id": f"delivery:{canonical_kind}:{tier or 'default'}",
                "family": "delivery",
                "kind": canonical_kind,
                "tier": tier,
                "entry_pattern_id": pattern_id,
                "topology": (
                    "multi_lane"
                    if lane_count > 1
                    else "single_lane"
                    if lane_count == 1
                    else str(getattr(stage, "topology", "") or "")
                ),
                "stages": [
                    str(getattr(item, "id", "") or "")
                    for item in route_stages
                ],
                "roles": route_roles,
                "writer_roles": _roles_of_kind(
                    route_roles,
                    role_kind_by_id,
                    "writer",
                ),
                "verify_roles": [
                    role for role in route_roles
                    if "verify" in role.lower() or "test" in role.lower()
                ],
                "lane_count": lane_count,
                "output_profile": "candidate_and_evidence",
                "entry_class": entry_contract["entry_class"],
                "entry_topology": entry_contract["entry_topology"],
                "entry_trigger": entry_contract["entry_trigger"],
                "start_adapter": entry_contract["start_adapter"],
                "available": True,
            })

    for template in RESEARCH_TEMPLATES:
        research_stage = stage_by_id.get(template.pattern_id)
        if research_stage is None:
            continue
        claimed_entries.add(template.pattern_id)
        if research_stage_contract_error(
            research_stage,
            template,
        ) or not _is_reader_entry(
            research_stage,
            role_kind_by_id=role_kind_by_id,
        ):
            continue
        research_roles = _stage_roles([research_stage])
        routes.append({
            "route_id": template.route_id,
            "family": "research",
            "kind": "research",
            "tier": template.tier,
            "template_id": template.template_id,
            "entry_pattern_id": template.pattern_id,
            "topology": str(
                getattr(research_stage, "topology", "") or "fanout_reader"
            ),
            "stages": [template.pattern_id],
            "roles": research_roles,
            "writer_roles": [],
            "verify_roles": [
                role for role in research_roles
                if "critic" in role.lower()
            ],
            "lane_count": 0,
            "output_profile": "research_report",
            "start_adapter": (
                "adaptive_research"
                if template is ADAPTIVE_RESEARCH_TEMPLATE
                else "fixed_research"
            ),
            "rollout": template.rollout,
            "task_binding_policy": "workflow_request_for_channel_prd",
            "available": True,
        })

    for stage in stages:
        stage_id = str(getattr(stage, "id", "") or "")
        if (
            not stage_id
            or stage_id in claimed_entries
            or str(getattr(stage, "trigger", "") or "")
            != "workflow.invoke.requested"
            or not _is_reader_entry(stage, role_kind_by_id=role_kind_by_id)
        ):
            continue
        general_roles = _stage_roles([stage])
        bounded = next((contract for contract in getattr(config.workflow, "generic_workflows", [])
                        if contract.get("template") == "bounded-agent-v1" and contract.get("entry") == stage_id), None)
        if bounded:
            general_roles = list(dict.fromkeys(role for task in bounded["tasks"] for role in task["roles"]))
        routes.append({
            "route_id": f"agent:single:{stage_id}" if bounded else f"general:{stage_id}",
            "family": "agent" if bounded else "general",
            **({"execution_mode": "single_executor", "executor_count": 1,
                "independent_verifier_count": 1, "source_write": False,
                "template_id": "bounded-agent-v1"} if bounded else {}),
            "kind": "workflow",
            "tier": "",
            "entry_pattern_id": stage_id,
            "topology": str(getattr(stage, "topology", "") or "single_reader"),
            "stages": [task["name"] for task in bounded["tasks"]] if bounded else [stage_id],
            "roles": general_roles,
            "writer_roles": [],
            "verify_roles": [role for task in bounded["tasks"]
                             if task["operation"] == "agent.verify" for role in task["roles"]] if bounded else [
                role for role in general_roles
                if "verify" in role.lower() or "critic" in role.lower()
            ],
            "lane_count": 0,
            "output_profile": "artifact",
            "start_adapter": "registered_general",
            "available": True,
        })

    policy_digest = effective_execution_policy_digest(config)
    for route in routes:
        route["flow_topology"] = route_flow_topology(config, route)
        route["execution_policy"] = route_execution_policy(
            config, route, digest=policy_digest,
        )
    routes.sort(key=lambda item: str(item["route_id"]))
    config_digest = _catalog_digest(routes)
    return {
        "schema_version": WORKFLOW_ROUTE_CATALOG_SCHEMA_VERSION,
        "is_derived_projection": True,
        "config_digest": config_digest,
        "routes": routes,
    }


def resolve_workflow_route(
    config: Any | None,
    route_id: str,
    *,
    expected_config_digest: str = "",
) -> dict[str, Any] | None:
    catalog = workflow_route_catalog(config)
    if (
        expected_config_digest
        and expected_config_digest != str(catalog.get("config_digest") or "")
    ):
        return None
    wanted = str(route_id or "").strip()
    return next(
        (
            dict(route)
            for route in catalog["routes"]
            if str(route.get("route_id") or "") == wanted
            and bool(route.get("available"))
        ),
        None,
    )


def _resolve_kind_route(
    routes: dict[str, Any],
    kind: str,
    route: Any,
) -> tuple[str, Any | None]:
    current_kind = str(kind or "").strip().lower()
    current = route
    seen: set[str] = set()
    while current is not None:
        alias = str(getattr(current, "alias", "") or "").strip().lower()
        if not alias:
            return current_kind, current
        if alias in seen:
            return current_kind, None
        seen.add(alias)
        current_kind = alias
        current = routes.get(alias)
    return current_kind, None


def _route_targets(route: Any) -> list[tuple[str, str]]:
    default_tier = str(getattr(route, "default_tier", "") or "").strip()
    pattern_id = str(getattr(route, "pattern_id", "") or "").strip()
    tier_routes = dict(getattr(route, "tier_routes", {}) or {})
    targets: list[tuple[str, str]] = []
    if pattern_id:
        targets.append((default_tier or "default", pattern_id))
    for tier, target in sorted(tier_routes.items()):
        value = str(target or "").strip()
        if value and (str(tier), value) not in targets:
            targets.append((str(tier), value))
    return targets


def _delivery_route_stages(
    stages: list[Any],
    *,
    kind: str,
    entry_pattern_id: str,
) -> list[Any]:
    scoped = [
        stage
        for stage in stages
        if str(getattr(stage, "flow_kind", "") or "") == kind
    ]
    if not scoped:
        prefix = f"{kind}-"
        scoped = [
            stage
            for stage in stages
            if str(getattr(stage, "id", "") or "").startswith(prefix)
        ]
    entry = next(
        (
            stage for stage in stages
            if str(getattr(stage, "id", "") or "") == entry_pattern_id
        ),
        None,
    )
    if entry is not None and entry not in scoped:
        scoped.insert(0, entry)
    return scoped or ([entry] if entry is not None else [])


def _stage_roles(stages: list[Any]) -> list[str]:
    roles: list[str] = []
    for stage in stages:
        for role in list(getattr(stage, "roles", []) or []):
            value = str(role or "").strip()
            if value and value not in roles:
                roles.append(value)
    return roles


def _roles_of_kind(
    roles: list[str],
    role_kind_by_id: dict[str, str],
    wanted: str,
) -> list[str]:
    return [
        role for role in roles
        if role_kind_by_id.get(role) == wanted
    ]


def _delivery_lane_count(
    config: Any,
    *,
    kind: str,
    route_roles: list[str],
    role_kind_by_id: dict[str, str],
) -> int:
    lane_profiles = dict(
        getattr(getattr(config, "workflow", None), "affinity_lanes", {}) or {}
    )
    counts = [
        len(list(getattr(profile, "lanes", []) or []))
        for profile_id, profile in lane_profiles.items()
        if str(profile_id or "").startswith(f"{kind}-")
    ]
    writer_count = len(_roles_of_kind(
        route_roles,
        role_kind_by_id,
        "writer",
    ))
    return max([writer_count, *counts], default=0)


def delivery_route_entry_contract(
    config: Any,
    *,
    kind: str,
    pattern_id: str,
    stage: Any | None = None,
    role_kind_by_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate the adapter boundary for one externally selectable route."""
    stages = list(getattr(getattr(config, "workflow", None), "stages", []) or [])
    if stage is None:
        stage = next(
            (
                item
                for item in stages
                if str(getattr(item, "id", "") or "") == pattern_id
            ),
            None,
        )
    if stage is None:
        return _delivery_entry_contract_failure(
            f"workflow route {kind!r} references missing stage {pattern_id!r}"
        )

    canonical_kind = str(kind or "").strip().lower()
    stage_kind = str(getattr(stage, "flow_kind", "") or "").strip().lower()
    if stage_kind and stage_kind != canonical_kind:
        return _delivery_entry_contract_failure(
            f"workflow route {canonical_kind!r} points at stage {pattern_id!r} "
            f"owned by flow_kind {stage_kind!r}"
        )

    topology = str(getattr(stage, "topology", "") or "").strip()
    trigger = str(getattr(stage, "trigger", "") or "").strip()
    metadata = flow_metadata_for(config, canonical_kind)
    if str(metadata.get("topology") or "").strip() == "light":
        entry_trigger = str(metadata.get("light_entry_trigger") or "").strip()
        if not entry_trigger:
            return _delivery_entry_contract_failure(
                f"light workflow route {canonical_kind!r} has no light_entry_trigger"
            )
        if topology != "fanout_writer_scoped" or trigger != "task_map.ready":
            return _delivery_entry_contract_failure(
                f"light workflow route {canonical_kind!r} must target a "
                "fanout_writer_scoped task_map.ready stage"
            )
        return {
            "ok": True,
            "error": "",
            "entry_class": "light_adapter",
            "entry_topology": topology,
            "entry_trigger": entry_trigger,
            "start_adapter": "light_delivery_request_submit",
        }

    if topology != "fanout_reader":
        return _delivery_entry_contract_failure(
            f"blocking workflow route {canonical_kind!r} must target a "
            f"fanout_reader entry, got {topology or 'unset'}"
        )
    role_kinds = role_kind_by_id or _role_kind_index(config)
    if not _is_reader_entry(stage, role_kind_by_id=role_kinds):
        return _delivery_entry_contract_failure(
            f"blocking workflow route {canonical_kind!r} entry {pattern_id!r} "
            "must contain reader roles only"
        )
    external_triggers = {
        str(item or "").strip()
        for item in list(
            getattr(
                getattr(getattr(config, "workflow", None), "dag", None),
                "external_triggers",
                [],
            )
            or []
        )
        if str(item or "").strip()
    }
    if not trigger or trigger not in external_triggers:
        return _delivery_entry_contract_failure(
            f"blocking workflow route {canonical_kind!r} entry trigger "
            f"{trigger or 'unset'!r} is not declared in workflow.dag.external_triggers"
        )
    return {
        "ok": True,
        "error": "",
        "entry_class": "external_reader",
        "entry_topology": topology,
        "entry_trigger": trigger,
        "start_adapter": "delivery_request_submit",
    }


def delivery_route_contracts_for_kind(
    config: Any,
    kind: str,
) -> list[dict[str, Any]]:
    """Return every configured external entry contract for one request kind."""
    canonical_request_kind = normalize_flow_kind(kind)
    routes = dict(
        getattr(getattr(config, "workflow", None), "kind_routes", {}) or {}
    )
    route = routes.get(canonical_request_kind)
    if route is None:
        return []
    canonical_kind, resolved = _resolve_kind_route(
        routes,
        canonical_request_kind,
        route,
    )
    if resolved is None:
        return []
    return [
        {
            "tier": tier,
            "pattern_id": pattern_id,
            **delivery_route_entry_contract(
                config,
                kind=canonical_kind,
                pattern_id=pattern_id,
            ),
        }
        for tier, pattern_id in _route_targets(resolved)
    ]


def _delivery_entry_contract_failure(error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "entry_class": "invalid",
        "entry_topology": "",
        "entry_trigger": "",
        "start_adapter": "",
    }


def _role_kind_index(config: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for role in list(getattr(config, "roles", []) or []):
        kind = str(getattr(role, "role_kind", "") or "")
        for identity in (
            str(getattr(role, "instance_id", "") or ""),
            str(getattr(role, "name", "") or ""),
        ):
            if identity:
                result[identity] = kind
    return result


def _is_reader_entry(
    stage: Any,
    *,
    role_kind_by_id: dict[str, str],
) -> bool:
    topology = str(getattr(stage, "topology", "") or "")
    roles = _stage_roles([stage])
    return (
        topology in {"", "single_reader", "fanout_reader"}
        and bool(roles)
        and all(role_kind_by_id.get(role, "reader") != "writer" for role in roles)
    )


def _catalog_digest(routes: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        routes,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "ADAPTIVE_RESEARCH_PATTERN_ID",
    "ADAPTIVE_RESEARCH_ROUTE_ID",
    "FIXED_RESEARCH_PATTERN_ID",
    "FIXED_RESEARCH_ROUTE_ID",
    "WORKFLOW_ROUTE_CATALOG_SCHEMA_VERSION",
    "delivery_route_contracts_for_kind",
    "delivery_route_entry_contract",
    "resolve_workflow_route",
    "workflow_route_catalog",
]
