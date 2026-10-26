"""Typed OA aggregation checkpoints without transferring terminal authority."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator_agent_contracts import (
    OrchestratorAgentContractError,
)
from zf.runtime.orchestrator_agent_operations import (
    request_orchestrator_agent_checkpoint,
)
from zf.runtime.orchestrator_agent_policy import (
    checkpoint_policy,
    orchestration_flow_kind,
)
from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


STAGE_BARRIER_ADMITTED = "orchestrator.stage_barrier.admitted"
PRE_CLOSEOUT_ADMITTED = "orchestrator.pre_closeout.admitted"
_ADMISSION_EVENTS = {
    "stage_barrier": STAGE_BARRIER_ADMITTED,
    "pre_closeout": PRE_CLOSEOUT_ADMITTED,
}
_RESULT_EVENT_TYPES = frozenset({
    "workflow.call.result.admitted",
    "fanout.child.completed",
    "fanout.aggregate.completed",
    "lane.stage.completed",
    "verify.passed",
    "test.passed",
    "review.approved",
    "judge.passed",
    "goal.closure.synthesized",
    "artifact.delivery.verified",
})
_DESCRIPTOR_FIELDS = frozenset({
    "admitted_call_result_ref",
    "call_result_envelope_ref",
    "control_result_ref",
    "envelope_ref",
    "goal_closure_result_ref",
    "result_ref",
})
_DESCRIPTOR_LIST_FIELDS = frozenset({
    "artifact_refs",
    "evidence_refs",
    "input_result_refs",
    "result_refs",
})


@dataclass(frozen=True)
class AggregationCheckpointState:
    enabled: bool
    blocking: bool
    satisfied: bool
    operation_id: str = ""
    status: str = ""


def stage_barrier_checkpoint_state(
    runtime: Any,
    event: ZfEvent,
) -> AggregationCheckpointState:
    """Fence only explicit aggregate edges, never per-lane continuations."""

    if event.type != "fanout.aggregate.completed":
        return AggregationCheckpointState(False, False, False)
    return _checkpoint_state(runtime, checkpoint="stage_barrier", event=event)


def pre_closeout_checkpoint_state(
    runtime: Any,
    event: ZfEvent,
    *,
    events: Sequence[ZfEvent] | None = None,
) -> AggregationCheckpointState:
    """Fence completion-claim creation until OA aggregation is admitted."""

    return _checkpoint_state(
        runtime,
        checkpoint="pre_closeout",
        event=event,
        events=events,
    )


def validate_aggregation_admission(
    runtime: Any,
    *,
    aggregation_result: Mapping[str, Any],
    request: Mapping[str, Any],
) -> None:
    """Require exact, readable operation-scoped aggregation inputs."""

    expected = _descriptor_set(request.get("aggregation_input_refs"))
    provided = _descriptor_set(aggregation_result.get("input_result_refs"))
    if not expected:
        raise OrchestratorAgentContractError(
            "aggregation checkpoint has no canonical result inputs"
        )
    if provided != expected:
        raise OrchestratorAgentContractError(
            "aggregation input refs must exactly match the checkpoint inputs"
        )
    manifest = _request_source_manifest(runtime, request)
    allowed = _descriptor_set(manifest.get("sources"))
    for field in (
        "input_result_refs",
        "selected_result_refs",
        "rejected_result_refs",
    ):
        values = aggregation_result.get(field)
        for descriptor in values if isinstance(values, list) else []:
            if not isinstance(descriptor, Mapping):
                continue
            key = _descriptor_key(descriptor)
            if key not in allowed:
                raise OrchestratorAgentContractError(
                    f"aggregation {field} ref is outside the operation: {key[0]}"
                )
            hydrate_sidecar_ref(runtime.state_dir, dict(descriptor))


def compile_aggregation_admission(
    runtime: Any,
    *,
    event: ZfEvent,
    decision: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist the semantic recommendation and expose one restart-safe edge."""

    checkpoint = str(outcome.get("checkpoint") or "")
    event_type = _ADMISSION_EVENTS.get(checkpoint)
    result = decision.get("aggregation_result")
    if not event_type or not isinstance(result, Mapping):
        raise OrchestratorAgentContractError(
            "aggregation admission requires a supported checkpoint and result"
        )
    result_ref = write_immutable_json_sidecar(
        runtime.state_dir,
        dict(result),
        root="orchestrator-agent/aggregation-results",
        kind="orchestration_result",
        schema_version="orchestration-result.v1",
        created_by="orchestrator-agent-admission",
        source_event_id=event.id,
    )
    identity = decision.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    payload = {
        "schema_version": "orchestrator-aggregation-admission.v1",
        "workflow_run_id": str(identity.get("workflow_run_id") or ""),
        "operation_id": str(outcome.get("operation_id") or ""),
        "checkpoint": checkpoint,
        "source_event_id": str(outcome.get("source_event_id") or ""),
        "decision_event_id": event.id,
        "recommendation": str(result.get("recommendation") or ""),
        "unclosed_claim_ids": list(result.get("unclosed_claim_ids") or []),
        "task_map_generation": str(identity.get("task_map_generation") or ""),
        "plan_artifact_package_ref": str(
            identity.get("plan_artifact_package_ref") or ""
        ),
        "plan_artifact_package_digest": str(
            identity.get("plan_artifact_package_digest") or ""
        ),
        "orchestration_result_ref": result_ref,
    }
    admitted = runtime.event_writer.append(ZfEvent(
        type=event_type,
        actor="zf-cli",
        origin="kernel",
        payload=payload,
        causation_id=event.id,
        correlation_id=event.correlation_id or payload["workflow_run_id"],
    ))
    return {
        **dict(outcome),
        "aggregation_event_id": admitted.id,
        "orchestration_result_ref": result_ref,
        "redrive_source_event_id": payload["source_event_id"],
    }


def pause_for_semantic_stop(
    runtime: Any,
    *,
    event: ZfEvent,
    outcome: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply halt/escalate as a recoverable pause, never terminal truth."""

    workflow_run_id = str(outcome.get("workflow_run_id") or "")
    operation_id = str(outcome.get("operation_id") or "")
    existing = [
        item
        for item in runtime.event_log.read_all()
        if item.type == "run.paused"
        and isinstance(item.payload, Mapping)
        and str(item.payload.get("orchestrator_operation_id") or "")
        == operation_id
    ]
    if not existing:
        shared = {
            "schema_version": "orchestrator-semantic-stop.v1",
            "workflow_run_id": workflow_run_id,
            "run_id": workflow_run_id,
            "checkpoint": str(outcome.get("checkpoint") or ""),
            "reason": (
                "OA semantic checkpoint requested "
                + str(outcome.get("decision") or "escalate")
            ),
            "orchestrator_operation_id": operation_id,
            "orchestrator_decision_ref": dict(
                outcome.get("decision_ref") or {}
            ),
        }
        paused = runtime.event_writer.append(ZfEvent(
            type="run.paused",
            actor="zf-cli",
            origin="kernel",
            payload=shared,
            causation_id=event.id,
            correlation_id=event.correlation_id or workflow_run_id,
        ))
        runtime.event_writer.append(ZfEvent(
            type="human.escalate",
            actor="zf-cli",
            origin="kernel",
            payload={**shared, "pause_event_id": paused.id},
            causation_id=paused.id,
            correlation_id=event.correlation_id or workflow_run_id,
        ))
    return {**dict(outcome), "status": "paused", "applied": True}


def _checkpoint_state(
    runtime: Any,
    *,
    checkpoint: str,
    event: ZfEvent,
    events: Sequence[ZfEvent] | None = None,
) -> AggregationCheckpointState:
    policy = checkpoint_policy(
        runtime.config,
        checkpoint,
        flow_kind=orchestration_flow_kind(event),
    )
    if not policy:
        return AggregationCheckpointState(False, False, False)
    rows = list(events) if events is not None else list(runtime.event_log.read_all())
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    workflow_run_id = _workflow_run_id(rows, event)
    package = _current_package(rows, workflow_run_id)
    admitted = _current_admission(
        runtime,
        rows,
        checkpoint=checkpoint,
        workflow_run_id=workflow_run_id,
        source_event_id=event.id,
        package=package,
    )
    if admitted:
        return AggregationCheckpointState(
            enabled=True,
            blocking=policy == "blocking",
            satisfied=True,
            operation_id=str(admitted.get("operation_id") or ""),
            status="admitted:" + str(admitted.get("recommendation") or ""),
        )
    result_refs = _current_result_descriptors(
        rows,
        source_event=event,
        workflow_run_id=workflow_run_id,
        task_map_generation=str(package.get("task_map_generation") or ""),
    )
    missing = []
    if not workflow_run_id:
        missing.append("workflow_run_id")
    if not package:
        missing.append("current_plan_artifact_package")
    if not result_refs:
        missing.append("canonical_result_refs")
    if missing:
        _record_checkpoint_rejection(
            runtime,
            checkpoint=checkpoint,
            event=event,
            workflow_run_id=workflow_run_id,
            reason="missing:" + ",".join(missing),
        )
        return AggregationCheckpointState(
            enabled=True,
            blocking=policy == "blocking",
            satisfied=False,
            status="rejected",
        )
    goal_id = str(
        payload.get("goal_id")
        or payload.get("feature_id")
        or payload.get("pdd_id")
        or _latest_goal_id(rows, workflow_run_id)
        or ""
    )
    checkpoint_payload = {
        **dict(payload),
        "workflow_run_id": workflow_run_id,
        "goal_id": goal_id,
        "plan_revision": str(package.get("plan_revision") or ""),
        "task_map_generation": str(package.get("task_map_generation") or ""),
        "plan_artifact_package_id": str(package.get("package_id") or ""),
        "plan_artifact_package_ref": str(package.get("package_ref") or ""),
        "plan_artifact_package_digest": str(
            package.get("package_digest") or ""
        ),
        "run_contract_ref": str(package.get("run_contract_ref") or ""),
        "run_contract_digest": str(package.get("run_contract_digest") or ""),
        "result_refs": result_refs,
        "aggregation_input_refs": result_refs,
    }
    prepared = request_orchestrator_agent_checkpoint(
        runtime,
        checkpoint=checkpoint,
        checkpoint_policy=policy,
        workflow_run_id=workflow_run_id,
        source_event=event,
        payload=checkpoint_payload,
    )
    return AggregationCheckpointState(
        enabled=True,
        blocking=policy == "blocking",
        satisfied=False,
        operation_id=prepared.operation_id,
        status=prepared.status,
    )


def _current_admission(
    runtime: Any,
    events: Iterable[ZfEvent],
    *,
    checkpoint: str,
    workflow_run_id: str,
    source_event_id: str,
    package: Mapping[str, Any],
) -> dict[str, Any]:
    expected_type = _ADMISSION_EVENTS[checkpoint]
    for event in reversed(list(events)):
        if event.type != expected_type or not isinstance(event.payload, Mapping):
            continue
        payload = event.payload
        if (
            str(payload.get("workflow_run_id") or "") != workflow_run_id
            or str(payload.get("source_event_id") or "") != source_event_id
        ):
            continue
        if package and any((
            str(payload.get("task_map_generation") or "")
            != str(package.get("task_map_generation") or ""),
            str(payload.get("plan_artifact_package_ref") or "")
            != str(package.get("package_ref") or ""),
            str(payload.get("plan_artifact_package_digest") or "")
            != str(package.get("package_digest") or ""),
        )):
            continue
        descriptor = payload.get("orchestration_result_ref")
        if not isinstance(descriptor, Mapping):
            continue
        hydrate_sidecar_ref(runtime.state_dir, dict(descriptor))
        return dict(payload)
    return {}


def _current_package(
    events: Sequence[ZfEvent],
    workflow_run_id: str,
) -> dict[str, Any]:
    if not workflow_run_id:
        return {}
    current = reduce_plan_artifact_packages(
        events,
        workflow_run_id=workflow_run_id,
    ).get("current")
    return dict(current) if isinstance(current, Mapping) else {}


def _workflow_run_id(events: Sequence[ZfEvent], event: ZfEvent) -> str:
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    direct = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or payload.get("trace_id")
        or event.correlation_id
        or ""
    ).strip()
    if direct:
        return direct
    from zf.runtime.run_scope import resolve_run_for_event

    return resolve_run_for_event(events, event)


def _latest_goal_id(events: Sequence[ZfEvent], workflow_run_id: str) -> str:
    for event in reversed(events):
        if event.type != "run.goal.started":
            continue
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        run_id = str(payload.get("run_id") or event.correlation_id or "")
        if run_id != workflow_run_id:
            continue
        return str(payload.get("goal_id") or payload.get("pdd_id") or "")
    return ""


def _current_result_descriptors(
    events: Sequence[ZfEvent],
    *,
    source_event: ZfEvent,
    workflow_run_id: str,
    task_map_generation: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    from zf.runtime.orchestrator_agent_results import current_result_events

    candidates = current_result_events(
        events, source_event=source_event, workflow_run_id=workflow_run_id,
        task_map_generation=task_map_generation, result_event_types=_RESULT_EVENT_TYPES,
    )
    for event in candidates:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        selected.extend(_payload_result_descriptors(payload))
    return _dedupe_descriptors(selected)


def _payload_result_descriptors(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for key in _DESCRIPTOR_FIELDS:
        descriptor = payload.get(key)
        if isinstance(descriptor, Mapping):
            values.append(dict(descriptor))
    for key in _DESCRIPTOR_LIST_FIELDS:
        rows = payload.get(key)
        if isinstance(rows, list):
            values.extend(dict(row) for row in rows if isinstance(row, Mapping))
    for prefix in ("closure_fact", "goal_claim_set"):
        ref = str(payload.get(f"{prefix}_ref") or "")
        digest = str(payload.get(f"{prefix}_digest") or "")
        if ref and digest:
            values.append({"ref": ref, "sha256": digest, "kind": prefix})
    return [row for row in values if all(_descriptor_key(row))]


def _dedupe_descriptors(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        key = _descriptor_key(value)
        if not all(key) or key in seen:
            continue
        seen.add(key)
        rows.append(dict(value))
    return rows


def _descriptor_set(value: Any) -> set[tuple[str, str]]:
    return {
        _descriptor_key(row)
        for row in value if isinstance(row, Mapping)
    } if isinstance(value, list) else set()


def _descriptor_key(value: Mapping[str, Any]) -> tuple[str, str]:
    return str(value.get("ref") or ""), str(value.get("sha256") or "")


def _request_source_manifest(
    runtime: Any,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    descriptor = request.get("source_manifest_ref")
    if not isinstance(descriptor, Mapping):
        raise OrchestratorAgentContractError("source manifest ref is missing")
    payload = hydrate_sidecar_ref(runtime.state_dir, dict(descriptor)).payload
    if not isinstance(payload, Mapping):
        raise OrchestratorAgentContractError("source manifest is invalid")
    return dict(payload)


def _record_checkpoint_rejection(
    runtime: Any,
    *,
    checkpoint: str,
    event: ZfEvent,
    workflow_run_id: str,
    reason: str,
) -> None:
    if any(
        item.type == "orchestrator.semantic.checkpoint.rejected"
        and isinstance(item.payload, Mapping)
        and str(item.payload.get("checkpoint") or "") == checkpoint
        and str(item.payload.get("source_event_id") or "") == event.id
        for item in runtime.event_log.read_all()
    ):
        return
    runtime.event_writer.append(ZfEvent(
        type="orchestrator.semantic.checkpoint.rejected",
        actor="zf-cli",
        origin="kernel",
        payload={
            "checkpoint": checkpoint,
            "workflow_run_id": workflow_run_id,
            "source_event_id": event.id,
            "reason": reason,
        },
        causation_id=event.id,
        correlation_id=event.correlation_id or workflow_run_id,
    ))


__all__ = [
    "AggregationCheckpointState",
    "PRE_CLOSEOUT_ADMITTED",
    "STAGE_BARRIER_ADMITTED",
    "compile_aggregation_admission",
    "pause_for_semantic_stop",
    "pre_closeout_checkpoint_state",
    "stage_barrier_checkpoint_state",
    "validate_aggregation_admission",
]
