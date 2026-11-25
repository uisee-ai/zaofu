"""Freeze optional provider-delivery evidence without changing old operations."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.core.workflow.flow_metadata import flow_metadata_for
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def delivery_mode(value: Any) -> str:
    mode = "produced" if value is None else value
    if mode not in ("produced", "observed"):
        raise ValueError("input delivery mode must be produced or observed")
    return mode


def operation_delivery_mode(runtime: Any, operation_id: str, payload: dict) -> str:
    from zf.runtime.workflow_operation import load_workflow_operation

    previous = load_workflow_operation(runtime.event_log, operation_id)
    if previous:
        request = hydrate_sidecar_ref(runtime.state_dir, previous["request_ref"]).payload["request"]
        # A transport retry must retain its first frozen requirement, even when
        # current configuration now opts new operations into stricter delivery.
        return delivery_mode(request.get("input_delivery_mode"))
    protocol = flow_metadata_for(runtime.config, payload=payload).get("result_protocol") or {}
    return delivery_mode(protocol.get("input_delivery_mode"))


def current_task_input_delivery(events: list, operation: Mapping[str, Any]) -> dict:
    operation_id = operation.get("operation_id")
    attempt_id = operation.get("active_attempt_id")
    dispatch_id = operation.get("dispatch_id")
    if not operation_id or not attempt_id:
        return {}
    return next((event.payload for event in reversed(events)
                 if event.type == "task.pipeline.stage.dispatched" and event.origin == "kernel"
                 and isinstance(event.payload, dict)
                 and event.payload.get("operation_id") == operation_id
                 and event.payload.get("attempt_id") == attempt_id
                 and (not dispatch_id or event.payload.get("dispatch_id") == dispatch_id)), {})


def submitted_input_policy(state_dir: Path, carrier: Mapping[str, Any]) -> dict:
    descriptor = carrier.get("input_consumption_policy_ref")
    if not isinstance(descriptor, Mapping) or not str(descriptor.get("ref") or ""):
        return {}
    policy = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    return dict(policy) if isinstance(policy, Mapping) else {}


def pinned_delivery_policy(state_dir: Path, events: list, operation: Mapping[str, Any], fallback: dict | None) -> dict:
    """Resolve the observed contract; a None fallback also resolves produced reads from their carrier."""
    from zf.runtime.workflow_operation import reduce_workflow_operations

    # Runtime terminal adapters carry a reduced operation, not its trusted
    # request ref. Always recover the frozen contract from the canonical log.
    canonical = reduce_workflow_operations(events).get(operation.get("operation_id"), {})
    if canonical:
        if operation.get("request_hash") and operation["request_hash"] != canonical.get("request_hash"):
            raise ValueError("input delivery operation request hash mismatch")
        operation = canonical
    descriptor = operation.get("request_ref")
    if not descriptor:
        return fallback or {}
    request = hydrate_sidecar_ref(state_dir, descriptor).payload["request"]
    observed = delivery_mode(request.get("input_delivery_mode")) == "observed"
    if not observed and fallback is not None:
        return fallback
    carrier = current_task_input_delivery(events, operation) if request.get("task_pipeline_stage") else request
    if not observed:
        # The validation caller resolves produced reads from the same frozen
        # request or current task dispatch as the independent submit gate.
        policy = submitted_input_policy(state_dir, carrier)
        if policy and (policy.get("attempt_id") != operation.get("active_attempt_id")
                       or policy.get("workflow_run_id") != operation.get("workflow_run_id")):
            raise ValueError("input consumption policy does not match the active attempt/run")
        return policy
    if request.get("task_pipeline_stage") and carrier.get("dispatch_id") != operation.get("dispatch_id"):
        raise ValueError("input delivery requires the exact kernel dispatch")
    policy = hydrate_sidecar_ref(state_dir, carrier["input_consumption_policy_ref"]).payload
    binding = policy.get("delivery_evidence") or {}
    if (binding.get("mode") != "observed"
            or binding.get("operation_id") != operation.get("operation_id")
            or binding.get("dispatch_id") != operation.get("dispatch_id")
            or policy.get("attempt_id") != operation.get("active_attempt_id")
            or policy.get("workflow_run_id") != operation.get("workflow_run_id")
            or binding.get("consumer_actor") != operation.get("role_instance")):
        raise ValueError("input delivery policy does not match the frozen operation/dispatch")
    return dict(policy)


def admission_delivery_policy(state_dir: Path, events: list, operation: Mapping[str, Any],
                                fallback: dict) -> tuple[dict, list[dict[str, str]]]:
    try:
        return pinned_delivery_policy(state_dir, events, operation, fallback), []
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return fallback, [{"field": "input_consumption.delivery",
                           "code": "input_delivery_contract_unavailable", "message": str(exc)}]


def legacy_input_policy(state_dir: Path, payload: Mapping[str, Any],
                        explicit: Mapping[str, Any] | None) -> dict[str, Any]:
    if explicit:
        return dict(explicit)
    raw = payload.get("input_consumption_policy")
    if isinstance(raw, Mapping):
        return dict(raw)
    descriptor = payload.get("input_consumption_policy_ref")
    if isinstance(descriptor, Mapping):
        hydrated = hydrate_sidecar_ref(state_dir, dict(descriptor))
        return dict(hydrated.payload) if isinstance(hydrated.payload, dict) else {}
    return {}
