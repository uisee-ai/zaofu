"""Bind route proposals to effective execution policy without exposing config bodies."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.workflow.flow_metadata import flow_metadata_for


EXECUTION_POLICY_FIELDS = (
    "project", "workflow", "roles", "constraints", "quality_gates", "verification",
    "safety", "security", "orchestrator", "goal", "providers", "autopilot",
    "cost", "skill_sources", "global_budget_usd",
    "budget_enforcement_enabled", "budget_fail_closed",
)
EXECUTION_RUNTIME_FIELDS = ("workdirs", "git", "skills", "run_manager", "execution_routing")


def effective_execution_policy_digest(config: Any) -> str:
    """Hash execution inputs; omit credentials, UI and observability configuration.

    This is deliberately conservative across routes: a shared role, pipeline,
    workdir policy or gate change requires a fresh proposal. Only the digest is
    projected, never the config body.
    """
    body = {name: _primitive(getattr(config, name, None)) for name in EXECUTION_POLICY_FIELDS}
    runtime = getattr(config, "runtime", None)
    body["runtime"] = {
        name: _primitive(getattr(runtime, name, None))
        for name in EXECUTION_RUNTIME_FIELDS
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def route_execution_policy(
    config: Any,
    route: Mapping[str, Any],
    *,
    digest: str,
) -> dict[str, Any]:
    metadata = (
        flow_metadata_for(config, str(route.get("kind") or ""))
        if route.get("family") == "delivery" else {}
    )
    pipeline = metadata.get("task_pipeline") or {}
    profile = str(metadata.get("controller_profile") or "")
    mode = str(pipeline.get("mode") or "")
    role_ids = set(route.get("roles") or [])
    profiles = {
        str(role.instance_id or role.name): {
            "default_profile": role.execution.default_profile,
            "profile_allowlist": list(role.execution.profile_allowlist),
        }
        for role in getattr(config, "roles", []) or []
        if getattr(role, "execution", None) is not None
        and {getattr(role, "name", ""), getattr(role, "instance_id", "")} & role_ids
    }
    return {
        "schema_version": "workflow-route-policy.v1",
        "digest": digest,
        "controller_profile": profile,
        "task_pipeline_mode": mode,
        "task_pipeline_profile_digest": str(pipeline.get("profile_digest") or ""),
        "rollout": "explicit_canary" if pipeline else str(route.get("rollout") or "configured"),
        "role_execution_profiles": profiles,
    }


def route_flow_topology(config: Any, route: Mapping[str, Any]) -> str:
    if route.get("family") != "delivery":
        return str(route.get("topology") or "")
    metadata = flow_metadata_for(config, str(route.get("kind") or ""))
    declared = str(metadata.get("topology") or "")
    if declared:
        return declared
    if route.get("start_adapter") == "light_delivery_request_submit":
        return "light"
    if route.get("family") == "delivery":
        return "fanout"
    return str(route.get("topology") or "")


def _primitive(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_primitive(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__"):
        return {key: _primitive(item) for key, item in vars(value).items() if not key.startswith("_")}
    raise TypeError(f"unsupported execution policy value: {type(value).__name__}")
