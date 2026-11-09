"""Proposal parsing and read-only capability policy for dynamic fragments."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from zf.core.config.schema import ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.execution_patterns import ExecutionPattern, resolve_execution_pattern


DYNAMIC_CONTINUATION_ACTION = "read-only-dynamic-continuation"
FRAGMENT_SCHEMA_VERSION = "operation-plan-fragment.v1"
CONTINUATION_ENVELOPE_SCHEMA_VERSION = "continuation-envelope.v1"


def canonical_fragment_digest(payload: Mapping[str, Any]) -> str:
    body = {
        str(key): value
        for key, value in payload.items()
        if str(key) not in {"fragment_digest", "created_at", "ts", "event_id"}
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def action_from_fragment_proposal(
    event: ZfEvent,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    nodes = payload.get("nodes")
    nodes = nodes if isinstance(nodes, list) else []
    node = nodes[0] if len(nodes) == 1 and isinstance(nodes[0], Mapping) else {}
    checkpoint = payload.get("trigger_checkpoint")
    checkpoint = checkpoint if isinstance(checkpoint, Mapping) else {}
    package = payload.get("current_plan_artifact_package")
    package = package if isinstance(package, Mapping) else {}
    fragment_id = str(payload.get("fragment_id") or "")
    continuation_key = str(payload.get("continuation_key") or fragment_id)
    workflow_run_id = str(
        payload.get("workflow_run_id")
        or event.correlation_id
        or ""
    )
    return {
        "action": DYNAMIC_CONTINUATION_ACTION,
        "safe_resume_action": "dispatch_read_only_fragment",
        "checkpoint_id": str(
            checkpoint.get("checkpoint_id")
            or checkpoint.get("ref")
            or payload.get("trigger_checkpoint_ref")
            or fragment_id
        ),
        "workflow_run_id": workflow_run_id,
        "run_id": str(payload.get("run_id") or workflow_run_id),
        "task_id": str(event.task_id or payload.get("task_id") or ""),
        "fragment_id": fragment_id,
        "fragment_digest": str(payload.get("fragment_digest") or ""),
        "fragment_schema_version": str(payload.get("schema_version") or ""),
        "fragment_mode": str(payload.get("mode") or ""),
        "fragment_node_count": len(nodes),
        "fragment_digest_valid": (
            str(payload.get("fragment_digest") or "")
            == canonical_fragment_digest(payload)
        ),
        "continuation_key": continuation_key,
        "parent_operation_id": str(payload.get("parent_operation_id") or ""),
        "pattern_id": str(node.get("pattern_id") or payload.get("pattern_id") or ""),
        "operation_type": str(node.get("operation_type") or ""),
        "task_map_generation": str(
            payload.get("task_map_generation")
            or payload.get("expected_generation")
            or ""
        ),
        "plan_artifact_package_id": str(
            package.get("package_id")
            or payload.get("current_plan_artifact_package_id")
            or payload.get("plan_artifact_package_id")
            or ""
        ),
        "plan_artifact_package_ref": str(
            package.get("ref")
            or payload.get("current_plan_artifact_package_ref")
            or payload.get("plan_artifact_package_ref")
            or ""
        ),
        "plan_artifact_package_digest": str(
            package.get("sha256")
            or package.get("digest")
            or payload.get("current_plan_artifact_package_digest")
            or payload.get("plan_artifact_package_digest")
            or ""
        ),
        "trigger_checkpoint_ref": str(
            checkpoint.get("ref")
            or payload.get("trigger_checkpoint_ref")
            or ""
        ),
        "trigger_checkpoint_digest": str(
            checkpoint.get("sha256")
            or checkpoint.get("digest")
            or payload.get("trigger_checkpoint_digest")
            or ""
        ),
        "budgets": dict(payload.get("budgets") or {})
        if isinstance(payload.get("budgets"), Mapping)
        else {},
        "expected_output": str(
            node.get("expected_output")
            or payload.get("expected_output")
            or ""
        ),
        "target_ref": str(node.get("target_ref") or payload.get("target_ref") or ""),
        "source_event_id": event.id,
        "source_event_type": event.type,
        "source_event_ids": [event.id],
        "failure_class": "controlled_dynamic_continuation",
        "owner_route": "run_manager",
        "action_policy": "auto_decide",
        "intervention_class": "auto_recover",
        "attempt_cap": 1,
        "expected_downstream_events": [
            "workflow.invoke.requested",
            "workflow.operation.started",
        ],
        "verify_condition": "expected_downstream_event:workflow.invoke.requested",
        "preflight": {
            "schema_version": "run-manager.action-preflight.v1",
            "status": "passed",
            "failures": [],
            "warnings": [],
        },
        "policy_decision": {
            "schema_version": "run-manager.action-policy.v1",
            "decision": "auto_decide",
            "executable": True,
            "reason": "registered read-only continuation uses reservation CAS",
        },
    }


def validate_read_only_fragment(
    config: ZfConfig,
    action: Mapping[str, Any],
) -> tuple[str, ExecutionPattern | None]:
    if str(action.get("fragment_schema_version") or "") != FRAGMENT_SCHEMA_VERSION:
        return "unsupported_fragment_schema", None
    if str(action.get("fragment_mode") or "") != "read_only":
        return "fragment_mode_must_be_read_only", None
    for key in (
        "workflow_run_id",
        "task_id",
        "fragment_id",
        "fragment_digest",
        "continuation_key",
        "parent_operation_id",
        "pattern_id",
        "task_map_generation",
        "plan_artifact_package_id",
        "plan_artifact_package_ref",
        "plan_artifact_package_digest",
        "trigger_checkpoint_ref",
        "trigger_checkpoint_digest",
    ):
        if not str(action.get(key) or ""):
            return f"missing_{key}", None
    if not bool(action.get("fragment_digest_valid")):
        return "fragment_digest_mismatch", None
    if int(action.get("fragment_node_count") or 0) != 1:
        return "d0_requires_exactly_one_registered_pattern", None
    pattern = resolve_execution_pattern(config, str(action["pattern_id"]))
    if pattern is None:
        return "execution_pattern_not_registered", None
    if pattern.kind != "fanout_reader":
        return "execution_pattern_is_not_read_only", None
    limits = action.get("budgets") or {}
    children = limits.get("max_children")
    depth = limits.get("max_depth", 1)
    if type(children) is not int or not 1 <= children <= 16:
        return "bounded_max_children_required", None
    if type(depth) is not int or depth != 1:
        return "d0_requires_depth_one", None
    child_count = pattern_child_count(config, pattern)
    if child_count == 0:
        return "execution_pattern_has_no_children", None
    if child_count > children:
        return "max_children_exceeded", None
    if pattern.barrier.get("mode") != "wait_for_all":
        return "required_children_must_join_all", None
    refs = set(pattern.roles) | {
        str(child.get("role_instance") or child.get("role") or "")
        for child in pattern.children
    }
    if pattern.barrier.get("synth_role"):
        refs.add(str(pattern.barrier["synth_role"]))
    for ref in refs:
        matches = [role for role in config.roles if ref in {role.name, role.instance_id}]
        if not matches or any(_role_kind(role) != "reader" for role in matches):
            return "execution_pattern_has_writer_capability", None
    operation_type = str(action.get("operation_type") or "")
    if operation_type and operation_type not in {
        "research",
        "scan",
        "critic",
        "test_discovery",
        "diagnosis",
        "synth",
        "read_only",
    }:
        return "operation_type_is_not_read_only", None
    return "", pattern


def pattern_child_count(config: ZfConfig, pattern: ExecutionPattern) -> int:
    return sum(
        sum(ref in {role.name, role.instance_id} for role in config.roles)
        for child in pattern.children
        if (ref := str(child.get("role_instance") or child.get("role") or ""))
    )


def _role_kind(role: Any) -> str:
    explicit = str(getattr(role, "role_kind", "") or "auto")
    if explicit != "auto":
        return explicit
    return (
        "reader"
        if str(getattr(role, "name", "") or "")
        in {"review", "test", "judge", "verify", "critic"}
        else "writer"
    )


__all__ = [
    "CONTINUATION_ENVELOPE_SCHEMA_VERSION",
    "DYNAMIC_CONTINUATION_ACTION",
    "FRAGMENT_SCHEMA_VERSION",
    "action_from_fragment_proposal",
    "canonical_fragment_digest",
    "validate_read_only_fragment",
]
