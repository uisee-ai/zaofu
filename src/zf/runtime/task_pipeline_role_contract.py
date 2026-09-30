"""Shared lifecycle invariant for Task Pipeline preflight and dispatch."""

from typing import Any


def task_pipeline_role_lifecycle_error(role: Any) -> str:
    if str(getattr(role.lifecycle, "mode", "") or "") != "on_demand":
        return f"Task Pipeline role {role.instance_id} must be on_demand"
    return ""


def configured_task_pipeline_role_errors(config: Any) -> list[str]:
    from zf.runtime.task_pipeline_reconciler import task_pipeline_policies

    instances = {
        str(instance)
        for policy in task_pipeline_policies(config) if policy.get("mode") == "blocking"
        for pool in (policy.get("pools") or {}).values()
        for instance in pool.get("role_instances", [])
    }
    return [error for role in getattr(config, "roles", []) or []
            if getattr(role, "instance_id", "") in instances
            if (error := task_pipeline_role_lifecycle_error(role))]
