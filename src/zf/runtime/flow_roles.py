"""Deterministic Flow-scoped role identity and startup selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.workflow.flow_metadata import normalize_flow_kind
from zf.runtime.orchestrator_agent_role import is_configured_orchestrator_role


class FlowRoleBindingError(ValueError):
    """A task or activation requested a role outside its confirmed Flow."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class FlowRoleBinding:
    owner_role: str
    owner_instance: str
    semantic_owner_role: str = ""


def configured_flow_kinds(config: Any) -> set[str]:
    return {
        normalize_flow_kind(getattr(role, "flow_kind", ""))
        for role in getattr(config, "roles", []) or []
        if normalize_flow_kind(getattr(role, "flow_kind", ""))
    }


def is_multi_flow_config(config: Any) -> bool:
    return len(configured_flow_kinds(config)) > 1


def initial_role_configs(config: Any) -> list[Any]:
    """Return roles that may be spawned before a Flow is confirmed."""

    roles = list(getattr(config, "roles", []) or [])
    if not is_multi_flow_config(config):
        return roles
    return [
        role for role in roles
        if not normalize_flow_kind(getattr(role, "flow_kind", ""))
    ]


def role_configs_for_flow(config: Any, flow_kind: str) -> list[Any]:
    """Return the exact configured role closure for one Flow."""

    kind = normalize_flow_kind(flow_kind)
    roles = list(getattr(config, "roles", []) or [])
    scoped = [
        role for role in roles
        if normalize_flow_kind(getattr(role, "flow_kind", "")) == kind
    ]
    if scoped:
        return scoped
    # Legacy and hand-written single-Flow configs predate role.flow_kind.
    if len(configured_flow_kinds(config)) <= 1:
        return [
            role for role in roles
            if not is_configured_orchestrator_role(config, role)
        ]
    return []


def writer_role_configs_for_flow(config: Any, flow_kind: str) -> list[Any]:
    closure = role_configs_for_flow(config, flow_kind)
    writers = [
        role for role in closure
        if str(getattr(role, "role_kind", "") or "") == "writer"
    ]
    if writers or is_multi_flow_config(config):
        return writers

    declared_writer_roles = {
        str(role_name or "").strip()
        for stage in getattr(
            getattr(config, "workflow", None),
            "stages",
            (),
        ) or ()
        if str(getattr(stage, "topology", "") or "").startswith("fanout_writer")
        for role_name in getattr(stage, "roles", ()) or ()
        if str(role_name or "").strip()
    }
    return [
        role for role in closure
        if {
            str(getattr(role, "name", "") or ""),
            str(getattr(role, "instance_id", "") or ""),
        } & declared_writer_roles
    ]


def resolve_writer_owner(
    config: Any,
    *,
    flow_kind: str,
    owner_role: str = "",
    owner_instance: str = "",
) -> FlowRoleBinding:
    """Normalize semantic/task-map owner input onto a Flow-local writer."""

    closure = writer_role_configs_for_flow(config, flow_kind)
    if not closure:
        raise FlowRoleBindingError(
            "flow_writer_binding_missing",
            f"Flow {normalize_flow_kind(flow_kind)!r} has no writer role",
        )
    all_roles = list(getattr(config, "roles", []) or [])
    raw_role = str(owner_role or "").strip()
    raw_instance = str(owner_instance or "").strip()

    def match(value: str, roles: list[Any]) -> Any | None:
        return next((
            role for role in roles
            if value in {
                str(getattr(role, "name", "") or ""),
                str(getattr(role, "instance_id", "") or ""),
            }
        ), None)

    if raw_instance:
        matched = next((
            role for role in closure
            if str(getattr(role, "instance_id", "") or "") == raw_instance
        ), None)
        if matched is None:
            code = (
                "flow_owner_cross_flow"
                if any(
                    str(getattr(role, "instance_id", "") or "")
                    == raw_instance
                    for role in all_roles
                )
                else "flow_owner_instance_unknown"
            )
            raise FlowRoleBindingError(
                code,
                f"owner_instance {raw_instance!r} is not a writer in "
                f"Flow {normalize_flow_kind(flow_kind)!r}",
            )
        if raw_role:
            matched_identity = {
                str(getattr(matched, "name", "") or ""),
                str(getattr(matched, "instance_id", "") or ""),
            }
            role_match = match(raw_role, closure)
            if raw_role not in matched_identity and role_match is not None:
                raise FlowRoleBindingError(
                    "flow_owner_identity_mismatch",
                    f"owner_role {raw_role!r} and owner_instance "
                    f"{raw_instance!r} resolve to different writers",
                )
            if (
                raw_role not in matched_identity
                and role_match is None
                and match(raw_role, all_roles) is not None
            ):
                raise FlowRoleBindingError(
                    "flow_owner_cross_flow",
                    f"owner_role {raw_role!r} is outside "
                    f"Flow {normalize_flow_kind(flow_kind)!r}",
                )
        return FlowRoleBinding(
            owner_role=str(getattr(matched, "name", "") or ""),
            owner_instance=str(getattr(matched, "instance_id", "") or ""),
            semantic_owner_role=(
                raw_role
                if raw_role and raw_role not in matched_identity
                else ""
            ),
        )

    if raw_role:
        matched = match(raw_role, closure)
        if matched is not None:
            return FlowRoleBinding(
                owner_role=str(getattr(matched, "name", "") or ""),
                owner_instance=str(getattr(matched, "instance_id", "") or ""),
            )
        if match(raw_role, all_roles) is not None:
            raise FlowRoleBindingError(
                "flow_owner_cross_flow",
                f"owner_role {raw_role!r} is outside "
                f"Flow {normalize_flow_kind(flow_kind)!r}",
            )

    default = closure[0]
    return FlowRoleBinding(
        owner_role=str(getattr(default, "name", "") or ""),
        owner_instance=str(getattr(default, "instance_id", "") or ""),
        semantic_owner_role=raw_role,
    )


__all__ = [
    "FlowRoleBinding",
    "FlowRoleBindingError",
    "configured_flow_kinds",
    "initial_role_configs",
    "is_multi_flow_config",
    "resolve_writer_owner",
    "role_configs_for_flow",
    "writer_role_configs_for_flow",
]
