"""Task-scoped execution route contracts and deterministic current state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from zf.core.config.schema import ExecutionRouteConfig, ProviderSessionConfig, RoleConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore, ZfNotInitialized
from zf.core.task.store import TERMINAL_STATES, TaskStore


EXECUTION_ROUTE_SWITCH_ACTION = "execution-route-switch"
EXECUTION_ROUTE_SAFE_ACTION = "switch_execution_route"
EXECUTION_ROUTE_STATE_SCHEMA = "execution-route-state.v1"
ROUTE_SELECTION_RECEIPT_SCHEMA = "route-selection-receipt.v1"
EXECUTION_ROUTE_APPLIED_EVENT = "execution.route.selection.applied"
EXECUTION_ROUTE_TRIGGER_CLASSES = frozenset({
    "provider_unavailable",
    "provider_rate_limited",
    "provider_capability_mismatch",
    "provider_context_exhausted",
})


class ExecutionRouteError(ValueError):
    """Execution route request is invalid, stale, or exceeds policy."""


def execution_routing_policy(config: ZfConfig | None) -> Any:
    return getattr(getattr(config, "runtime", None), "execution_routing", None)


def execution_route_policy_digest(config: ZfConfig | None) -> str:
    policy = execution_routing_policy(config)
    if policy is None:
        return ""
    return _digest({
        "schema_version": "execution-route-policy.v1",
        "enabled": bool(getattr(policy, "enabled", False)),
        "max_switches_per_task": int(
            getattr(policy, "max_switches_per_task", 0) or 0
        ),
        "semantic_triage_attempt": int(
            getattr(policy, "semantic_triage_attempt", 0) or 0
        ),
        "routes": [
            execution_route_payload(route)
            for route in list(getattr(policy, "routes", []) or [])
        ],
    })


def execution_route_payload(route: ExecutionRouteConfig) -> dict[str, Any]:
    payload = asdict(route)
    payload["roles"] = sorted(set(payload.get("roles") or []))
    payload["flow_kinds"] = sorted(set(payload.get("flow_kinds") or []))
    payload["automatic_triggers"] = sorted(
        set(payload.get("automatic_triggers") or [])
    )
    return payload


def resolve_execution_route(
    config: ZfConfig | None,
    *,
    route_id: str,
    role: RoleConfig,
    trigger_class: str,
    flow_kind: str = "",
) -> ExecutionRouteConfig:
    policy = execution_routing_policy(config)
    if policy is None or not bool(getattr(policy, "enabled", False)):
        raise ExecutionRouteError("runtime.execution_routing is disabled")
    route = next((
        item
        for item in list(getattr(policy, "routes", []) or [])
        if str(getattr(item, "id", "") or "") == route_id
    ), None)
    if route is None:
        raise ExecutionRouteError(f"execution route {route_id!r} is not declared")
    role_refs = set(getattr(route, "roles", []) or [])
    if role.name not in role_refs and role.instance_id not in role_refs:
        raise ExecutionRouteError(
            f"execution route {route_id!r} does not allow role {role.instance_id!r}"
        )
    allowed_flows = set(getattr(route, "flow_kinds", []) or [])
    normalized_flow = str(flow_kind or role.flow_kind or "").strip().lower()
    if allowed_flows and normalized_flow not in allowed_flows:
        raise ExecutionRouteError(
            f"execution route {route_id!r} does not allow flow {normalized_flow!r}"
        )
    if trigger_class not in EXECUTION_ROUTE_TRIGGER_CLASSES:
        raise ExecutionRouteError(
            f"execution route trigger {trigger_class!r} is not structured"
        )
    if trigger_class not in set(getattr(route, "automatic_triggers", []) or []):
        raise ExecutionRouteError(
            f"execution route {route_id!r} does not allow trigger {trigger_class!r}"
        )
    return route


def matching_execution_routes(
    config: ZfConfig | None,
    *,
    role: RoleConfig,
    trigger_class: str,
    flow_kind: str = "",
) -> list[ExecutionRouteConfig]:
    policy = execution_routing_policy(config)
    if policy is None or not bool(getattr(policy, "enabled", False)):
        return []
    matches: list[ExecutionRouteConfig] = []
    for route in list(getattr(policy, "routes", []) or []):
        try:
            matches.append(resolve_execution_route(
                config,
                route_id=route.id,
                role=role,
                trigger_class=trigger_class,
                flow_kind=flow_kind,
            ))
        except ExecutionRouteError:
            continue
    return matches


def execution_route_catalog_for_role(
    config: ZfConfig | None,
    *,
    role: RoleConfig,
) -> list[dict[str, Any]]:
    """Project only statically approved routes applicable to one role."""

    policy = execution_routing_policy(config)
    if policy is None or not bool(getattr(policy, "enabled", False)):
        return []
    catalog: list[dict[str, Any]] = []
    for route in list(getattr(policy, "routes", []) or []):
        role_refs = set(getattr(route, "roles", []) or [])
        flow_kinds = set(getattr(route, "flow_kinds", []) or [])
        if not {role.name, role.instance_id}.intersection(role_refs):
            continue
        if flow_kinds and role.flow_kind not in flow_kinds:
            continue
        catalog.append({
            "id": route.id,
            "backend": route.backend,
            "model": route.model,
            "execution_profile": route.execution_profile,
            "automatic_triggers": list(route.automatic_triggers),
        })
    return catalog


def apply_execution_route(
    role: RoleConfig,
    route: ExecutionRouteConfig,
) -> RoleConfig:
    """Apply only provider/execution fields; never widen role authority."""

    execution = role.execution
    if route.execution_profile:
        if route.execution_profile not in set(execution.profile_allowlist):
            raise ExecutionRouteError(
                f"execution profile {route.execution_profile!r} is not allowed "
                f"by role {role.instance_id!r}"
            )
        execution = replace(
            execution,
            default_profile=route.execution_profile,
        )
    provider_session = route.provider_session
    if getattr(role.provider_session, "native_delegation", None) is False:
        if getattr(provider_session, "native_delegation", None) is True:
            raise ExecutionRouteError("execution route cannot enable native delegation for this role")
        provider_session = replace(
            provider_session or ProviderSessionConfig(), native_delegation=False,
        )
    return replace(
        role,
        backend=route.backend,
        model=route.model,
        model_reasoning_effort=route.model_reasoning_effort,
        provider_session=provider_session,
        execution=execution,
    )


def classify_execution_route_trigger(event: ZfEvent) -> str:
    """Map structured provider facts only; never parse arbitrary prose."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    explicit = str(payload.get("execution_route_trigger") or "").strip()
    if (
        event.type == "orchestrator.rework.triage.recorded"
        and explicit in EXECUTION_ROUTE_TRIGGER_CLASSES
    ):
        return explicit
    failure_class = str(payload.get("failure_class") or "").strip()
    if (
        event.type == "provider.telemetry.capability.observed"
        and failure_class in {
            "provider_native_probe_required",
            "provider_capability_mismatch",
        }
    ):
        return "provider_capability_mismatch"
    if event.type == "provider.stop.recovery":
        reason = str(
            payload.get("provider_stop_reason") or payload.get("reason") or ""
        ).strip()
        if reason == "rate_limited":
            return "provider_rate_limited"
        if reason in {"transport_error", "provider_process_exited", "provider_pane_dead"}:
            return "provider_unavailable"
        if reason in {"context_limit", "provider_context_window_exhausted"} and bool(
            payload.get("recovery_exhausted")
        ):
            return "provider_context_exhausted"
    if (
        event.type == "worker.context.critical"
        and str(payload.get("reason") or "")
        == "provider_context_window_exhausted"
        and bool(payload.get("recovery_exhausted"))
    ):
        return "provider_context_exhausted"
    return ""


def execution_route_event_run_id(event: ZfEvent) -> str:
    """Return the immutable workflow run identity carried by source evidence."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    return str(
        payload.get("workflow_run_id") or event.correlation_id or ""
    ).strip()


def pending_execution_route_actions(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    events: list[ZfEvent],
) -> list[dict[str, Any]]:
    """Derive immediate switches only from unambiguous structured facts."""

    policy = execution_routing_policy(config)
    if policy is None or not bool(getattr(policy, "enabled", False)):
        return []
    state_dir = Path(state_dir)
    try:
        session = SessionStore(state_dir / "session.yaml").load()
    except ZfNotInitialized:
        return []
    tasks = TaskStore(state_dir / "kanban.json")
    from zf.runtime.execution_route_state import ExecutionRouteStore

    store = ExecutionRouteStore(state_dir)
    completed_checkpoints = {
        str((event.payload or {}).get("checkpoint_id") or "")
        for event in events
        if event.type in {
            "run.manager.action.applied",
            "run.manager.action.blocked",
            "run.manager.action.failed",
        }
        and isinstance(event.payload, dict)
    }
    latest_by_task: dict[str, ZfEvent] = {}
    for event in events:
        if event.type == "orchestrator.rework.triage.recorded":
            continue
        trigger_class = classify_execution_route_trigger(event)
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        if trigger_class and task_id:
            latest_by_task[task_id] = event
    actions: list[dict[str, Any]] = []
    for task_id, event in latest_by_task.items():
        task = tasks.get(task_id)
        if task is None or task.status in TERMINAL_STATES:
            continue
        workflow_run_id = str(
            task.execution_binding.workflow_run_id or session.session_id
        ).strip()
        if execution_route_event_run_id(event) != workflow_run_id:
            continue
        if store.task_record(
            task_id,
            workflow_run_id=workflow_run_id,
        ) is not None:
            continue
        assigned_to = str(task.assigned_to or "").strip()
        matching_roles = [
            role
            for role in list(getattr(config, "roles", []) or [])
            if assigned_to in {role.name, role.instance_id}
        ]
        if len(matching_roles) != 1:
            continue
        role = matching_roles[0]
        trigger_class = classify_execution_route_trigger(event)
        routes = matching_execution_routes(
            config,
            role=role,
            trigger_class=trigger_class,
            flow_kind=role.flow_kind,
        )
        if len(routes) != 1:
            continue
        route = routes[0]
        checkpoint_id = _stable_id(
            "execution-route", workflow_run_id, task_id, event.id, route.id
        )
        if checkpoint_id in completed_checkpoints:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        source_event_ids = list(dict.fromkeys([
            event.id,
            *[
                str(item)
                for item in payload.get("source_event_ids") or []
                if str(item).strip()
            ],
        ]))
        actions.append({
            "schema_version": "run-manager.pending-action.v1",
            "action": EXECUTION_ROUTE_SWITCH_ACTION,
            "safe_resume_action": EXECUTION_ROUTE_SAFE_ACTION,
            "checkpoint_id": checkpoint_id,
            "action_id": _stable_id("route-action", checkpoint_id),
            "workflow_run_id": workflow_run_id,
            "task_id": task_id,
            "role": role.name,
            "instance_id": role.instance_id,
            "role_instance": role.instance_id,
            "dispatch_id": str(task.active_dispatch_id or ""),
            "flow_kind": role.flow_kind,
            "route_id": route.id,
            "trigger_class": trigger_class,
            "fingerprint": str(
                payload.get("failure_fingerprint")
                or payload.get("fingerprint")
                or f"{trigger_class}:{event.id}"
            ),
            "failure_class": f"execution_route:{trigger_class}",
            "source_event_id": event.id,
            "source_event_type": event.type,
            "source_event_ids": source_event_ids,
            "policy_digest": execution_route_policy_digest(config),
            "owner_route": "controlled_action",
            "action_policy": "auto_decide",
            "intervention_class": "auto_recover",
            "expected_downstream_events": [
                EXECUTION_ROUTE_APPLIED_EVENT,
                "worker.respawn.requested",
                "task.assigned",
            ],
            "verify_condition": (
                "expected_downstream_event:"
                f"{EXECUTION_ROUTE_APPLIED_EVENT},worker.respawn.requested,task.assigned"
            ),
            "route_registry": "run-manager-router.v1",
        })
    return actions


def enrich_execution_route_action(
    action: dict[str, Any],
    *,
    state_dir: Path,
    config: ZfConfig | None,
) -> dict[str, Any]:
    action_name = str(action.get("action") or "")
    if (
        action_name == "resident-agent-reprompt"
        and str(action.get("semantic_triage_request_id") or "")
    ):
        return _enrich_resident_route_triage(
            action,
            state_dir=state_dir,
            config=config,
        )
    if action_name != EXECUTION_ROUTE_SWITCH_ACTION:
        return action
    enriched = dict(action)
    state_dir = Path(state_dir)
    session_run_id = ""
    try:
        session = SessionStore(state_dir / "session.yaml").load()
        session_run_id = session.session_id
    except ZfNotInitialized:
        pass
    task_id = str(enriched.get("task_id") or "")
    task = TaskStore(state_dir / "kanban.json").get(task_id) if task_id else None
    if task is not None:
        bound_run_id = str(task.execution_binding.workflow_run_id or "").strip()
        if bound_run_id:
            enriched["workflow_run_id"] = bound_run_id
        assigned = str(task.assigned_to or "")
        roles = [
            role
            for role in list(getattr(config, "roles", []) or [])
            if assigned in {role.name, role.instance_id}
        ]
        if len(roles) == 1:
            role = roles[0]
            enriched["role"] = role.name
            enriched["instance_id"] = role.instance_id
            enriched["role_instance"] = role.instance_id
            if not str(enriched.get("flow_kind") or "").strip():
                enriched["flow_kind"] = role.flow_kind
        if not str(enriched.get("dispatch_id") or "").strip():
            enriched["dispatch_id"] = str(task.active_dispatch_id or "")
    if not str(enriched.get("workflow_run_id") or "").strip() and session_run_id:
        enriched["workflow_run_id"] = session_run_id
    if not str(enriched.get("policy_digest") or "").strip():
        enriched["policy_digest"] = execution_route_policy_digest(config)
    return enriched


def _enrich_resident_route_triage(
    action: dict[str, Any],
    *,
    state_dir: Path,
    config: ZfConfig | None,
) -> dict[str, Any]:
    enriched = dict(action)
    task_id = str(enriched.get("task_id") or "").strip()
    task = TaskStore(Path(state_dir) / "kanban.json").get(task_id) if task_id else None
    assigned = str(task.assigned_to or "") if task is not None else ""
    roles = [
        role
        for role in list(getattr(config, "roles", []) or [])
        if assigned in {role.name, role.instance_id}
    ]
    if len(roles) != 1:
        return enriched
    role = roles[0]
    catalog = execution_route_catalog_for_role(config, role=role)
    if not catalog:
        return enriched
    enriched["execution_route_catalog"] = catalog
    enriched["recommended_actions"] = list(dict.fromkeys([
        *list(enriched.get("recommended_actions") or []),
        EXECUTION_ROUTE_SAFE_ACTION,
    ]))
    return enriched


def execution_route_action_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    required = {
        "checkpoint_id": "missing_checkpoint_id",
        "workflow_run_id": "missing_workflow_run_id",
        "task_id": "missing_task_id",
        "instance_id": "missing_instance_id",
        "route_id": "missing_route_id",
        "trigger_class": "missing_trigger_class",
        "source_event_id": "missing_source_event_id",
        "policy_digest": "missing_policy_digest",
    }
    for field, failure in required.items():
        if not str(payload.get(field) or "").strip():
            failures.append(failure)
    trigger_class = str(payload.get("trigger_class") or "").strip()
    if trigger_class and trigger_class not in EXECUTION_ROUTE_TRIGGER_CLASSES:
        failures.append("unsupported_trigger_class")
    source_event_id = str(payload.get("source_event_id") or "").strip()
    source_event_ids = {
        str(item)
        for item in payload.get("source_event_ids") or []
        if str(item).strip()
    }
    if source_event_id and source_event_id not in source_event_ids:
        failures.append("source_event_not_in_evidence_set")
    expected = [
        EXECUTION_ROUTE_APPLIED_EVENT,
        "worker.respawn.requested",
        "task.assigned",
    ]
    return {
        "schema_version": "run-manager.action-preflight.v1",
        "status": "blocked" if failures else "passed",
        "failures": failures,
        "warnings": [],
        "checkpoint_id": str(payload.get("checkpoint_id") or ""),
        "safe_resume_action": EXECUTION_ROUTE_SAFE_ACTION,
        "expected_downstream_events": expected,
        "verify_condition": "expected_downstream_event:" + ",".join(expected),
    }


def execution_route_router_decision(
    action: str,
    payload: dict[str, Any],
    *,
    decision_factory: Any,
) -> dict[str, Any] | None:
    if action != EXECUTION_ROUTE_SWITCH_ACTION:
        return None
    preflight = execution_route_action_preflight(payload)
    if preflight["status"] == "blocked":
        return decision_factory(
            "needs_diagnosis",
            executable=True,
            payload=payload,
            preflight=preflight,
            reason="execution route switch is missing immutable identity or evidence",
        )
    return decision_factory(
        "auto_decide",
        executable=True,
        payload=payload,
        preflight=preflight,
        reason="pre-approved task route switch is bounded and mechanically verified",
    )


def execution_route_preflight_for(
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    if action != EXECUTION_ROUTE_SWITCH_ACTION:
        return None
    return execution_route_action_preflight(payload)


def execution_route_expected_events(safe_action: str) -> set[str] | None:
    if safe_action != EXECUTION_ROUTE_SAFE_ACTION:
        return None
    return {
        EXECUTION_ROUTE_APPLIED_EVENT,
        "worker.respawn.requested",
        "task.assigned",
    }


def recorded_execution_route_action(
    recommendation: str,
    base: dict[str, Any],
    payload: dict[str, Any],
    capped: ZfEvent,
) -> dict[str, Any] | None:
    if recommendation != "switch_execution_route":
        return None
    capped_payload = capped.payload if isinstance(capped.payload, dict) else {}
    instance_id = str(
        payload.get("instance_id")
        or payload.get("role_instance")
        or capped_payload.get("instance_id")
        or capped_payload.get("role_instance")
        or capped_payload.get("role")
        or ""
    )
    trigger_class = str(payload.get("execution_route_trigger") or "").strip()
    return {
        **base,
        "action": EXECUTION_ROUTE_SWITCH_ACTION,
        "safe_resume_action": EXECUTION_ROUTE_SAFE_ACTION,
        "workflow_run_id": str(
            payload.get("workflow_run_id")
            or capped_payload.get("workflow_run_id")
            or capped.correlation_id
            or ""
        ),
        "instance_id": instance_id,
        "role_instance": instance_id,
        "dispatch_id": str(
            payload.get("dispatch_id") or capped_payload.get("dispatch_id") or ""
        ),
        "flow_kind": str(
            payload.get("flow_kind") or capped_payload.get("flow_kind") or ""
        ),
        "route_id": str(
            payload.get("execution_route_id") or payload.get("route_id") or ""
        ).strip(),
        "trigger_class": trigger_class,
        "policy_digest": str(payload.get("execution_route_policy_digest") or ""),
        "failure_class": f"execution_route:{trigger_class or 'unproven'}",
        "action_policy": "auto_decide",
        "intervention_class": "auto_recover",
        "expected_downstream_events": [
            EXECUTION_ROUTE_APPLIED_EVENT,
            "worker.respawn.requested",
            "task.assigned",
        ],
        "verify_condition": (
            "expected_downstream_event:"
            f"{EXECUTION_ROUTE_APPLIED_EVENT},worker.respawn.requested,task.assigned"
        ),
        "route_registry": "run-manager-router.v1",
    }


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return prefix + "-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "EXECUTION_ROUTE_APPLIED_EVENT",
    "EXECUTION_ROUTE_SAFE_ACTION",
    "EXECUTION_ROUTE_STATE_SCHEMA",
    "EXECUTION_ROUTE_SWITCH_ACTION",
    "EXECUTION_ROUTE_TRIGGER_CLASSES",
    "ExecutionRouteError",
    "ROUTE_SELECTION_RECEIPT_SCHEMA",
    "apply_execution_route",
    "classify_execution_route_trigger",
    "execution_route_expected_events",
    "execution_route_catalog_for_role",
    "execution_route_event_run_id",
    "execution_route_payload",
    "execution_route_action_preflight",
    "execution_route_preflight_for",
    "execution_route_policy_digest",
    "execution_route_router_decision",
    "execution_routing_policy",
    "matching_execution_routes",
    "enrich_execution_route_action",
    "pending_execution_route_actions",
    "recorded_execution_route_action",
    "resolve_execution_route",
]
