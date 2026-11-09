"""Pinned Flow role closure shared by Plan admission and submit validation."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from zf.runtime.flow_roles import FlowRoleBindingError, resolve_writer_owner


def writer_role_policy(config: Any) -> dict[str, Any]:
    return {
        "roles": [
            {key: str(getattr(role, key, "") or "") for key in (
                "name", "instance_id", "role_kind", "flow_kind",
            )}
            for role in getattr(config, "roles", ()) or ()
        ],
        "stages": [
            {"topology": str(getattr(stage, "topology", "") or ""),
             "roles": list(getattr(stage, "roles", ()) or ())}
            for stage in getattr(getattr(config, "workflow", None), "stages", ()) or ()
        ],
    }


def writer_role_errors(
    tasks: list[dict[str, Any]], *, flow_kind: str, policy: Mapping[str, Any],
) -> list[dict[str, str]]:
    # Older pinned operations predate this snapshot. Their admission remains
    # governed by the canonical writer resolver when materializing the plan.
    snapshot = policy.get("role_closure")
    if not isinstance(snapshot, Mapping):
        return []
    config = SimpleNamespace(
        roles=[SimpleNamespace(**row) for row in snapshot.get("roles", [])],
        workflow=SimpleNamespace(stages=[
            SimpleNamespace(**row) for row in snapshot.get("stages", [])
        ]),
    )
    errors = []
    for index, task in enumerate(tasks):
        try:
            resolve_writer_owner(
                config, flow_kind=flow_kind,
                owner_role=str(task.get("owner_role") or ""),
                owner_instance=str(task.get("owner_instance") or ""),
            )
        except FlowRoleBindingError as exc:
            errors.append({"code": exc.code,
                           "field": f"task_map.tasks[{index}].owner_instance",
                           "message": str(exc)})
    return errors
